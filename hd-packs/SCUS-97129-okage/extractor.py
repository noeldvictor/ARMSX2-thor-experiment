"""Okage: Shadow King (SCUS-97129) disc extractor for tools/disc_textures.

Everything this game needs that the shared tools do not know: where its textures are on the disc
and how they are stored. `make_pack.py` imports it; to look at the textures on their own,

    python tools/disc_textures/extract_native.py hd-packs/SCUS-97129-okage/extractor.py okage.iso OUT_DIR

writes each one as a PNG (see README.md in this folder for the whole recipe).

The disc's formats (worked out 2026-09-22):

- `.XPF` - archive. Header `XPFX`, u32 data offset, u32 entry count, u32 pad; then 32-byte entries
  (name[24], u32 offset from the data offset, u32 compressed length). Every entry is compressed.
- Compression - bit-flag LZ. Byte 0 is zero, bytes 1..3 the decoded size big-endian, byte 4 the
  first flag byte, data from byte 5. Flag bits MSB first: 0 = literal byte; 1 = reference, whose
  next bit picks a one-byte distance (-256..-1; a zero byte ends the stream) or a long one (that
  byte plus four more flag bits, minus 0xFF); then a gamma-coded length n, copying n + 1 bytes.
  The scheme was documented by simontime/xpftool; this is an independent implementation.
- `.XIM` - image, inside XPF archives or standalone. u32 at 0: a GS TEX0 value (bits 20-25 are the
  PSM: 0x13 PSMT8, 0x14 PSMT4, 0x01 PSMCT24 and friends). True-colour images have no palette
  block: the image block below starts at 0x10 (PSMCT24 is packed RGB). Indexed ones have, at 0x10,
  the palette block: u32 block size including its 16-byte
  header, u32 0, u32 palette count, u32 entry count, then RGBA entries in index order (not CSM1
  swizzled), alpha on the PS2 scale (0x80 = opaque). Then the image block: u32 block size including
  its 16-byte header (in some files the width instead), u32 0, u32 height, u32 width, then linear
  indices (PSMT4: low nibble first).

- `.FNT` - fonts (`/CMNDATA/FONT/`: BM, BMUI and the 24 px IQ24). Header: byte 1 the cell width,
  byte 2 the cell height, bytes 4 and 5 the first and last character code. Then one 20-byte entry
  per character (last - first + 1), whose bytes 8..19 are the glyph's corners in the font sheet
  (TL at 8, TR at 12, BL and BR at 16). Then u16 row count, u16 1, and the sheet: 256 wide,
  4 bits per texel, that many rows (BM and BMUI end with an empty row).
  The game colours the sheet with a palette it makes at runtime, so a font is a palette-free
  image. That palette, read from the emulator's `Disc atlas: palette` log line on the status
  menu: index 0 transparent, index k white at PS2 alpha 0x80 - 8 (k - 1) - index 1 is the
  solid ink and higher indices fade out.

Keys: `<sha1 of the decoded XIM, 12 hex>` for images (the HD file is `<key>.png`, or
`<key>_p<N>.png` for an XIM with several palettes), `fnt_<NAME>` for font sheets.
"""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path

import numpy as np


def lz_decode(src: bytes) -> bytes:
    size = int.from_bytes(src[1:4], "big")
    out = bytearray()
    pos = 5
    flag = src[4]
    mask = 0x80

    def bit() -> int:
        nonlocal flag, mask, pos
        if mask == 0:
            flag = src[pos]
            pos += 1
            mask = 0x80
        b = flag & mask
        mask >>= 1
        return 1 if b else 0

    while True:
        while bit() == 0:
            out.append(src[pos])
            pos += 1
        long_ref = bit()
        ch = src[pos]
        pos += 1
        if not long_ref:
            if ch == 0:
                break
            distance = ch - 256
        else:
            v = ch - 256
            for _ in range(4):
                v = (v << 1) | bit()
            distance = v - 0xFF
        n = 1
        while bit():
            n = (n << 1) | bit()
        start = len(out) + distance
        for i in range(n + 1):  # byte by byte: a reference may overlap what it is producing
            out.append(out[start + i])
    return bytes(out[:size])


def xpf_entries(data: bytes):
    magic, data_off, count, _ = struct.unpack_from("<4sIII", data, 0)
    if magic != b"XPFX":
        raise ValueError("not an XPF archive")
    for i in range(count):
        name, off, length = struct.unpack_from("<24sII", data, 16 + 32 * i)
        yield name.split(b"\0")[0].decode("ascii", "replace"), data[data_off + off : data_off + off + length]


def fnt_sheet(d: bytes):
    """The indices of a .FNT font sheet (see the docstring), or None for another layout."""
    count = d[5] - d[4] + 1
    data = d[8 + count * 20:]
    h = int.from_bytes(data[0:2], "little") if len(data) >= 4 else 0
    if h == 0 or len(data) != 4 + 128 * h:
        return None
    raw = np.frombuffer(data, np.uint8, 128 * h, 4).reshape(h, 128)
    px = np.empty((h, 256), np.uint8)
    px[:, 0::2] = raw & 0x0F
    px[:, 1::2] = raw >> 4
    return px


# The runtime font palette (see the docstring), RGBA with PS2 alpha, index order.
FONT_PALETTE = bytes([0, 0, 0, 0]) + b"".join(bytes([0xFF, 0xFF, 0xFF, 0x80 - 8 * k]) for k in range(15))


def palette_free_palette(key: str) -> bytes:
    """The palette a palette-free image is upscaled through (see extract_native.py)."""
    return FONT_PALETTE


def disc_images(iso_path: Path):
    """Yield (key, indices, palettes) for each unique palette image on the disc - the extractor
    contract, documented in tools/disc_textures/extract_native.py."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))

    def read(path: str) -> bytes:
        b = io.BytesIO()
        iso.get_file_from_iso_fp(b, iso_path=path)
        return b.getvalue()

    seen: set[bytes] = set()
    for root, _dirs, files in iso.walk(iso_path="/"):
        for f in files:
            path = root.rstrip("/") + "/" + f
            upper = f.upper()
            if ".FNT" in upper:
                px = fnt_sheet(read(path))
                if px is not None:
                    yield f"fnt_{f.split('.')[0].upper()}", px, []
                continue
            if ".XIM" in upper:
                blobs = [(read(path), False)]
            elif ".XPF" in upper:
                blobs = [(b, True) for n, b in xpf_entries(read(path)) if n.lower().endswith(".xim")]
            else:
                continue
            for blob, compressed in blobs:
                digest = hashlib.sha1(blob).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                d = lz_decode(blob) if compressed else blob
                psm = (struct.unpack_from("<I", d, 0)[0] >> 20) & 0x3F
                if psm not in (0x13, 0x14):
                    continue
                key = hashlib.sha1(d).hexdigest()[:12]
                pal_size, _, pal_count, entries = struct.unpack_from("<IIII", d, 0x10)
                img_off = 0x10 + pal_size
                _, _, h, w = struct.unpack_from("<IIII", d, img_off)
                px = np.frombuffer(d, np.uint8, len(d) - img_off - 0x10, img_off + 0x10)
                if psm == 0x14:
                    e = np.empty(px.size * 2, np.uint8)
                    e[0::2] = px & 0x0F
                    e[1::2] = px >> 4
                    px = e
                indices = px[: w * h].reshape(h, w)
                pals = [d[0x20 + p * entries * 4 : 0x20 + (p + 1) * entries * 4] for p in range(max(pal_count, 1))]
                yield key, indices, pals
    iso.close()
