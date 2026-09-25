"""ASTC 4x4 encoding for disc packs, many images per astcenc call, alpha verified.

Disc packs store their HD images as ASTC 4x4 (8 bits a pixel, a quarter of RGBA8): the Thor's
GPU samples it natively, so an HD texture costs a quarter of the memory and is copied, not
decoded, when it loads. At 4x every crop edge is a multiple of 4 HD pixels - one native texel is
exactly one 4x4 block - so the emulator cuts crops and assembles composites block by block.

Encoding is Arm's `astcenc` (https://github.com/ARM-software/astc-encoder, Apache 2.0; get a
release build and put it on PATH, set ASTCENC, or pass --astcenc). Starting it costs ~0.1 s, which
over 100,000 sprites is hours, so `AstcBatch` lays many images out on one canvas (every image on
the 4-pixel grid), encodes the canvas once, and slices the blocks back out: ASTC blocks are
encoded independently, so each image's blocks are what encoding it alone would give.

Alpha must survive exactly. PS2 games alpha-test against exact values - River King draws almost
everything with ATST EQUAL, AREF 0x80 - and ASTC is lossy in alpha as in colour: with astcenc's
default weights 41% of an opaque texture's texels came back at 0x7F or 0x81, failed the test and
showed the surface behind as speckle. So the alpha channel is weighted 1000:1 (`-cw 1 1 1 1000`,
`-thorough`), which leaves ~0.1% of opaque texels wrong, all on blocks where opaque and
transparent texels meet. Every canvas is then decoded again and each image checked: an opaque
texel (0x80) must decode to 0x80 and nothing else may decode to 0x80.

Those failing blocks are repaired, not the image thrown away: 0x80 is not a value ASTC endpoints
can hold at the reduced precision astcenc picks for most blocks (6-bit endpoints give 130, 7-bit
129; only 8-bit gives 128). Each failing block is re-encoded by hand in a layout whose endpoints
are 8-bit (QUANT_256), so 0x80 is exact by construction: an all-opaque block as one plane with
2-bit weights and alpha 0x80 at both ends (`block_opaque`), a mixed block as two planes - alpha on
its own 1-bit weights, 0x80 or the block's other alpha value - and two colours (`block_mixed`).
The canvas is decoded again with astcenc and checked; on Tales of Rebirth 2% of blocks needed it,
at about the colour error astcenc's own blocks had.

What "exact" means depends on the game's alpha tests (`TEST` in a GS dump). `rule="exact"` (the
default) is for EQUAL 0x80 (River King): 0x80 stays 0x80, nothing else becomes it.
`rule="threshold"` is for GEQUAL/LESS 0x80 (Tales of Rebirth): every texel stays on its side of
0x80 - 0x80 may come back as 0x81, which the test cannot tell apart. Under it the repair is two
alpha levels, one each side (`block_two_level`), which also covers art whose PS2 alpha goes above
0x80 (Rebirth's cave sheets; under "exact" those blocks cannot be repaired). Only an image that
still fails is written as a lossless PNG instead (`fallbacks`), loaded as RGBA8.

The `.astc` container: 16-byte header (magic 13 AB A1 5C, block x/y/z, 24-bit width, height,
depth), then the blocks row by row, 16 bytes each.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

MAGIC = bytes([0x13, 0xAB, 0xA1, 0x5C])
CANVAS = 4096  # pixels a side; the budget per astcenc call
ENCODE_ARGS = ["-thorough", "-cw", "1", "1", "1", "1000"]
PS2_OPAQUE = 0x80


def find_astcenc(given: str | None = None) -> str | None:
    for c in (given, os.environ.get("ASTCENC"), "astcenc", "astcenc-avx2", "astcenc-sse4.1", "astcenc-sse2"):
        if not c:
            continue
        if Path(c).is_file():
            return str(c)
        if shutil.which(c):
            return shutil.which(c)
    return None


def header(width: int, height: int) -> bytes:
    return MAGIC + bytes([4, 4, 1]) + width.to_bytes(3, "little") + height.to_bytes(3, "little") + (1).to_bytes(3, "little")


def alpha_survives(src: np.ndarray, decoded: np.ndarray) -> bool:
    """Whether ASTC kept the alpha an exact PS2 alpha test sees: opaque stays opaque, nothing else becomes it."""
    a, b = src[..., 3], decoded[..., 3]
    opaque = a == PS2_OPAQUE
    return bool(np.all(b[opaque] == PS2_OPAQUE) and not np.any(b[~opaque] == PS2_OPAQUE))


def alpha_kept(src: np.ndarray, decoded: np.ndarray, rule: str = "exact") -> bool:
    """alpha_survives() for rule "exact"; for "threshold", every texel on its side of 0x80."""
    if rule == "threshold":
        return bool(np.array_equal(src[..., 3] >= PS2_OPAQUE, decoded[..., 3] >= PS2_OPAQUE))
    return alpha_survives(src, decoded)


def bad_blocks(src: np.ndarray, decoded: np.ndarray, rule: str = "exact") -> list[tuple[int, int]]:
    """The 4x4 blocks (row, column) where alpha_kept() fails."""
    a, b = src[..., 3], decoded[..., 3]
    if rule == "threshold":
        bad = (a >= PS2_OPAQUE) != (b >= PS2_OPAQUE)
    else:
        bad = ((a == PS2_OPAQUE) & (b != PS2_OPAQUE)) | ((a != PS2_OPAQUE) & (b == PS2_OPAQUE))
    return sorted({(int(y) // 4, int(x) // 4) for y, x in zip(*np.nonzero(bad))})


# Hand-built blocks (Khronos ASTC spec: block mode in bits 0-10, partition count - 1 in 11-12,
# the colour endpoint mode in 13-16, endpoint values from bit 17 up, weights from bit 127 down).
_CEM_RGBA_DIRECT = 12
_MODE_4X4_QUANT4 = 0x042  # 4x4 weight grid, one plane, 2-bit weights
_MODE_4X4_DUAL_QUANT2 = 0x441  # 4x4 weight grid, two planes, 1-bit weights
_ALPHA_PLANE = 3  # colour component selector: alpha has the second plane


def _block(mode: int, e0: tuple, e1: tuple, weights: list[int], bits_each: int, ccs: int | None = None) -> bytes:
    """One block with CEM 12 endpoints at QUANT_256 (8 bits, exact) - the only quant level these
    layouts leave room for - and plain binary weights stored bit-reversed from the top."""
    v = mode | (_CEM_RGBA_DIRECT << 13)
    for i, x in enumerate((e0[0], e1[0], e0[1], e1[1], e0[2], e1[2], e0[3], e1[3])):
        v |= (int(x) & 0xFF) << (17 + 8 * i)
    if ccs is not None:
        v |= ccs << (128 - len(weights) * bits_each - 2)
    k = 0
    for value in weights:
        for b in range(bits_each):
            if (value >> b) & 1:
                v |= 1 << (127 - k)
            k += 1
    return v.to_bytes(16, "little")


def _ordered(lo: tuple, hi: tuple) -> tuple[tuple, tuple, bool]:
    """CEM 12 swaps the endpoints and blue-contracts them when e1's RGB sum is below e0's."""
    return (lo, hi, False) if sum(hi[:3]) >= sum(lo[:3]) else (hi, lo, True)


def _axis(px: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = px.mean(0)
    d = px - mean
    if not np.abs(d).any():
        return mean, np.array([1.0, 0.0, 0.0])
    return mean, np.linalg.svd(d, full_matrices=False)[2][0]


def block_opaque(rgb: np.ndarray) -> bytes:
    """An all-opaque 4x4 block (16 x RGB): a line fit with four steps, alpha 0x80 at both ends."""
    px = rgb.astype(np.float64)
    mean, axis = _axis(px)
    t = (px - mean) @ axis
    lo = tuple(np.clip(np.rint(mean + axis * t.min()), 0, 255).astype(int)) + (PS2_OPAQUE,)
    hi = tuple(np.clip(np.rint(mean + axis * t.max()), 0, 255).astype(int)) + (PS2_OPAQUE,)
    e0, e1, _ = _ordered(lo, hi)
    c0, c1 = np.array(e0[:3], float) * 257, np.array(e1[:3], float) * 257
    steps = np.stack([np.floor((c0 * (64 - w) + c1 * w + 32) / 64) for w in (0, 21, 43, 64)])
    idx = ((px[:, None, :] * 257 - steps[None]) ** 2).sum(2).argmin(1)
    return _block(_MODE_4X4_QUANT4, e0, e1, [int(i) for i in idx], 2)


def block_mixed(rgba: np.ndarray) -> bytes | None:
    """A 4x4 block (16 x RGBA) with alpha of its own plane: each texel 0x80 or the block's other
    (most common) alpha value, the colour one of two clusters. None if any alpha is above 0x80."""
    a = rgba[:, 3]
    if (a > PS2_OPAQUE).any():
        return None
    others = a[a != PS2_OPAQUE]
    a_lo = int(np.bincount(others).argmax()) if len(others) else 0
    opaque = a == PS2_OPAQUE
    px = rgba[:, :3].astype(np.float64)
    mean, axis = _axis(px[opaque] if opaque.any() else px)  # the colours that will be seen
    t = (px - mean) @ axis
    lab = t > np.median(t)
    c_lo = px[~lab].mean(0) if (~lab).any() else mean
    c_hi = px[lab].mean(0) if lab.any() else mean
    for _ in range(4):
        lab = ((px - c_hi) ** 2).sum(1) < ((px - c_lo) ** 2).sum(1)
        if lab.any():
            c_hi = px[lab].mean(0)
        if (~lab).any():
            c_lo = px[~lab].mean(0)
    lo = tuple(np.clip(np.rint(c_lo), 0, 255).astype(int)) + (a_lo,)
    hi = tuple(np.clip(np.rint(c_hi), 0, 255).astype(int)) + (PS2_OPAQUE,)
    e0, e1, swapped = _ordered(lo, hi)  # alpha travels with its colour endpoint
    rgb_bit, a_bit = lab.astype(int), opaque.astype(int)
    if swapped:
        rgb_bit, a_bit = 1 - rgb_bit, 1 - a_bit
    weights = [w for i in range(16) for w in (int(rgb_bit[i]), int(a_bit[i]))]  # planes interleaved
    return _block(_MODE_4X4_DUAL_QUANT2, e0, e1, weights, 1, _ALPHA_PLANE)


def block_two_level(rgba: np.ndarray) -> bytes:
    """A 4x4 block (16 x RGBA) for the threshold rule: two planes, alpha one level below 0x80 and
    one at or above it (each the most common value on its side), the colour two clusters."""
    a = rgba[:, 3]
    high = a >= PS2_OPAQUE
    a_lo = int(np.bincount(a[~high]).argmax()) if (~high).any() else 0
    a_hi = int(np.bincount(a[high]).argmax()) if high.any() else PS2_OPAQUE
    px = rgba[:, :3].astype(np.float64)
    mean, axis = _axis(px[a > 0] if (a > 0).any() else px)
    t = (px - mean) @ axis
    lab = t > np.median(t)
    c_lo = px[~lab].mean(0) if (~lab).any() else mean
    c_hi = px[lab].mean(0) if lab.any() else mean
    for _ in range(4):
        lab = ((px - c_hi) ** 2).sum(1) < ((px - c_lo) ** 2).sum(1)
        if lab.any():
            c_hi = px[lab].mean(0)
        if (~lab).any():
            c_lo = px[~lab].mean(0)
    lo = tuple(np.clip(np.rint(c_lo), 0, 255).astype(int)) + (a_lo,)
    hi = tuple(np.clip(np.rint(c_hi), 0, 255).astype(int)) + (a_hi,)
    e0, e1, swapped = _ordered(lo, hi)
    rgb_bit, a_bit = lab.astype(int), high.astype(int)
    if swapped:
        rgb_bit, a_bit = 1 - rgb_bit, 1 - a_bit
    weights = [w for i in range(16) for w in (int(rgb_bit[i]), int(a_bit[i]))]
    return _block(_MODE_4X4_DUAL_QUANT2, e0, e1, weights, 1, _ALPHA_PLANE)


def repair_block(rgba: np.ndarray, rule: str = "exact") -> bytes | None:
    """A block (16 x RGBA, PS2 alpha) re-encoded so its alpha passes `rule`, or None."""
    if (rgba[:, 3] == PS2_OPAQUE).all():
        return block_opaque(rgba[:, :3])
    return block_two_level(rgba) if rule == "threshold" else block_mixed(rgba)


class AstcBatch:
    def __init__(self, astcenc: str, args: list[str] | None = None, rule: str = "exact"):
        if rule not in ("exact", "threshold"):
            raise ValueError(f"alpha rule {rule!r}: exact or threshold")
        self.astcenc = astcenc
        self.args = ENCODE_ARGS if args is None else args
        self.rule = rule
        self.pending: list[tuple[Path, np.ndarray]] = []
        self.pixels = 0
        self.tmp = Path(tempfile.mkdtemp(prefix="astc_"))
        self.written = 0
        self.repaired_blocks = 0
        self.fallbacks: list[Path] = []  # .astc paths written as .png instead (alpha not exact)

    def add(self, path: Path, rgba: np.ndarray) -> None:
        """Queue one image (H x W x 4 uint8, PS2 alpha, both sides a multiple of 4) to be written as `path`."""
        h, w = rgba.shape[:2]
        if h % 4 or w % 4:
            raise ValueError(f"{path.name}: {w}x{h} is not on the 4x4 block grid")
        if self.pixels + h * w > CANVAS * CANVAS:
            self.flush()
        self.pending.append((path, rgba))
        self.pixels += h * w

    def flush(self) -> None:
        if not self.pending:
            return
        # Shelf layout: rows as tall as their tallest image, CANVAS wide (wider if one image is).
        width = max(CANVAS, max(r.shape[1] for _, r in self.pending))
        places = []
        x = y = row = 0
        for _, rgba in self.pending:
            h, w = rgba.shape[:2]
            if x + w > width:
                x, y, row = 0, y + row, 0
            places.append((x, y))
            x += w
            row = max(row, h)
        height = y + row
        canvas = np.zeros((height, width, 4), np.uint8)
        for (_, rgba), (px, py) in zip(self.pending, places):
            canvas[py : py + rgba.shape[0], px : px + rgba.shape[1]] = rgba
        src, dst, back = self.tmp / "canvas.tga", self.tmp / "canvas.astc", self.tmp / "decoded.tga"
        Image.fromarray(canvas, "RGBA").save(src)
        for cmd in ([self.astcenc, "-cl", str(src), str(dst), "4x4", *self.args, "-silent"],
                    [self.astcenc, "-dl", str(dst), str(back), "-silent"]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"astcenc failed: {r.stdout[-1000:]}{r.stderr[-1000:]}")
        decoded = np.asarray(Image.open(back).convert("RGBA"))
        data = dst.read_bytes()
        bx = (width + 3) // 4
        blocks = np.frombuffer(data, np.uint8, offset=16).reshape(-1, bx, 16).copy()
        # Re-encode the blocks whose alpha is off, then decode the canvas once more to check.
        repaired = False
        for (path, rgba), (px, py) in zip(self.pending, places):
            h, w = rgba.shape[:2]
            bad = bad_blocks(rgba, decoded[py : py + h, px : px + w], self.rule)
            fixes = []
            for by, bxx in bad:
                block = repair_block(rgba[by * 4 : by * 4 + 4, bxx * 4 : bxx * 4 + 4].reshape(16, 4), self.rule)
                if block is None:
                    fixes = None
                    break
                fixes.append((py // 4 + by, px // 4 + bxx, block))
            for row, col, block in fixes or []:
                blocks[row, col] = np.frombuffer(block, np.uint8)
            if fixes:
                self.repaired_blocks += len(fixes)
                repaired = True
        if repaired:
            dst.write_bytes(data[:16] + blocks.tobytes())
            r = subprocess.run([self.astcenc, "-dl", str(dst), str(back), "-silent"], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"astcenc failed: {r.stdout[-1000:]}{r.stderr[-1000:]}")
            decoded = np.asarray(Image.open(back).convert("RGBA"))
        for (path, rgba), (px, py) in zip(self.pending, places):
            h, w = rgba.shape[:2]
            if alpha_kept(rgba, decoded[py : py + h, px : px + w], self.rule):
                part = blocks[py // 4 : (py + h) // 4, px // 4 : (px + w) // 4]
                path.write_bytes(header(w, h) + np.ascontiguousarray(part).tobytes())
            else:
                Image.fromarray(rgba, "RGBA").save(path.with_suffix(".png"))
                self.fallbacks.append(path)
            self.written += 1
        self.pending, self.pixels = [], 0

    def close(self) -> None:
        self.flush()
        shutil.rmtree(self.tmp, ignore_errors=True)
