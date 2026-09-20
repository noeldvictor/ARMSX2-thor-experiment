#!/usr/bin/env python3
"""Fit RAISR-style upscaling kernels from HD texture packs, and evaluate them.

RAISR (Romano, Isidoro, Milanfar 2016) is not a neural network: every input pixel is
hashed by the local gradient's angle, strength and coherence into one of a few hundred
buckets, and each bucket owns one small kernel per output phase, fit by least squares.
The kernels are what ship; at runtime the cost is one hash and one k*k convolution per
output pixel, an order of magnitude under the fork's CPU upscaler ceiling.

The HD packs give the target side of the pairs for free. The input side is the PS2's
native texture, which only a running game can emit (a dump). Until dumps exist, the
`extract` step synthesises it: box-downscale the pack texture back to native size, then
quantise the way the PSM in the filename says the native was stored (5551 for CT16,
256 colours for PSMT8, 16 for PSMT4). Real dumps drop into the same `lr/` folder later
and the fit is rerun unchanged.

Usage:
    python tools/raisr_train.py extract PACK.zip|PACK.tar.zst OUT_DIR [--astcenc PATH/astcenc-avx2.exe]
    python tools/raisr_train.py train  DATASET_DIR... -o world_x2.a2rk --scale 2
    python tools/raisr_train.py eval   world_x2.a2rk DATASET_DIR... --out report_dir

A dataset dir holds `hr/<name>.png` (pack texture), `lr/<name>.png` (native-size input)
and `meta.tsv` (name, native w/h, PSM, pack scale). Names are the PCSX2 replacement
names, so `lr/` can be filled from a real `textures/<serial>/dumps` folder by filename.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import os
import re
import struct
import subprocess
import sys
import tarfile
import time

import numpy as np
from PIL import Image

# PCSX2 replacement filename: TEX0Hash[-CLUTHash][-rWxH]-bits, bits = PSM:6 TW:4 TH:4 ...
NAME_RE = re.compile(r"^([0-9a-f]{1,16})(?:-([0-9a-f]{1,16}))?(?:-r(\d+)x(\d+))?-([0-9a-f]{8})$")
MIP_RE = re.compile(r"-mip\d+$")

# GSLocalMemory PSM numbers.
PSM_CT32, PSM_CT24, PSM_CT16, PSM_CT16S = 0, 1, 2, 10
PSM_T8, PSM_T4, PSM_T8H, PSM_T4HL, PSM_T4HH = 19, 20, 27, 36, 44
PSM_PAL256 = {PSM_T8, PSM_T8H}
PSM_PAL16 = {PSM_T4, PSM_T4HL, PSM_T4HH}
PSM_16BIT = {PSM_CT16, PSM_CT16S}

MAGIC = b"A2RK"
VERSION = 1


def parse_name(stem: str):
    """Return (native_w, native_h, psm) from a replacement/dump filename stem, or None."""
    if MIP_RE.search(stem):
        return None
    m = NAME_RE.match(stem)
    if not m:
        return None
    bits = int(m.group(5), 16)
    psm = bits & 0x3F
    tw = (bits >> 6) & 0xF
    th = (bits >> 10) & 0xF
    w = int(m.group(3)) if m.group(3) else (1 << tw)
    h = int(m.group(4)) if m.group(4) else (1 << th)
    return w, h, psm


# ---------------------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------------------

def _decode_astc(astcenc: str, data: bytes, tmp_path: str) -> Image.Image | None:
    src = tmp_path + ".astc"
    dst = tmp_path + ".png"
    with open(src, "wb") as f:
        f.write(data)
    try:
        r = subprocess.run([astcenc, "-dl", src, dst], capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(dst):
            return None
        img = Image.open(dst)
        img.load()
        return img.convert("RGBA")
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass


def box_down(img: np.ndarray, factor: int) -> np.ndarray:
    """Area-average downscale of an HxWxC float array by an integer factor."""
    h, w, c = img.shape
    h2, w2 = h // factor, w // factor
    img = img[: h2 * factor, : w2 * factor]
    return img.reshape(h2, factor, w2, factor, c).mean(axis=(1, 3))


def degrade(lr: np.ndarray, psm: int) -> np.ndarray:
    """Quantise a float RGBA [0,255] native-size image the way its PSM stored it."""
    out = np.clip(np.rint(lr), 0, 255).astype(np.uint8)
    if psm in PSM_16BIT:
        # RGBA5551: 5 bits per colour channel, alpha to 1 bit.
        rgb = (out[..., :3] >> 3) << 3
        rgb = rgb | (rgb >> 5)  # replicate high bits, as the GS expands 5-bit to 8
        a = np.where(out[..., 3:4] >= 128, 255, 0).astype(np.uint8)
        return np.concatenate([rgb, a], axis=-1)
    if psm in PSM_PAL256 or psm in PSM_PAL16:
        colours = 256 if psm in PSM_PAL256 else 16
        pil = Image.fromarray(out, "RGBA")
        # Median-cut palette on RGB; alpha is carried through separately (a real CLUT has
        # per-entry alpha, but the RGB error is what matters to the kernels).
        q = pil.convert("RGB").quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
        rgb = np.asarray(q.convert("RGB"), dtype=np.uint8)
        return np.concatenate([rgb, out[..., 3:4]], axis=-1)
    return out  # CT32 / CT24: stored at 8 bits


def cmd_extract(args) -> int:
    os.makedirs(os.path.join(args.out, "hr"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "lr"), exist_ok=True)
    tmp_dir = os.path.join(args.out, "_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    entries = []
    skipped = {}
    t0 = time.time()

    def wanted(name: str):
        base = os.path.basename(name)
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        if ext not in (".astc", ".png", ".dds"):
            return None
        if ext == ".astc" and not args.astcenc:
            skipped["astc (no --astcenc)"] = skipped.get("astc (no --astcenc)", 0) + 1
            return None
        meta = parse_name(stem)
        if meta is None:
            skipped["unparsed name"] = skipped.get("unparsed name", 0) + 1
            return None
        return stem, ext, meta

    def drain(pending):
        return _process_batch(args, pending, tmp_dir)

    pending = []
    if args.pack.lower().endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(args.pack) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                w = wanted(info.filename)
                if w is None:
                    continue
                pending.append((*w, z.read(info)))
                if len(pending) >= args.batch:
                    entries += drain(pending)
                    pending = []
    else:
        import zstandard  # only needed for the B2 tar+zstd packs
        with open(args.pack, "rb") as fh:
            dctx = zstandard.ZstdDecompressor()
            with dctx.stream_reader(fh) as reader, tarfile.open(fileobj=reader, mode="r|") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    w = wanted(member.name)
                    if w is None:
                        continue
                    pending.append((*w, tar.extractfile(member).read()))
                    if len(pending) >= args.batch:
                        entries += drain(pending)
                        pending = []
    if pending:
        entries += drain(pending)
    if skipped:
        print("skipped:", ", ".join(f"{v} {k}" for k, v in skipped.items()))

    with open(os.path.join(args.out, "meta.tsv"), "w", encoding="utf-8") as f:
        f.write("name\tnative_w\tnative_h\tpsm\tpack_scale\n")
        for name, w, h, psm, scale in entries:
            f.write(f"{name}\t{w}\t{h}\t{psm}\t{scale}\n")
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass
    print(f"{len(entries)} textures extracted to {args.out} in {time.time() - t0:.0f}s")
    return 0


def _process_one(args, item, tmp_dir):
    stem, ext, (nw, nh, psm), data = item
    try:
        if ext == ".astc":
            img = _decode_astc(args.astcenc, data, os.path.join(tmp_dir, stem))
        else:
            # Pillow reads PNG and BC1/BC3 (DXT1/DXT5) DDS; BC7 DDS raises and is skipped.
            img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    if img is None:
        return None
    hw, hh = img.size
    # The pack texture must be an integer multiple of the native; anything else is a
    # region/mip oddity the runtime would not upscale either. A zero side comes from a
    # region name like "-r0x64" (one axis unbounded), which is the same oddity.
    if nw <= 0 or nh <= 0 or hw % nw or hh % nh or hw // nw != hh // nh:
        return None
    scale = hw // nw
    if scale < 2 or scale > 8:
        return None
    if nw < 16 or nh < 16 or nw > args.max_native or nh > args.max_native:
        return None
    hr = np.asarray(img, dtype=np.float32)
    lr = degrade(box_down(hr, scale), psm)
    img.save(os.path.join(args.out, "hr", stem + ".png"))
    Image.fromarray(lr, "RGBA").save(os.path.join(args.out, "lr", stem + ".png"))
    return (stem, nw, nh, psm, scale)


def _process_batch(args, pending, tmp_dir):
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(lambda it: _process_one(args, it, tmp_dir), pending))
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------------------

def luma(rgba: np.ndarray) -> np.ndarray:
    return rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114


def gaussian_kernel(size: int, sigma: float) -> np.ndarray:
    r = size // 2
    x = np.arange(-r, r + 1, dtype=np.float32)
    g = np.exp(-(x * x) / (2 * sigma * sigma))
    g /= g.sum()
    return np.outer(g, g).astype(np.float32)


def _window_sum(img: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted sum over a size x size window, replicate-padded. Separable would be faster;
    the window is 7x7 and this runs once per texture, so clarity wins."""
    size = weights.shape[0]
    r = size // 2
    pad = np.pad(img, r, mode="edge")
    out = np.zeros_like(img, dtype=np.float32)
    h, w = img.shape
    for dy in range(size):
        for dx in range(size):
            out += weights[dy, dx] * pad[dy : dy + h, dx : dx + w]
    return out


