"""Upscale the textures extract_native.py wrote with an ESRGAN-family model, on a desktop GPU.

    python upscale.py IN_DIR OUT_DIR --model PATH [--scale 4|2] [--tile N] [--limit N]
                      [--only a.png,b.png] [--force]

Reads every `*.png` in IN_DIR (RGBA, alpha already on the 0..255 scale) and writes the upscaled
texture to OUT_DIR under the same name. `manifest.json`, when IN_DIR has one, is copied through
with an `upscale` record (scale, model, model SHA-256). Files already in OUT_DIR are skipped
unless `--force`, so an interrupted run resumes where it stopped - unless the input changed since
(an extractor fix): `upscale-inputs.json` in OUT_DIR records each input's SHA-1, and an output
whose input no longer matches, or whose size is not the input's times the scale, is redone.

Requirements (not in the repo; install into a venv):

    pip install torch --index-url https://download.pytorch.org/whl/cu130   # CUDA build
    pip install spandrel numpy pillow

spandrel loads the model whatever its architecture (ESRGAN, SPAN, DAT, ...). The default model
is 4x-UltraSharp (Kim2091, ESRGAN, CC BY-NC-SA 4.0,
https://huggingface.co/Kim2091/UltraSharp). Models stay outside the repo; the licence is
non-commercial, which is why nothing made with it ships in the APK.

Decisions (worked out 2026-09-22 on Okage: Shadow King, 2,807 textures):

- Seams. Floors, walls and grass repeat, and an upscaler run on the bare image invents a border
  that shows as a seam every time the texture repeats. So every texture is padded before
  inference by `--pad` native pixels (default 16) and the scaled margin is cropped off after:
  fully opaque textures with WRAP (the opposite edge, as the GS sees it with REPEAT
  addressing), textures with transparency with EDGE replication (cutouts, sprites and fonts are
  clamped, and wrapping would pull the other side's pixels into them). Measured on 60 opaque
  textures that tile natively, the step across the wrap boundary exceeds the step between
  neighbouring pixels by a median of 4.1/255 with no margin, 0.12 with 8, 0.02 with 16 (i.e.
  gone), and 32 is no better than 16. Whether an opaque texture tiles cannot be read from the
  pixels reliably (the edge-continuity ratio over the set is a smooth spread, not two groups),
  and a wrong WRAP on a non-tiling texture costs at most a pixel of its border, while a wrong
  EDGE on a floor is a seam across the room - so every opaque texture wraps.
- Minimum size. The margin also brings every input up to MIN_SIZE (4x4 textures exist), plus
  whatever the model's own size requirements ask for.
- Transparent pixels. Where alpha is 0 the PS2 does not care what the colour is, and the palette
  usually has black there; the model bleeds it into the cutout as a thick dark outline. Before
  inference those pixels take the colour of their nearest visible neighbours (repeated
  8-neighbour averaging outward from the visible area).
- Alpha. Run through the same model as a grey image (`--alpha model`, default), or resized with
  bicubic (`--alpha resize`). Compared side by side, the model's alpha follows the drawn shape:
  fern leaves and eye outlines come out as clean curves and the menu font stays sharp, where
  bicubic keeps the pixel staircase on cutouts and blurs soft-alpha text. A source alpha that is
  binary (only 0 and 255: 225 of the 381 textures with transparency) is re-thresholded at 128
  after scaling, so a cutout stays a cutout instead of growing a grey fringe the game never had.
- 2x. `--scale 2` with a 4x model runs the model at 4x and downscales with Lanczos (in float,
  per channel, before the margin is cropped so tiling textures stay seamless). UltraSharp has
  no 2x version, and the downscale averages away much of the grain the model adds at 4x.
- Batching. The model's per-call cost dominates for small textures (Tales of Destiny's 59k sprite
  frames, mostly 32x32, ran at ~4 textures/s one by one). Textures up to BATCH_MAX a side,
  margin included, are laid out side by side on BATCH_CANVAS canvases and upscaled together; each
  keeps its own seam margin, so its neighbours sit beyond the margin. `--no-batch` turns it off.
  The model's cost is per pixel, so the margin matters as much: textures with transparency
  (sprites, cutouts - never tiled) get `--pad-transparent` (8), opaque ones keep 16 for seams.
- Precision. fp16 on CUDA when the model supports it, with an fp32 retry if the output has any
  NaN or infinity. cuDNN autotuning is off and deterministic algorithms are requested; a rerun
  writes byte-identical files.
- Tiling. Inputs larger than `--tile` (native pixels, default 512, 0 = never) run in tiles with
  TILE_OVERLAP pixels of context on each side, keeping only each tile's centre. 24 Okage textures
  (the 640x448 screens and wide strips) are tiled; on a 640x448 screen the tiled result is within
  2/255 of an untiled run. The whole of the largest one fits in 12 GB at fp16 too.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

# Must be set before torch initialises cuBLAS, for deterministic output.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

MIN_SIZE = 32  # smallest padded input, native pixels
TILE_OVERLAP = 16  # context kept around each tile, native pixels
NEIGHBOURS = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]


def fill_transparent(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Give every alpha-0 pixel the average colour of its nearest visible pixels."""
    known = alpha > 0
    if known.all() or not known.any():
        return rgb
    out = rgb.astype(np.float32)
    out[~known] = 0
    h, w = known.shape
    while not known.all():
        pv = np.pad(out, ((1, 1), (1, 1), (0, 0)))
        pk = np.pad(known, 1).astype(np.float32)
        total = np.zeros_like(out)
        count = np.zeros(known.shape, np.float32)
        for dy, dx in NEIGHBOURS:
            total += pv[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
            count += pk[1 + dy : 1 + dy + h, 1 + dx : 1 + dx + w]
        grow = ~known & (count > 0)
        out[grow] = total[grow] / count[grow, None]
        known = known | grow
    return out


class Upscaler:
    def __init__(self, model_path: Path, tile: int, fp16: bool = True):
        import torch
        from spandrel import ImageModelDescriptor, ModelLoader

        self.torch = torch
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.use_deterministic_algorithms(True, warn_only=True)

        desc = ModelLoader().load_from_file(str(model_path))
        if not isinstance(desc, ImageModelDescriptor):
            raise SystemExit(f"{model_path} is not a single-image model")
        if desc.input_channels != 3 or desc.output_channels != 3:
            raise SystemExit(f"{model_path}: expected an RGB model, got {desc.input_channels} channels")
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.fp32 = desc.to(self.device).eval()
        self.fp16 = None
        if fp16 and self.device.type == "cuda" and desc.supports_half:
            self.fp16 = copy.deepcopy(self.fp32).half()
        self.scale = desc.scale
        self.req = desc.size_requirements
        self.arch = desc.architecture.name
        self.tile = tile
        self.fp16_retries = 0

    def _forward(self, img: np.ndarray) -> np.ndarray:
        """img: H x W x 3 float32 in 0..1. Returns (H*scale) x (W*scale) x 3 float32."""
        torch = self.torch
        h, w, _ = img.shape
        # Meet the model's own size rules by extending bottom/right, then crop them off.
        mult = max(self.req.multiple_of, 1)
        need_h = max(h, self.req.minimum)
        need_w = max(w, self.req.minimum)
        if self.req.square:
            need_h = need_w = max(need_h, need_w)
        need_h = -(-need_h // mult) * mult
        need_w = -(-need_w // mult) * mult
        if (need_h, need_w) != (h, w):
            img = np.pad(img, ((0, need_h - h), (0, need_w - w), (0, 0)), mode="edge")
        x = torch.from_numpy(np.ascontiguousarray(img.transpose(2, 0, 1), np.float32))[None].to(self.device)
        with torch.inference_mode():
            y = None
            if self.fp16 is not None:
                y = self.fp16(x.half()).float()
                if not torch.isfinite(y).all():
                    self.fp16_retries += 1
                    y = None
            if y is None:
                y = self.fp32(x)
            y = y.clamp_(0, 1)[0].permute(1, 2, 0).cpu().numpy()
        s = self.scale
        return y[: h * s, : w * s]

    def run(self, img: np.ndarray) -> np.ndarray:
        """Upscale by the model's scale, in tiles when the image is larger than --tile."""
        h, w, _ = img.shape
        t, ov, s = self.tile, TILE_OVERLAP, self.scale
        if t <= 0 or (h <= t and w <= t):
            return self._forward(img)
        out = np.empty((h * s, w * s, 3), np.float32)
        for y in range(0, h, t):
            for x in range(0, w, t):
                y0, y1 = max(y - ov, 0), min(y + t + ov, h)
                x0, x1 = max(x - ov, 0), min(x + t + ov, w)
                res = self._forward(img[y0:y1, x0:x1])
                ch, cw = min(t, h - y), min(t, w - x)
                cy, cx = y - y0, x - x0
                out[y * s : (y + ch) * s, x * s : (x + cw) * s] = \
                    res[cy * s : (cy + ch) * s, cx * s : (cx + cw) * s]
        return out


def resize_float(chan: np.ndarray, size: tuple[int, int], resample) -> np.ndarray:
    """Resize one float channel (H x W) to size = (width, height) without quantising."""
    return np.asarray(Image.fromarray(chan.astype(np.float32), "F").resize(size, resample))


def prepare(rgba: np.ndarray, pad: int, pad_transparent: int | None = None) -> dict:
    """The model's input for one texture: colour and alpha with the seam margin, 0..1 floats."""
    h, w, _ = rgba.shape
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3]
    opaque = bool(alpha.min() == 255)
    if not opaque and pad_transparent is not None:
        pad = pad_transparent  # cutouts and sprites never tile: the margin is only context
    binary_alpha = not opaque and bool(np.isin(alpha, (0, 255)).all())
    if not opaque:
        rgb = fill_transparent(rgb, alpha)
    # Margin per axis: --pad, or more to reach MIN_SIZE.
    my = max(pad, -(-(MIN_SIZE - h) // 2))
    mx = max(pad, -(-(MIN_SIZE - w) // 2))
    widths = ((my, my), (mx, mx))
    mode = "wrap" if opaque else "edge"
    prgb = np.pad(rgb, widths + ((0, 0),), mode=mode) / 255.0
    pa = None if opaque else np.pad(alpha.astype(np.float32), widths, mode=mode) / 255.0
    return dict(h=h, w=w, my=my, mx=mx, rgb=prgb, alpha=pa, binary=binary_alpha)


def finish(prep: dict, out_rgb: np.ndarray, out_a: np.ndarray | None, model_scale: int, scale: int) -> np.ndarray:
    """RGBA 0..255 output from the model's output for one prepared texture (margin included)."""
    h, w, my, mx = prep["h"], prep["w"], prep["my"], prep["mx"]
    ph, pw = prep["rgb"].shape[:2]
    if scale != model_scale:  # e.g. 4x model, 2x output: Lanczos down, still with the margin attached
        size = (pw * scale, ph * scale)
        out_rgb = np.stack([resize_float(out_rgb[..., c], size, Image.LANCZOS) for c in range(3)], axis=2)
        if out_a is not None:
            out_a = resize_float(out_a, size, Image.LANCZOS)
    cy, cx = my * scale, mx * scale
    out_rgb = out_rgb[cy : cy + h * scale, cx : cx + w * scale]
    result = np.empty((h * scale, w * scale, 4), np.uint8)
    result[..., :3] = np.rint(np.clip(out_rgb, 0, 1) * 255)
    if out_a is None:
        result[..., 3] = 255
    else:
        out_a = np.clip(out_a[cy : cy + h * scale, cx : cx + w * scale], 0, 1)
        result[..., 3] = np.where(out_a >= 0.5, 255, 0) if prep["binary"] else np.rint(out_a * 255)
    return result


def upscale_alpha(pa: np.ndarray, up: Upscaler, alpha_mode: str) -> np.ndarray:
    s = up.scale
    if alpha_mode == "model":
        return up.run(np.repeat(pa[..., None], 3, axis=2)).mean(axis=2)
    ph, pw = pa.shape
    return np.clip(resize_float(pa, (pw * s, ph * s), Image.BICUBIC), 0, 1)


def upscale_texture(rgba: np.ndarray, up: Upscaler, scale: int, pad: int, alpha_mode: str) -> np.ndarray:
    prep = prepare(rgba, pad)
    out_rgb = up.run(prep["rgb"])  # (ph*s, pw*s, 3), 0..1
    out_a = None if prep["alpha"] is None else upscale_alpha(prep["alpha"], up, alpha_mode)
    return finish(prep, out_rgb, out_a, up.scale, scale)


BATCH_CANVAS = 512  # native pixels a side: one model call per canvas of small textures
BATCH_MAX = 224  # a prepared texture (margin included) up to this size a side is batched


def upscale_batch(preps: list[dict], up: Upscaler, scale: int, alpha_mode: str) -> list[np.ndarray]:
    """Small textures laid out side by side on one canvas, each with its own seam margin, so the
    model runs once for all of them (and once more for their alpha) instead of once each - the
    per-call cost dominates for sprites. Rows of the tallest item; items never touch the canvas
    edge's padding. Returns each texture's RGBA like upscale_texture()."""
    places = []
    x = y = row_h = 0
    for p in preps:
        ph, pw = p["rgb"].shape[:2]
        if x + pw > BATCH_CANVAS:
            x, y, row_h = 0, y + row_h, 0
        places.append((x, y))
        x += pw
        row_h = max(row_h, ph)
    height = y + row_h
    rgb = np.zeros((height, BATCH_CANVAS, 3), np.float32)
    alpha = np.ones((height, BATCH_CANVAS), np.float32)
    any_alpha = False
    for p, (px, py) in zip(preps, places):
        ph, pw = p["rgb"].shape[:2]
        rgb[py : py + ph, px : px + pw] = p["rgb"]
        if p["alpha"] is not None:
            alpha[py : py + ph, px : px + pw] = p["alpha"]
            any_alpha = True
    s = up.scale
    out_rgb = up.run(rgb)
    out_a = upscale_alpha(alpha, up, alpha_mode) if any_alpha else None
    results = []
    for p, (px, py) in zip(preps, places):
        ph, pw = p["rgb"].shape[:2]
        o_rgb = out_rgb[py * s : (py + ph) * s, px * s : (px + pw) * s]
        o_a = None if p["alpha"] is None else out_a[py * s : (py + ph) * s, px * s : (px + pw) * s]
        results.append(finish(p, o_rgb, o_a, s, scale))
    return results


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def up_to_date(src: Path, dst: Path, scale: int, recorded: str | None, digest: str) -> bool:
    """An existing output is kept only if it was made from this input (when that was recorded)
    and is the input's size times the scale."""
    if recorded is not None and recorded != digest:
        return False
    try:
        with Image.open(src) as a, Image.open(dst) as b:
            return b.size == (a.width * scale, a.height * scale)
    except OSError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inp", type=Path, metavar="IN_DIR")
    ap.add_argument("out", type=Path, metavar="OUT_DIR")
    ap.add_argument("--model", type=Path, required=True, help="model file (.pth / .safetensors)")
    ap.add_argument("--scale", type=int, default=4, choices=(2, 4), help="output scale (default 4)")
    ap.add_argument("--tile", type=int, default=512, help="tile size in native pixels, 0 = never tile")
    ap.add_argument("--pad", type=int, default=16, help="seam margin in native pixels (default 16)")
    ap.add_argument("--pad-transparent", type=int, default=8,
                    help="margin for textures with transparency, which never tile (default 8)")
    ap.add_argument("--alpha", choices=("model", "resize"), default="model", help="how alpha is upscaled")
    ap.add_argument("--limit", type=int, default=0, help="process at most N files")
    ap.add_argument("--only", default="", help="comma-separated file names to process")
    ap.add_argument("--force", action="store_true", help="overwrite files already in OUT_DIR")
    ap.add_argument("--fp32", action="store_true", help="never use fp16")
    ap.add_argument("--no-batch", dest="batch", action="store_false",
                    help="run the model once per texture (slower for small ones; see upscale_batch)")
    a = ap.parse_args()

    files = sorted(p for p in a.inp.glob("*.png"))
    if a.only:
        wanted = {n.strip() for n in a.only.split(",") if n.strip()}
        files = [p for p in files if p.name in wanted or p.stem in wanted]
    if a.limit:
        files = files[: a.limit]
    a.out.mkdir(parents=True, exist_ok=True)

    up = Upscaler(a.model, a.tile, fp16=not a.fp32)
    if a.scale > up.scale:
        raise SystemExit(f"--scale {a.scale} needs a model of at least that scale ({a.model.name} is {up.scale}x)")
    print(f"{a.model.name}: {up.arch} {up.scale}x on {up.device}"
          f"{' fp16' if up.fp16 is not None else ' fp32'}; output {a.scale}x, alpha via {a.alpha}")

    manifest = a.inp / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text())
        m["upscale"] = {"scale": a.scale, "model": a.model.name, "model_sha256": sha256(a.model),
                        "model_scale": up.scale, "alpha": a.alpha, "pad": a.pad}
        (a.out / "manifest.json").write_text(json.dumps(m, indent=1))

    t0 = time.perf_counter()
    last = t0
    done = skipped = 0
    failures: list[tuple[str, str]] = []
    inputs_path = a.out / "upscale-inputs.json"
    inputs = json.loads(inputs_path.read_text()) if inputs_path.exists() else {}
    def write(dst: Path, result: np.ndarray, name: str, digest: str) -> None:
        nonlocal done
        tmp = dst.with_name(dst.name + ".tmp")  # a killed run never leaves half a PNG
        Image.fromarray(result, "RGBA").save(tmp, format="PNG")
        os.replace(tmp, dst)
        inputs[name] = digest
        done += 1

    batch: list[tuple[Path, str, dict]] = []
    batch_area = 0

    def flush() -> None:
        nonlocal batch, batch_area
        if not batch:
            return
        try:
            results = upscale_batch([p for _, _, p in batch], up, a.scale, a.alpha)
            for (src, digest, _), result in zip(batch, results):
                write(a.out / src.name, result, src.name, digest)
        except Exception as e:  # keep going; report at the end
            failures.extend((src.name, f"{type(e).__name__}: {e}") for src, _, _ in batch)
        batch, batch_area = [], 0

    for i, src in enumerate(files, 1):
        dst = a.out / src.name
        digest = hashlib.sha1(src.read_bytes()).hexdigest()
        if dst.exists() and not a.force and up_to_date(src, dst, a.scale, inputs.get(src.name), digest):
            inputs[src.name] = digest
            skipped += 1
        else:
            try:
                rgba = np.asarray(Image.open(src).convert("RGBA"))
                prep = prepare(rgba, a.pad, a.pad_transparent)
                ph, pw = prep["rgb"].shape[:2]
                if a.batch and ph <= BATCH_MAX and pw <= BATCH_MAX:
                    # Canvas nearly full (rows waste some width): run what is queued first.
                    if batch_area + ph * pw > BATCH_CANVAS * BATCH_CANVAS * 3 // 4:
                        flush()
                    batch.append((src, digest, prep))
                    batch_area += ph * pw
                else:
                    out_rgb = up.run(prep["rgb"])
                    out_a = None if prep["alpha"] is None else upscale_alpha(prep["alpha"], up, a.alpha)
                    write(dst, finish(prep, out_rgb, out_a, up.scale, a.scale), src.name, digest)
            except Exception as e:  # keep going; report at the end
                failures.append((src.name, f"{type(e).__name__}: {e}"))
        if i == len(files):
            flush()
        now = time.perf_counter()
        if now - last >= 10 or i == len(files):
            inputs_path.write_text(json.dumps(inputs, indent=0, sort_keys=True))
            rate = done / (now - t0) if done else 0.0
            eta = (len(files) - i) / rate if rate else 0.0
            print(f"[{i}/{len(files)}] {done} written, {skipped} skipped, {len(failures)} failed, "
                  f"{rate:.1f}/s, eta {eta:.0f}s", flush=True)
            last = now

    total = time.perf_counter() - t0
    print(f"done in {total:.1f}s: {done} written, {skipped} skipped, {len(failures)} failed"
          f"{f', {up.fp16_retries} fp16 retries in fp32' if up.fp16_retries else ''}")
    for name, err in failures:
        print(f"  FAILED {name}: {err}", file=sys.stderr)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
