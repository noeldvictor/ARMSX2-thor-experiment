"""River King: A Wonderful Journey (SLUS-21275) disc extractor for tools/disc_textures.

Worked out 2026-09-23 on the NTSC-U disc (Natsume / Marvelous, 2006).

- Everything but the music is in `DATA0000.AFS` (898 MB), CRI's AFS archive: `AFS\\0`, u32 entry
  count, then u32 offset + u32 size per entry, then the offset and size of a name table of 48-byte
  records (name[32], date, size). `DATA0001.AFS` is ADX audio only.
- Entries: `.arc` (1,076) are Nintendo U8 archives (magic 55 AA 38 2D, big-endian) of models and
  their textures; `.tex` / `.tpl` are texture lists (u32 2, u32 count, u32 offset per texture);
  `.pig` a single texture. None of these is compressed; three `.arc.clz` archives are
  (`disc_codecs.clz_unpack`): commonall (the UI windows and 2D parts), preload (map window, sky,
  water) and mainchapter0 (14.5 MB, 125 character archives).
- Every texture is a `P2IG` image, 128-byte header:
    0x00 `P2IG`, 0x04 `a` (version), 0x0C u16 6, u16 1, 0x10 name[8] + an 8-byte hash,
    0x20 u16 log2 width, u16 log2 height, 0x24 u8 PSM (0x13 PSMT8, 0x14 PSMT4, 0x24 PSMT4HL),
    0x25 u8 CLUT PSM (0 PSMCT32, 2 PSMCT16), 0x40 u32 CLUT offset, u32 CLUT size, u32 image
    offset, u32 image size (offsets from the header).
  Indices are linear, 4-bit low nibble first. 256-colour CLUTs are stored CSM1-swizzled (the
  dumped palette hashes match only once unswizzled); 16-colour ones in index order.
  So a raw scan for `P2IG` across the archive finds every uncompressed texture, wherever it sits.
- PSMCT16 CLUTs (831 4-bit images): the GS expands each entry to 32 bits with TEXA (alpha TA1
  when the top bit is set, else TA0). No dumped scene drew one yet, so TEXA is a guess below
  (TA0 0, TA1 0x80, AEM off) - if their matches never show up, that is why.

Checked against 259 textures desktop PCSX2 dumped (title, character select, name entry, the
house intro): 230 reproduce from disc data (`verify_dumps.py`). The other 29 are glyph caches
drawn with the runtime text palette; their cells are all font glyphs (below), so the emulator
builds them as composites. A 1x pack replays those five scenes on the Thor with every texture
matched and frames bit-identical to an empty pack.
"""

from __future__ import annotations

import hashlib
import io
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "disc_textures"))
from disc_codecs import clz_unpack  # noqa: E402
from tim2 import csm1_unswizzle  # noqa: E402

AFS_FILE = "/DATA0000.AFS;1"
ELF_FILE = "/SLUS_212.75;1"
# The dialogue font: 1-bit 24x24 glyphs, 3 bytes a row, most significant bit first, in the
# executable - digits, capitals, kana, symbols, lowercase, then ~1,300 kanji. The game copies the
# glyphs of a line into a 4-bit glyph cache (index 0 or 15, cells on a 24-pixel grid) and colours
# it with a palette it builds at runtime - white, alpha 0..127 over the 16 indices (read from a GS
# dump's VRAM; its hash b5f22e468666052c is the one in the texture dump names).
FONT_TABLE = (0x3965D0, 1586)  # offset in SLUS_212.75, glyph count (noise follows)
FONT_PALETTE = b"".join(bytes([255, 255, 255, a]) for a in (0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 127))
PSMT8, PSMT4, PSMT4HL = 0x13, 0x14, 0x24
CT16_TEXA = (0x00, 0x80, False)  # TA0, TA1, AEM - unverified, see above


def read_file(iso_path: Path, name: str) -> bytes:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    buf = io.BytesIO()
    iso.get_file_from_iso_fp(buf, iso_path=name)
    iso.close()
    return buf.getvalue()


def palette_free_palette(key: str) -> bytes:
    """The palette a palette-free image (a font glyph) is upscaled through: the game's text one."""
    return FONT_PALETTE