class Hasher:
    """Gradient-structure hashing shared by training and inference."""

    def __init__(self, qa: int, qs: int, qc: int, window: int, sigma: float,
                 s_thresh: np.ndarray | None = None, c_thresh: np.ndarray | None = None):
        self.qa, self.qs, self.qc = qa, qs, qc
        self.window, self.sigma = window, sigma
        self.weights = gaussian_kernel(window, sigma)
        self.s_thresh = s_thresh
        self.c_thresh = c_thresh

    def features(self, y: np.ndarray):
        """Per-pixel (angle in [0, pi), strength, coherence) from a luma image."""
        pad = np.pad(y, 1, mode="edge")
        gx = (pad[1:-1, 2:] - pad[1:-1, :-2]) * 0.5
        gy = (pad[2:, 1:-1] - pad[:-2, 1:-1]) * 0.5
        gxx = _window_sum(gx * gx, self.weights)
        gyy = _window_sum(gy * gy, self.weights)
        gxy = _window_sum(gx * gy, self.weights)
        tr = gxx + gyy
        det = gxx * gyy - gxy * gxy
        disc = np.sqrt(np.maximum(tr * tr * 0.25 - det, 0.0))
        l1 = tr * 0.5 + disc
        l2 = np.maximum(tr * 0.5 - disc, 0.0)
        angle = np.mod(0.5 * np.arctan2(2.0 * gxy, gxx - gyy), np.pi)
        s1, s2 = np.sqrt(l1), np.sqrt(l2)
        strength = s1
        coherence = (s1 - s2) / (s1 + s2 + 1e-6)
        return angle, strength, coherence

    def buckets(self, y: np.ndarray) -> np.ndarray:
        angle, strength, coherence = self.features(y)
        a = np.minimum((angle / np.pi * self.qa).astype(np.int32), self.qa - 1)
        s = np.searchsorted(self.s_thresh, strength).astype(np.int32)
        c = np.searchsorted(self.c_thresh, coherence).astype(np.int32)
        return (a * self.qs + s) * self.qc + c

    @property
    def count(self) -> int:
        return self.qa * self.qs * self.qc


