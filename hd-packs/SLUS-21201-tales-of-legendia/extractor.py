"""Tales of Legendia (SLUS-21201) disc extractor for tools/disc_textures.

Worked out 2026-09-24 on the NTSC-U disc (the ReUndub v1.4 build: audio only, textures unchanged).

- The data is in CRI AFS archives under /AFS (SYS_REG, FIELD, MAP, BATTLE; MOVIE and SND are
  video and audio). The ISO's UDF bridge is stale after patching, so the disc is read through
  isofs.py (ISO 9660 only), not pycdlib.
- Every entry is a `CPS` file: `CPS\\0`, u32 stored size (with the 16-byte header), u32 unpacked
  size, u32 0, then Namco's Tales compression, version 1 (disc_codecs.tales_lzss) - or the data
  as is when stored size - 16 == unpacked size.
- The unpacked files (`*.mcd` and friends) hold no image files: they hold the GS packets that
  upload the textures, inside VIF DIRECT blocks - a CLUT (PSMCT32, 16x16 at block 0x3A70) then the
  texture itself as PSMCT32 data at 0x3A80, drawn right after as PSMT8/PSMT4 through TEX0. Every
  texture streams through the same slot. gifscan.textures() pairs each image upload with its CLUT
  and the TEX0 that draws it, and reads it back through the GS swizzle as the draw does.
- PCSX2 names the big ones by region (a 512x512 PSMT8 drawn from a 1024x1024 TEX0 is
  `...-r512x512-...`), so those names reproduce from disc data; small ones are full size.
- The dialogue font is in SYS_REG.AFS entry 0 (`system_regident.mcd`): one chain of 199 glyphs
  from offset 0x81BB0 of the unpacked file, two faces. Each glyph is a 16-byte record - six u16
  metrics, u8 height, u8 advance, u8 width class (0: 24 pixels, 1: 32), u8 0 - then its rows,
  4-bit, low nibble first, the height rounded up to even and the bytes to 16. The game clears a
  48x48 corner of a 64x64 PSMT4 texture, uploads the glyph's rows (the even height) at (4,1) and
  draws it with a white alpha-ramp palette it uploads each frame: a palette-free image, 64x64.

Checked against the boat scene's dump: every 512/256/128-texel texture's name reproduces.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "disc_textures"))
import isofs  # noqa: E402
from disc_codecs import tales_lzss  # noqa: E402
from gifscan import textures  # noqa: E402

ARCHIVES = ("/AFS/SYS_REG.AFS", "/AFS/FIELD.AFS", "/AFS/MAP.AFS", "/AFS/BATTLE.AFS")
FONT_CHAIN = 0x81BB0  # first glyph record in SYS_REG.AFS entry 0, unpacked
# The font's palette as the GS holds it (u32 0xAABBGGRR): white, alpha 0..0x7F. Rebuilt from the
# texture dumps' colours; its XXH3 ad3632fee165f017 is the one in the dump names.
FONT_PALETTE = struct.pack("<16I", *(a << 24 | 0xFFFFFF for a in (0x00, 0x08, 0x11, 0x19, 0x22, 0x2A, 0x33, 0x3B,
                                                                    0x44, 0x4C, 0x55, 0x5D, 0x66, 0x6E, 0x77, 0x7F)))


def palette_free_palette(key: str) -> bytes:
    """The palette a palette-free image (a font glyph) is upscaled through: the font's runtime one."""
    return FONT_PALETTE


def afs_entries(data: bytes):
    count = struct.unpack_from("<I", data, 4)[0]
    for i in range(count):
        off, size = struct.unpack_from("<II", data, 8 + 8 * i)
        yield i, data[off : off + size]


def cps_unpack(blob: bytes) -> bytes | None:
    if blob[:4] != b"CPS\0" or len(blob) < 16:
        return None
    stored, size = struct.unpack_from("<II", blob, 4)
    if stored - 16 == size:
        return blob[16 : 16 + size]
    try:
        return tales_lzss(blob[16:stored], 1, size)
    except ValueError:
        return None


def font_glyphs(iso_path: Path):
    """(key, indices, []) for every glyph of the dialogue font, as the 64x64 texture it is drawn from."""
    sysreg = isofs.read(iso_path, ARCHIVES[0])
    font = cps_unpack(next(blob for i, blob in afs_entries(sysreg) if i == 0))
    p, n = FONT_CHAIN, 0
    # The chain ends where a record stops making sense (199 glyphs on the NTSC-U disc).
    while (p + 16 <= len(font) and font[p + 14] <= 1 and not font[p + 15] and 1 <= font[p + 12] <= 32
           and 1 <= font[p + 13] <= 32):
        rows, width = (font[p + 12] + 1) // 2 * 2, 32 if font[p + 14] else 24
        packed = np.frombuffer(font, np.uint8, rows * width // 2, p + 16).reshape(rows, width // 2)
        idx = np.zeros((64, 64), np.uint8)
        idx[1 : 1 + rows, 4 : 4 + width : 2] = packed & 0x0F
        idx[1 : 1 + rows, 5 : 5 + width : 2] = packed >> 4
        yield f"font_{n:03d}", idx, []
        p += 16 + (rows * width // 2 + 15) // 16 * 16
        n += 1


def disc_images(iso_path: Path):
    """Every texture the disc's GS packets upload - the extractor contract (extract_native.py)."""
    seen: set[str] = set()
    yield from font_glyphs(iso_path)
    for archive in ARCHIVES:
        data = isofs.read(iso_path, archive)
        for _, blob in afs_entries(data):
            body = cps_unpack(blob)
            if body is None:
                continue
            for tex in textures(body):
                key = hashlib.sha1(tex.blob + struct.pack("<Q", tex.tex0 & ((1 << 34) - 1))).hexdigest()[:12]
                if key in seen:
                    continue
                seen.add(key)
                yield key, tex.texels, tex.palettes  # [] for PSMCT32: RGBA with PS2 alpha