def font_glyphs(iso_path: Path):
    """The dialogue font's glyphs as palette-free 24x24 images, indices 0 or 15 as the cache holds them."""
    elf = read_file(iso_path, ELF_FILE)
    start, count = FONT_TABLE
    for i in range(count):
        rows = np.frombuffer(elf, np.uint8, 72, start + 72 * i).reshape(24, 3)
        yield f"font_{i:04d}", (np.unpackbits(rows, axis=1) * 15).astype(np.uint8), []


def afs_entries(data: bytes):
    """(name, offset, size) of every entry of an AFS archive."""
    if data[:4] != b"AFS\0":
        raise ValueError("not an AFS archive")
    count = struct.unpack_from("<I", data, 4)[0]
    table = [struct.unpack_from("<II", data, 8 + 8 * i) for i in range(count)]
    names_at = struct.unpack_from("<I", data, 8 + 8 * count)[0]
    for i, (off, size) in enumerate(table):
        name = f"{i:05d}"
        if names_at and names_at + 48 * count <= len(data):
            name = data[names_at + 48 * i : names_at + 48 * i + 32].split(b"\0")[0].decode("ascii", "replace")
        yield name, off, size


def expand_ct16(clut: bytes, ta0: int, ta1: int, aem: bool) -> bytes:
    """A PSMCT16 CLUT as the GS expands it to 32 bits (GSClut::Expand16)."""
    v = np.frombuffer(clut, "<u2").astype(np.uint32)
    r, g, b = (v & 0x1F) << 3, ((v >> 5) & 0x1F) << 3, ((v >> 10) & 0x1F) << 3
    a = np.where(v & 0x8000, ta1, ta0)
    if aem:
        a = np.where(v == 0, 0, a)
    return (r | (g << 8) | (b << 16) | (a.astype(np.uint32) << 24)).astype("<u4").tobytes()


def p2ig(buf: bytes, h: int):
    """(name, indices, palettes) of the P2IG image at `h`, or None if it is not a valid one."""
    if buf[h + 4] != 0x61 or h + 0x80 > len(buf):
        return None
    lw, lh = struct.unpack_from("<HH", buf, h + 0x20)
    psm, cpsm = buf[h + 0x24], buf[h + 0x25]
    if psm not in (PSMT8, PSMT4, PSMT4HL) or cpsm not in (0, 2) or not (0 < lw <= 10 and 0 < lh <= 10):
        return None
    clut_off, clut_size, img_off, img_size = struct.unpack_from("<IIII", buf, h + 0x40)
    w, hh = 1 << lw, 1 << lh
    bpp = 8 if psm == PSMT8 else 4
    entries = 256 if bpp == 8 else 16
    if (img_size != w * hh * bpp // 8 or clut_size != entries * (2 if cpsm else 4)
            or h + img_off + img_size > len(buf) or h + clut_off + clut_size > len(buf)):
        return None
    raw = np.frombuffer(buf, np.uint8, img_size, h + img_off)
    if bpp == 8:
        idx = raw.reshape(hh, w)
    else:
        idx = np.empty(w * hh, np.uint8)
        idx[0::2] = raw & 0x0F
        idx[1::2] = raw >> 4
        idx = idx.reshape(hh, w)
    clut = buf[h + clut_off : h + clut_off + clut_size]
    if cpsm == 2:
        clut = expand_ct16(clut, *CT16_TEXA)
    pal = np.frombuffer(clut, np.uint8).reshape(-1, 4)
    if entries == 256:
        pal = csm1_unswizzle(pal)
    name = buf[h + 0x10 : h + 0x18].split(b"\0")[0].decode("ascii", "replace")
    return name, idx, [np.ascontiguousarray(pal).tobytes()], raw.tobytes() + clut


def disc_images(iso_path: Path):
    """Every unique P2IG image in DATA0000.AFS, and the font - the extractor contract (extract_native.py)."""
    yield from font_glyphs(iso_path)
    data = read_file(iso_path, AFS_FILE)
    seen: set[str] = set()
    for name, off, size in afs_entries(data):
        entry = data[off : off + size]
        if name.lower().endswith(".clz"):
            entry = clz_unpack(entry)  # the three compressed archives (disc_codecs)
        pos = entry.find(b"P2IG")
        while pos >= 0:
            got = p2ig(entry, pos)
            if got is not None:
                _, idx, palettes, blob = got
                key = hashlib.sha1(blob).hexdigest()[:12]
                if key not in seen:
                    seen.add(key)
                    yield key, idx, palettes
            pos = entry.find(b"P2IG", pos + 4)
