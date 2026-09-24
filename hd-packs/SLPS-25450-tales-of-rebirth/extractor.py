"""Tales of Rebirth (SLPS-25450) disc extractor for tools/disc_textures.

Worked out 2026-09-24 on the English fan translation v1.0; the translation keeps the serial. The
ISO's UDF bridge is stale after patching, so the disc is read through isofs.py (ISO 9660 only).

- Three big files: `DAT.BIN` (1.9 GB, everything but movies and fields), `MOV.BIN` (movies) and
  `FLD.BIN` (0.8 GB, nine files of geometry, no textures). There is no table file on the disc: the tables are in the
  executable, `SLPS_254.50`, one after another - DAT.BIN's at 0xD76B0 (14,981 files + its end),
  then MOV.BIN's, then FLD.BIN's. Each is u32 entries in Tales of Destiny's DAT.TBL format: the
  file's start with the padding after the file in the low 6 bits; the last entry is the file size.
- A DAT.BIN file is Namco's Tales compression (`disc_codecs.tales_lzss`: u8 version 1/3, u32
  compressed size, u32 decompressed size) or stored raw. Decoded files are packs (`SCPK` and
  plain offset tables) holding TIM2 pictures, `anp3` sprite files and more Tales-compressed
  sub-files at any offset (`find_tales_blobs`).
- Character sprites are `anp3` files, as in Tales of Destiny (`tales.anp3_frames`); Rebirth's
  are 4-bit, the frame record's bytes 2-3 hold the frame's byte count, and the CLUT is a 16-wide
  image of four 8x2 palettes. A sprite is drawn from several frames placed in one buffer, so the
  emulator matches it as a composite.
- The font is in the executable: 24x24 4-bit glyphs, 288 bytes each (rows of 12 bytes, low nibble
  first), just before DAT.BIN's table. The English patch's font is one small table - digits,
  Latin letters and punctuation. The game uploads one glyph at a time as PSMT4HL and colours it
  with a palette that stays resident in VRAM (CBP 0x3AF6): a palette-free image.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "disc_textures"))
import isofs  # noqa: E402
from disc_codecs import find_tales_blobs, tales_lzss  # noqa: E402
from tales import anp3_frames  # noqa: E402
from tim2 import tim2_images  # noqa: E402

EXECUTABLE = "/SLPS_254.50"
# Offsets in the English patch's executable (the recipe's ISO SHA-1 pins it).
DAT_TABLE = 0xD76B0
FONT_TABLE = (0xD0560, 0xD75E0)  # a few cells at the ends are not glyphs and never match
# The font's palette as the GS holds it (u32 0xAABBGGRR), read from a field dump's VRAM at CBP
# 0x3AF6; its XXH3 6986de77e268b7fc is the one in the texture dump names.
FONT_PALETTE = struct.pack("<16I", 0x00000000, 0x18000000, 0x33000000, 0x4C000000, 0x64000000, 0x7F000000,
                           0x80191919, 0x80363636, 0x804E4E4E, 0x80636363, 0x807A7A7A, 0x809F9F9F,
                           0x80B3B3B3, 0x80C7C7C7, 0x80DDDDDD, 0x80FDFDFD)


def palette_free_palette(key: str) -> bytes:
    """The palette a palette-free image (a font glyph) is upscaled through: the font's runtime one."""
    return FONT_PALETTE


