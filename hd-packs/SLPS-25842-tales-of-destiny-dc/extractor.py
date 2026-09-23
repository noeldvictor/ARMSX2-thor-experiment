"""Tales of Destiny: Director's Cut (SLPS-25842) disc extractor for tools/disc_textures.

Worked out 2026-09-23 on the English fan-translated disc (v1.6); the translation keeps the serial.

- The disc is two big files: `DAT.BIN` (2.1 GB, everything but movies) indexed by `DAT.TBL`, and
  `MOV.BIN` (movies). `DAT.TBL` is 8-byte entries, u32 + u32: the first is a byte offset into
  `DAT.BIN` whose low 6 bits are the padding after the file (files start 64-byte aligned); the
  second is usually the file's decompressed size - 0 for an empty slot, sometimes a flagged value
  (high byte set) for a raw pack.
- A file is Namco's Tales compression (`disc_codecs.tales_lzss`: a 9-byte header, u8 version 1/3,
  u32 compressed size, u32 decompressed size) or stored raw.
- Decoded files are packs: a u32 count and offsets, or model data (`MGLK`), holding more
  Tales-compressed sub-files at any byte offset; `disc_codecs.find_tales_blobs` finds those.
- Textures are TIM2 files (`tim2.py`), many written by Namco's own tool with the picture count,
  header size and width left 0 - the reader is lenient about that. 256-colour CLUTs are stored
  CSM1-swizzled. Map textures are 256x256 8-bit; character sprites small 8/4-bit frames.

Not covered yet:
- The title art is uploaded as PSMCT32 data (512x128) and drawn as 512x512 PSMT8 from the same
  memory, a common PS2 upload trick; turning that into indices needs the GS's swizzle, which the
  tools do not emulate yet.
- The menu font is drawn from PSMT4HL glyphs with a palette made at runtime (palette-free, like
  Okage's fonts); its source file is not decoded yet.
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "disc_textures"))
from disc_codecs import find_tales_blobs, tales_lzss  # noqa: E402
from tim2 import tim2_images  # noqa: E402


def dat_files(iso_path: Path):
    """Yield (index, bytes) for every DAT.BIN entry, decoded when compressed."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    b = io.BytesIO()
    iso.get_file_from_iso_fp(b, iso_path="/DAT.TBL;1")
    table = np.frombuffer(b.getvalue(), "<u4").reshape(-1, 2).astype(np.int64)
    base = iso.get_record(iso_path="/DAT.BIN;1").extent_location() * 2048
    iso.close()

    starts = sorted({int(x) & ~63 for x in table[:, 0]})
    following = {o: starts[k + 1] if k + 1 < len(starts) else None for k, o in enumerate(starts)}
    with open(iso_path, "rb") as f:
        for i, (f1, f2) in enumerate(table):
            f1, f2 = int(f1), int(f2)
            if f2 == 0:
                continue
            off = f1 & ~63
            f.seek(base + off)
            head = f.read(9)
            version = head[0]
            comp, dec = struct.unpack_from("<II", head, 1)
            data = None
            if version in (1, 3) and dec == f2 and f2 < (64 << 20):
                try:
                    data = tales_lzss(f.read(comp), version, dec)
                except ValueError:
                    data = None
            elif version == 0 and dec == f2:
                data = f.read(dec)
            if data is None:  # stored raw (a flagged pack): up to the next file, minus padding
                end = following[off]
                size = (end - off - (f1 & 63)) if end else 0
                if not 0 < size <= (256 << 20):
                    continue
                f.seek(base + off)
                data = f.read(size)
            yield i, data


def disc_images(iso_path: Path):
    """Every unique TIM2 picture on the disc - the extractor contract (extract_native.py)."""
    seen: set[str] = set()

    def pictures(buf: bytes, where: str, depth: int):
        for key, texels, palettes, _info in tim2_images(buf, where):
            if key not in seen:
                seen.add(key)
                yield key, texels, palettes
        if depth < 3:
            for off, _length, sub in find_tales_blobs(buf):
                yield from pictures(sub, f"{where}/{off:x}", depth + 1)

    for i, data in dat_files(iso_path):
        yield from pictures(data, str(i), 1)
