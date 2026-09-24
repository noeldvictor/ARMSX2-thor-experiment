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

Checked against the boat scene's dump: every 512/256/128-texel texture's name reproduces.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "disc_textures"))
import isofs  # noqa: E402
from disc_codecs import tales_lzss  # noqa: E402
from gifscan import textures  # noqa: E402

ARCHIVES = ("/AFS/SYS_REG.AFS", "/AFS/FIELD.AFS", "/AFS/MAP.AFS", "/AFS/BATTLE.AFS")


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


def disc_images(iso_path: Path):
    """Every texture the disc's GS packets upload - the extractor contract (extract_native.py)."""
    seen: set[str] = set()
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