def _table(elf: bytes, at: int, end_value: int) -> list[int]:
    """The u32 entries from `at` up to and including the one equal to `end_value` (the file size)."""
    a = np.frombuffer(elf, "<u4", (len(elf) - at) // 4, at)
    stop = int(np.nonzero(a == end_value)[0][0])
    return [int(x) for x in a[: stop + 1]]


def dat_files(iso_path: Path):
    """(index, bytes) for every DAT.BIN file, decoded when compressed."""
    elf = isofs.read(iso_path, EXECUTABLE)
    entries = _table(elf, DAT_TABLE, isofs.files(iso_path)["/DAT.BIN"][1])
    f, _size = isofs.open_file(iso_path, "/DAT.BIN")
    base = f.tell()
    with f:
        for i in range(len(entries) - 1):
            start = entries[i] & ~63
            length = (entries[i + 1] & ~63) - start - (entries[i] & 63)
            if not 0 < length <= (256 << 20):
                continue
            f.seek(base + start)
            data = f.read(length)
            if data[0] in (1, 3) and len(data) >= 9:
                comp, dec = struct.unpack_from("<II", data, 1)
                if 9 + comp <= len(data) and 0 < dec < (64 << 20):
                    try:
                        data = tales_lzss(data[9 : 9 + comp], data[0], dec)
                    except ValueError:
                        pass
            yield i, data


def font_glyphs(iso_path: Path):
    """(key, indices, []) for every glyph cell of the executable's font table (palette-free)."""
    elf = isofs.read(iso_path, EXECUTABLE)
    seen: set[bytes] = set()
    lo, hi = FONT_TABLE
    for off in range(lo, hi, 288):
        raw = elf[off : off + 288]
        if raw in seen or not any(raw):
            continue
        seen.add(raw)
        packed = np.frombuffer(raw, np.uint8).reshape(24, 12)
        idx = np.empty((24, 24), np.uint8)
        idx[:, 0::2] = packed & 0x0F
        idx[:, 1::2] = packed >> 4
        yield f"font_{(off - lo) // 288:04d}", idx, []


def pack_members(buf: bytes):
    """The members of a plain pack - u32 count, then count (offset, size) pairs - or nothing.

    Most anp3 files are Tales-compressed sub-files (find_tales_blobs finds them), but some packs
    store them raw, and an anp3 file's CLUT runs to the end of the file, so it needs its extent."""
    if len(buf) < 12:
        return
    n = struct.unpack_from("<I", buf, 0)[0]
    if not 0 < n < 4096 or len(buf) < 4 + 8 * n:
        return
    pairs = struct.unpack_from(f"<{2 * n}I", buf, 4)
    end = 4 + 8 * n
    if not end <= pairs[0] <= end + 64:
        return
    for k in range(n):
        off, size = pairs[2 * k], pairs[2 * k + 1]
        if off < end or off + size > len(buf):
            return
        end = off + size
    for k in range(n):
        yield buf[pairs[2 * k] : pairs[2 * k] + pairs[2 * k + 1]]


def sprite_frames(buf: bytes):
    """anp3 frames. Most files keep the CLUT at the header's offset; the bigger ones run their
    frames on past it (in 64 KB steps) to a CLUT of 128 or 256 bytes at the very end."""
    if buf[:4] != b"anp3" or len(buf) < 32:
        return
    tail = len(buf) - struct.unpack_from("<I", buf, 12)[0]
    if 0 < tail <= 1024:
        yield from anp3_frames(buf)
        return
    for size in range(64, 1024 + 1, 64):
        frames = list(anp3_frames(buf, len(buf) - size))
        if frames:
            yield from frames
            return


def colourful(texels: np.ndarray, palettes: list[bytes]) -> list[bytes]:
    """The palettes that paint the image in more than one colour. A TIM2 map sheet often carries
    silhouette palettes (every visible entry the same colour - shadows, fades); an upscale gains
    nothing there and they would double the pack, so those draws stay native."""
    used = np.unique(texels)
    keep = []
    for pal in palettes:
        entries = np.frombuffer(pal, np.uint8).reshape(-1, 4)
        visible = entries[used[used < len(entries)]]
        visible = visible[visible[:, 3] > 0]
        if len(visible) and (visible[:, :3] != visible[0, :3]).any():
            keep.append(pal)
    return keep


def disc_images(iso_path: Path):
    """Every unique texture on the disc - the extractor contract (extract_native.py)."""
    seen: set[str] = set()

    def pictures(buf: bytes, depth: int):
        for key, texels, palettes, _info in tim2_images(buf, ""):
            if key not in seen:
                seen.add(key)
                palettes = colourful(texels, palettes) if palettes else palettes
                if palettes or texels.ndim == 3:
                    yield key, texels, palettes
        for key, texels, palettes in sprite_frames(buf):
            if key not in seen:
                seen.add(key)
                palettes = colourful(texels, palettes)
                if palettes:
                    yield key, texels, palettes
        if depth < 3:
            for member in pack_members(buf):
                if member[:4] == b"anp3":
                    yield from pictures(member, 3)  # anp3 only: TIM2 and blobs are found below
            for _off, _length, sub in find_tales_blobs(buf):
                yield from pictures(sub, depth + 1)

    yield from font_glyphs(iso_path)
    for _i, data in dat_files(iso_path):
        yield from pictures(data, 1)