def patches(img: np.ndarray, k: int, idx_y: np.ndarray, idx_x: np.ndarray) -> np.ndarray:
    """k*k neighbourhoods (replicate-padded) around the given pixel coordinates.
    img is HxW (one channel). Returns N x k*k, row-major within the patch."""
    r = k // 2
    pad = np.pad(img, r, mode="edge")
    cols = []
    for dy in range(k):
        for dx in range(k):
            cols.append(pad[idx_y + dy, idx_x + dx])
    return np.stack(cols, axis=1).astype(np.float32)


DIHEDRAL = [
    lambda a: a,
    lambda a: np.rot90(a, 1),
    lambda a: np.rot90(a, 2),
    lambda a: np.rot90(a, 3),
    lambda a: a[::-1],
    lambda a: np.rot90(a[::-1], 1),
    lambda a: np.rot90(a[::-1], 2),
    lambda a: np.rot90(a[::-1], 3),
]


# ---------------------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------------------

def load_meta(dataset: str):
    rows = []
    with open(os.path.join(dataset, "meta.tsv"), encoding="utf-8") as f:
        next(f)
        for line in f:
            name, w, h, psm, scale = line.rstrip("\n").split("\t")
            rows.append((name, int(w), int(h), int(psm), int(scale)))
    return rows


