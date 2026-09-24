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
texel (0x80) must decode to 0x80 and nothing else may decode to 0x80. An image that fails is
written as a lossless PNG instead (`fallbacks`), which the emulator loads as RGBA8.

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


class AstcBatch:
    def __init__(self, astcenc: str, args: list[str] | None = None):
        self.astcenc = astcenc
        self.args = ENCODE_ARGS if args is None else args
        self.pending: list[tuple[Path, np.ndarray]] = []
        self.pixels = 0
        self.tmp = Path(tempfile.mkdtemp(prefix="astc_"))
        self.written = 0
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
        blocks = np.frombuffer(data, np.uint8, offset=16).reshape(-1, bx, 16)
        for (path, rgba), (px, py) in zip(self.pending, places):
            h, w = rgba.shape[:2]
            if alpha_survives(rgba, decoded[py : py + h, px : px + w]):
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
