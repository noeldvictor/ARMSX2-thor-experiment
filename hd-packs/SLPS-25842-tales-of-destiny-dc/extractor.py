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

- Character sprites are `anp3` files (magic `anp3`; u32 at 12 = the CLUT's offset). Their frames
  sit back to back before the CLUT, each behind a 16-byte record (u8 width, u8 height, u8 flags,
  u8 palette?, 12 zero bytes), all at the file's one bit depth (8 or 4). The frame chain is found by
  walking from each plausible record to exactly the CLUT offset. The CLUT is 256 CSM1 entries for
  8-bit files; for 4-bit files a 16-wide CLUT image whose 8x2 patches are the palettes.

- The font is in the executable, `SLPS_258.42`: one table of 24x24 4-bit glyphs, 288 bytes each,
  rows of 12 bytes, low nibble first, from 0xE18E0 to 0x17A100 (2169 cells in several weights -
  ASCII, kana, Latin extras, kanji; a few cells at the ends are not glyphs and never match). The
  game uploads one glyph at a time as PSMT4HL and colours it with a palette it makes at runtime,
  so glyphs are palette-free images. That palette, read from a GS dump's VRAM (CBP 0x3FFF; its
  hash 1f20317c5df98afd is the one in the texture dump names): 0 transparent, a dark outline at
  alpha 0x40/0x7C, then greys up to white fill. The offsets are for the English v1.6 executable
  (the recipe's ISO SHA-1 pins it).

Not covered yet:
- The title art is uploaded as PSMCT32 data (512x128) and drawn as 512x512 PSMT8 from the same
  memory, a common PS2 upload trick; turning that into indices needs the GS's swizzle, which the
  tools do not emulate yet.
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "disc_textures"))
from disc_codecs import find_tales_blobs, tales_lzss  # noqa: E402
from tim2 import csm1_unswizzle, tim2_images  # noqa: E402


def anp3_frames(buf: bytes):
    """(key, indices, palettes) for every frame of an anp3 sprite file, or nothing."""
    import hashlib

    if buf[:4] != b"anp3" or len(buf) < 32:
        return
    clut_off = struct.unpack_from("<I", buf, 12)[0]
    if not 32 <= clut_off < len(buf):
        return

    def chain(p: int, bpp: int):
        frames = []
        while p < clut_off:
            w, h = buf[p], buf[p + 1]
            if w == 0 or h == 0 or buf[p + 4 : p + 16] != bytes(12):
                return None
            frames.append((p + 16, w, h))
            p += 16 + (w * h * bpp + 7) // 8
        return frames if p == clut_off else None

    # The chain starts just after the offset at 8 (16120 -> 16128, 5980 -> 6000 in the files
    # looked at); look there first, the whole file only if that fails.
    near = struct.unpack_from("<I", buf, 8)[0]
    found = None
    for start, stop in ((near, min(near + 64, clut_off - 16)), (16, clut_off - 16)):
        for p in range(max(start, 16), stop):
            if buf[p] and buf[p + 1] and buf[p + 4 : p + 16] == bytes(12):
                for bpp in (8, 4):
                    frames = chain(p, bpp)
                    if frames:
                        found = (bpp, frames)
                        break
            if found:
                break
        if found:
            break
    if not found:
        return
    bpp, frames = found
    entries = np.frombuffer(buf, np.uint8, (len(buf) - clut_off) // 4 * 4, clut_off).reshape(-1, 4)
    if bpp == 8:
        if len(entries) < 256:
            return
        palettes = [csm1_unswizzle(entries[:256]).tobytes()]
    else:
        rows = entries[: len(entries) // 16 * 16].reshape(-1, 16, 4)
        palettes = [np.concatenate([rows[y, x : x + 8], rows[y + 1, x : x + 8]]).tobytes()
                    for y in range(0, len(rows) - 1, 2) for x in (0, 8)]
    clut = buf[clut_off:]
    for off, w, h in frames:
        raw = np.frombuffer(buf, np.uint8, (w * h * bpp + 7) // 8, off)
        if bpp == 8:
            idx = raw.reshape(h, w)
        else:
            idx = np.empty(w * h, np.uint8)
            idx[0::2] = raw & 0x0F
            idx[1::2] = raw >> 4
            idx = idx.reshape(h, w)
        # Every palette gets its own HD image (not a palette-free index map): the pack stores
        # palette images as ASTC, and a sprite batch is assembled from ASTC pieces block by block.
        yield hashlib.sha1(raw.tobytes() + clut).hexdigest()[:12], idx, palettes


FONT_TABLE = (0xE18E0, 0x17A100)  # in SLPS_258.42, English v1.6
# 16 entries as the GS holds them (u32 0xAABBGGRR), read from a dump's VRAM; XXH3 1f20317c5df98afd.
FONT_PALETTE = struct.pack("<16I", 0x00000000, 0x40010101, 0x7C020202, 0x80121212, 0x80353535, 0x804B4B4B,
                           0x80666666, 0x80767676, 0x80858585, 0x80969696, 0x80ABABAB, 0x80BCBCBC,
                           0x80D2D2D2, 0x80E5E5E5, 0x80F1F1F1, 0x80FDFDFD)


def palette_free_palette(key: str) -> bytes:
    """The palette a palette-free image (a font glyph) is upscaled through: the font's runtime one."""
    return FONT_PALETTE


def font_glyphs(iso_path: Path):
    """(key, indices, []) for every glyph cell of the executable's font table (palette-free)."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    b = io.BytesIO()
    iso.get_file_from_iso_fp(b, iso_path="/SLPS_258.42;1")
    iso.close()
    elf = b.getvalue()
    seen: set[bytes] = set()
    lo, hi = FONT_TABLE
    for off in range(lo, min(hi, len(elf) - 287), 288):
        raw = elf[off : off + 288]
        if raw in seen or not any(raw):
            continue
        seen.add(raw)
        packed = np.frombuffer(raw, np.uint8).reshape(24, 12)
        idx = np.empty((24, 24), np.uint8)
        idx[:, 0::2] = packed & 0x0F
        idx[:, 1::2] = packed >> 4
        yield f"font_{(off - lo) // 288:04d}", idx, []


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
        for key, texels, palettes in anp3_frames(buf):
            if key not in seen:
                seen.add(key)
                yield key, texels, palettes
        if depth < 3:
            for off, _length, sub in find_tales_blobs(buf):
                yield from pictures(sub, f"{where}/{off:x}", depth + 1)

    yield from font_glyphs(iso_path)
    for i, data in dat_files(iso_path):
        yield from pictures(data, str(i), 1)