def load_pair(dataset: str, name: str, pack_scale: int, scale: int):
    """Return (lr RGBA float, target RGBA float at `scale` x native), or None."""
    if pack_scale % scale:
        return None
    lr = np.asarray(Image.open(os.path.join(dataset, "lr", name + ".png")).convert("RGBA"), dtype=np.float32)
    hr = np.asarray(Image.open(os.path.join(dataset, "hr", name + ".png")).convert("RGBA"), dtype=np.float32)
    if pack_scale != scale:
        hr = box_down(hr, pack_scale // scale)
    if hr.shape[0] != lr.shape[0] * scale or hr.shape[1] != lr.shape[1] * scale:
        return None
    return lr, hr


def split_names(rows, holdout: float, seed: int = 1234):
    rng = np.random.default_rng(seed)
    names = sorted(r[0] for r in rows)
    mask = rng.random(len(names)) < holdout
    held = {n for n, m in zip(names, mask) if m}
    return [r for r in rows if r[0] not in held], [r for r in rows if r[0] in held]


# ---------------------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------------------

def sample_thresholds(hasher: Hasher, items, scale: int, n_textures: int, rng) -> tuple[np.ndarray, np.ndarray]:
    """Tertile thresholds for strength and coherence, so each of the 3 bins is equally
    populated on this data rather than on photographs."""
    strengths, coherences = [], []
    pick = rng.choice(len(items), size=min(n_textures, len(items)), replace=False)
    for i in pick:
        dataset, (name, _, _, _, pack_scale) = items[i]
        pair = load_pair(dataset, name, pack_scale, scale)
        if pair is None:
            continue
        lr, _ = pair
        _, s, c = hasher.features(luma(lr))
        alive = lr[..., 3] > 0
        strengths.append(s[alive].ravel())
        coherences.append(c[alive].ravel())
    s = np.concatenate(strengths)
    c = np.concatenate(coherences)
    qs = np.quantile(s, np.linspace(0, 1, hasher.qs + 1)[1:-1]).astype(np.float32)
    qc = np.quantile(c, np.linspace(0, 1, hasher.qc + 1)[1:-1]).astype(np.float32)
    return qs, qc


def cmd_train(args) -> int:
    rng = np.random.default_rng(args.seed)
    scale, k = args.scale, args.kernel
    kk = k * k
    hasher = Hasher(args.angles, args.strengths, args.coherences, args.window, args.sigma)

    items = []
    for ds in args.datasets:
        rows = load_meta(ds)
        train_rows, _ = split_names(rows, args.holdout)
        items += [(ds, r) for r in train_rows if r[4] % scale == 0]
    if not items:
        sys.exit("no usable textures")
    print(f"{len(items)} training textures, scale x{scale}, {k}x{k} kernels, "
          f"{hasher.count} buckets x {scale * scale} phases")

    t0 = time.time()
    hasher.s_thresh, hasher.c_thresh = sample_thresholds(hasher, items, scale, args.threshold_textures, rng)
    print(f"strength thresholds {hasher.s_thresh}, coherence thresholds {hasher.c_thresh}")

    nb = hasher.count
    phases = scale * scale
    Q = np.zeros((nb, kk, kk), dtype=np.float64)
    V = np.zeros((nb, phases, kk), dtype=np.float64)
    counts = np.zeros(nb, dtype=np.int64)

    for n, (dataset, (name, _, _, _, pack_scale)) in enumerate(items):
        pair = load_pair(dataset, name, pack_scale, scale)
        if pair is None:
            continue
        lr0, hr0 = pair
        for t in (DIHEDRAL if args.augment else DIHEDRAL[:1]):
            lr, hr = t(lr0), t(hr0)
            h, w = lr.shape[:2]
            b = hasher.buckets(luma(lr))
            alive = lr[..., 3] > 0
            ys, xs = np.nonzero(alive)
            if ys.size == 0:
                continue
            if ys.size > args.pixels_per_texture:
                pick = rng.choice(ys.size, size=args.pixels_per_texture, replace=False)
                ys, xs = ys[pick], xs[pick]
            bk = b[ys, xs]
            order = np.argsort(bk, kind="stable")
            ys, xs, bk = ys[order], xs[order], bk[order]
            # Channels are separate samples sharing the luma bucket, so one kernel serves
            # R, G, B (and alpha at runtime) - what makes it cheap and keeps colour edges aligned.
            P = [patches(lr[..., ch], k, ys, xs) for ch in range(3)]
            T = []
            for ph in range(phases):
                dy, dx = divmod(ph, scale)
                T.append([hr[ys * scale + dy, xs * scale + dx, ch] for ch in range(3)])
            starts = np.flatnonzero(np.r_[True, bk[1:] != bk[:-1]])
            ends = np.r_[starts[1:], bk.size]
            for s0, e0 in zip(starts, ends):
                bi = bk[s0]
                counts[bi] += (e0 - s0) * 3
                for ch in range(3):
                    p = P[ch][s0:e0]
                    Q[bi] += p.T @ p
                    for ph in range(phases):
                        V[bi, ph] += p.T @ T[ph][ch][s0:e0]
        if (n + 1) % 50 == 0 or n + 1 == len(items):
            print(f"  {n + 1}/{len(items)} textures, {time.time() - t0:.0f}s")

    # Solve. Ridge toward the pooled (all-bucket) kernel, so a thin bucket degrades to
    # "the average good kernel" instead of to noise.
    Qg = Q.sum(axis=0)
    Vg = V.sum(axis=0)
    lam_g = args.ridge * np.trace(Qg) / kk
    Kg = np.linalg.solve(Qg + lam_g * np.eye(kk), Vg.T).T  # phases x kk
    kernels = np.zeros((phases, nb, kk), dtype=np.float32)
    for bi in range(nb):
        lam = args.ridge * max(np.trace(Q[bi]), 1e-6) / kk + 1e-3 * lam_g
        A = Q[bi] + lam * np.eye(kk)
        B = V[bi].T + lam * Kg.T
        kernels[:, bi, :] = np.linalg.solve(A, B).T
    empty = int((counts == 0).sum())
    print(f"solved in {time.time() - t0:.0f}s; {empty}/{nb} buckets had no samples "
          f"(they carry the pooled kernel); median samples per bucket {int(np.median(counts))}")

    write_a2rk(args.out, scale, k, hasher, kernels)
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump({
            "datasets": args.datasets, "textures": len(items), "scale": scale, "kernel": k,
            "angles": args.angles, "strengths": args.strengths, "coherences": args.coherences,
            "window": args.window, "sigma": args.sigma, "ridge": args.ridge,
            "augment": args.augment, "pixels_per_texture": args.pixels_per_texture,
            "empty_buckets": empty, "seconds": round(time.time() - t0),
        }, f, indent=1)
    print(f"wrote {args.out} ({os.path.getsize(args.out) / 1024:.0f} KB)")
    return 0


# ---------------------------------------------------------------------------------------
# file format
# ---------------------------------------------------------------------------------------

def write_a2rk(path: str, scale: int, k: int, hasher: Hasher, kernels: np.ndarray) -> None:
    """Little-endian. Header, then f32 kernels [phase][bucket][k*k]; bucket = (a*Qs+s)*Qc+c,
    phase = dy*scale+dx, patch row-major with the centre at (k//2, k//2)."""
    with open(path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<IIIIIIIf", VERSION, scale, k, hasher.qa, hasher.qs, hasher.qc,
                            hasher.window, hasher.sigma))
        f.write(np.asarray(hasher.s_thresh, dtype="<f4").tobytes())
        f.write(np.asarray(hasher.c_thresh, dtype="<f4").tobytes())
        f.write(np.ascontiguousarray(kernels, dtype="<f4").tobytes())


def read_a2rk(path: str):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != MAGIC:
        sys.exit(f"{path}: not an A2RK file")
    version, scale, k, qa, qs, qc, window, sigma = struct.unpack_from("<IIIIIIIf", data, 4)
    if version != VERSION:
        sys.exit(f"{path}: version {version} unsupported")
    off = 4 + struct.calcsize("<IIIIIIIf")
    s_thresh = np.frombuffer(data, dtype="<f4", count=qs - 1, offset=off); off += 4 * (qs - 1)
    c_thresh = np.frombuffer(data, dtype="<f4", count=qc - 1, offset=off); off += 4 * (qc - 1)
    nb = qa * qs * qc
    kernels = np.frombuffer(data, dtype="<f4", count=scale * scale * nb * k * k, offset=off)
    kernels = kernels.reshape(scale * scale, nb, k * k)
    hasher = Hasher(qa, qs, qc, window, sigma, s_thresh.copy(), c_thresh.copy())
    return scale, k, hasher, kernels


# ---------------------------------------------------------------------------------------
# inference (numpy reference for evaluation; the C++ runtime mirrors this)
# ---------------------------------------------------------------------------------------

def raisr_upscale(lr: np.ndarray, scale: int, k: int, hasher: Hasher, kernels: np.ndarray) -> np.ndarray:
    h, w, c = lr.shape
    b = hasher.buckets(luma(lr)).ravel()
    ys, xs = np.divmod(np.arange(h * w), w)
    out = np.zeros((h * scale, w * scale, c), dtype=np.float32)
    for ch in range(c):
        P = patches(lr[..., ch], k, ys, xs)  # (h*w) x kk
        for ph in range(scale * scale):
            dy, dx = divmod(ph, scale)
            K = kernels[ph][b]  # (h*w) x kk
            out[ys * scale + dy, xs * scale + dx, ch] = np.einsum("ij,ij->i", P, K)
    return np.clip(out, 0, 255)


def pil_upscale(lr: np.ndarray, scale: int, method) -> np.ndarray:
    """Per-channel resample. Pillow resamples RGBA through premultiplied alpha, which bleeds
    the garbage colour under transparent pixels into their neighbours; the emulator's filters
    treat each channel independently, so the baseline must too or it loses for the wrong reason."""
    h, w = lr.shape[:2]
    src = np.clip(np.rint(lr), 0, 255).astype(np.uint8)
    out = np.zeros((h * scale, w * scale, src.shape[2]), dtype=np.float32)
    for ch in range(src.shape[2]):
        plane = Image.fromarray(src[..., ch], "L").resize((w * scale, h * scale), method)
        out[..., ch] = np.asarray(plane, dtype=np.float32)
    return out


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = np.mean((a - b) ** 2)
    return 99.0 if mse < 1e-9 else 10 * np.log10(255.0 ** 2 / mse)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM over RGB with an 11x11 Gaussian window (Wang et al. constants)."""
    g = gaussian_kernel(11, 1.5)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    vals = []
    for ch in range(3):
        x, y = a[..., ch], b[..., ch]
        mx, my = _window_sum(x, g), _window_sum(y, g)
        sxx = _window_sum(x * x, g) - mx * mx
        syy = _window_sum(y * y, g) - my * my
        sxy = _window_sum(x * y, g) - mx * my
        s = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
        vals.append(s.mean())
    return float(np.mean(vals))


def cmd_eval(args) -> int:
    scale, k, hasher, kernels = read_a2rk(args.model)
    items = []
    for ds in args.datasets:
        rows = load_meta(ds)
        _, held = split_names(rows, args.holdout)
        items += [(ds, r) for r in held if r[4] % scale == 0]
    rng = np.random.default_rng(args.seed)
    if len(items) > args.max_textures:
        items = [items[i] for i in rng.choice(len(items), size=args.max_textures, replace=False)]
    os.makedirs(args.out, exist_ok=True)

    methods = {
        "nearest": lambda lr: pil_upscale(lr, scale, Image.Resampling.NEAREST),
        "bilinear": lambda lr: pil_upscale(lr, scale, Image.Resampling.BILINEAR),
        "bicubic": lambda lr: pil_upscale(lr, scale, Image.Resampling.BICUBIC),
        "lanczos": lambda lr: pil_upscale(lr, scale, Image.Resampling.LANCZOS),
        "raisr": lambda lr: raisr_upscale(lr, scale, k, hasher, kernels),
    }
    totals = {m: {"psnr": [], "ssim": [], "ms": []} for m in methods}
    rows_out = []
    saved = 0
    for dataset, (name, nw, nh, psm, pack_scale) in items:
        pair = load_pair(dataset, name, pack_scale, scale)
        if pair is None:
            continue
        lr, hr = pair
        alive = hr[..., 3] > 0
        if alive.mean() < 0.2:
            continue  # mostly transparent; metrics would be about garbage pixels
        results = {}
        for m, fn in methods.items():
            t0 = time.perf_counter()
            up = fn(lr)
            dt = (time.perf_counter() - t0) * 1000
            # Metrics on visible RGB only.
            a = np.where(alive[..., None], up[..., :3], 0)
            b = np.where(alive[..., None], hr[..., :3], 0)
            p, s = psnr(a, b), ssim(a, b)
            totals[m]["psnr"].append(p)
            totals[m]["ssim"].append(s)
            totals[m]["ms"].append(dt)
            results[m] = (up, p, s)
        rows_out.append((name, nw, nh, psm, {m: (results[m][1], results[m][2]) for m in methods}))
        if saved < args.samples:
            _save_comparison(os.path.join(args.out, f"{saved:02d}_{name[:16]}.png"), lr, hr, results, scale)
            saved += 1

    lines = [f"{'method':10} {'PSNR':>7} {'SSIM':>7} {'ms/tex':>8}   ({len(rows_out)} held-out textures, x{scale})"]
    for m in methods:
        t = totals[m]
        lines.append(f"{m:10} {np.mean(t['psnr']):7.2f} {np.mean(t['ssim']):7.4f} {np.mean(t['ms']):8.1f}")
    wins = sum(1 for r in rows_out if r[4]["raisr"][0] > max(r[4][m][0] for m in ("bicubic", "lanczos")))
    lines.append(f"raisr beats the better of bicubic/lanczos on PSNR for {wins}/{len(rows_out)} textures")
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(args.out, "report.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n\nname\tnative\tpsm\t" + "\t".join(f"{m}_psnr\t{m}_ssim" for m in methods) + "\n")
        for name, nw, nh, psm, res in rows_out:
            f.write(f"{name}\t{nw}x{nh}\t{psm}\t" + "\t".join(f"{res[m][0]:.2f}\t{res[m][1]:.4f}" for m in methods) + "\n")
    return 0


def _save_comparison(path: str, lr, hr, results, scale: int) -> None:
    """Side by side: nearest | bicubic | lanczos | raisr | target, on a crop where it matters."""
    h, w = hr.shape[:2]
    crop = min(256, h, w)
    # Pick the crop with the most gradient energy in the target, so the comparison is not of flat fill.
    y = luma(hr)
    gy, gx = np.gradient(y)
    energy = gx * gx + gy * gy
    best, by, bx = -1.0, 0, 0
    step = max(1, crop // 4)
    for cy in range(0, h - crop + 1, step):
        for cx in range(0, w - crop + 1, step):
            e = energy[cy : cy + crop, cx : cx + crop].sum()
            if e > best:
                best, by, bx = e, cy, cx
    panels = []
    for m in ("nearest", "bicubic", "lanczos", "raisr"):
        panels.append(results[m][0][by : by + crop, bx : bx + crop, :3])
    panels.append(hr[by : by + crop, bx : bx + crop, :3])
    sheet = np.concatenate(panels, axis=1)
    Image.fromarray(np.clip(np.rint(sheet), 0, 255).astype(np.uint8), "RGB").save(path)


# ---------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="unpack a tar+zstd pack into hr/ + synthetic lr/")
    ex.add_argument("pack")
    ex.add_argument("out")
    ex.add_argument("--astcenc", help="path to astcenc executable (needed for .astc packs)")
    ex.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ex.add_argument("--batch", type=int, default=64)
    ex.add_argument("--max-native", type=int, default=512, help="skip textures larger than this on a side")
    ex.set_defaults(func=cmd_extract)

    tr = sub.add_parser("train", help="fit kernels from one or more dataset dirs")
    tr.add_argument("datasets", nargs="+")
    tr.add_argument("-o", "--out", required=True)
    tr.add_argument("--scale", type=int, default=2, choices=(2, 4))
    tr.add_argument("--kernel", type=int, default=9, help="kernel side, odd")
    tr.add_argument("--angles", type=int, default=24)
    tr.add_argument("--strengths", type=int, default=3)
    tr.add_argument("--coherences", type=int, default=3)
    tr.add_argument("--window", type=int, default=7, help="structure tensor window side, odd")
    tr.add_argument("--sigma", type=float, default=1.5)
    tr.add_argument("--ridge", type=float, default=1e-4, help="relative ridge on each bucket's normal equations")
    tr.add_argument("--pixels-per-texture", type=int, default=6000)
    tr.add_argument("--threshold-textures", type=int, default=60)
    tr.add_argument("--holdout", type=float, default=0.1, help="fraction of textures kept out for eval")
    tr.add_argument("--no-augment", dest="augment", action="store_false", help="skip the 8 dihedral transforms")
    tr.add_argument("--seed", type=int, default=1)
    tr.set_defaults(func=cmd_train)

    ev = sub.add_parser("eval", help="compare a kernel file against bicubic/lanczos on held-out textures")
    ev.add_argument("model")
    ev.add_argument("datasets", nargs="+")
    ev.add_argument("--out", required=True)
    ev.add_argument("--holdout", type=float, default=0.1)
    ev.add_argument("--max-textures", type=int, default=200)
    ev.add_argument("--samples", type=int, default=12, help="comparison sheets to write")
    ev.add_argument("--seed", type=int, default=1)
    ev.set_defaults(func=cmd_eval)

    args = ap.parse_args()
    if args.cmd == "train" and (args.kernel % 2 == 0 or args.window % 2 == 0):
        sys.exit("kernel and window must be odd")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
