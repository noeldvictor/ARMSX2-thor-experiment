"""Sony TIM2 (`.tm2`) textures: find them in any buffer and turn them into what the GS receives.

TIM2 is the PS2 SDK's standard texture file, so a large share of games store their textures in it,
bare or inside archives. `tim2_images(buf)` finds every TIM2 in a (decompressed) buffer and yields
each picture in the extractor contract of `extract_native.py`:

- palette pictures (4 and 8 bit): an HxW uint8 index array and the palette(s) as RGBA bytes in
  index order with PS2 alpha - a 4-bit picture whose CLUT holds several 16-colour palettes yields
  each one, since the game picks one per draw (TEX0.CSA);
- PSMCT24: an HxWx3 RGB array; PSMCT32: HxWx4 RGBA with PS2 alpha.

Layout (all little-endian): file header `TIM2`, u8 version, u8 alignment (0: 16 bytes, 1: 128),
u16 picture count, 8 reserved. Each picture: u32 total size, u32 CLUT size, u32 image size, u16
header size, u16 CLUT colours, u8 format, u8 mipmap count, u8 CLUT type, u8 image type (1 16-bit,
2 24-bit, 3 32-bit, 4 4-bit, 5 8-bit), u16 width, u16 height, u64 GsTex0, u64 GsTex1, u32
GsTexaFbaPabe, u32 GsTexClut; then (header size - 48) bytes of mipmap/user header, the image,
the CLUT.

CLUT order: a 256-colour CLUT is stored the way it is uploaded for CSM1, with entries 8-15 and
16-23 of every 32 swapped; the GS reads it back in index order, which is what PCSX2 hashes. Bit 7
of the CLUT type marks a CLUT that is already in index order (a "linear" TIM2). A lone 16-colour
CLUT is not swapped; a 4-bit image with a CLUT of 256+ entries uses 16-colour palettes out of it,
each the 8x2 patch of the stored 16-wide CLUT - 16 consecutive entries once unswizzled. 16-bit CLUTs (5551) are not handled yet: the GS expands them with TEXA, which the
disc does not say.

Only the base level of a mipmapped picture is used, and 16-bit images are skipped.

Real files bend the format, so the reader is lenient where the data is unambiguous. Namco's
writer (Tales of Destiny DC) leaves the picture count, header size, width or CLUT size at 0, and
its total size sometimes leaves out the image: a count of 0 is read as 1, a header size of 0 as 48,
a CLUT size of 0 as whatever the total leaves after header and image, a picture is as long as
header + image + CLUT when the total is short, and a missing width comes from the image size and
height.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Iterator

import numpy as np

HEADER = 16
PICTURE_HEADER = 48


def csm1_unswizzle(pal: np.ndarray) -> np.ndarray:
    """256 RGBA entries as stored for CSM1 -> index order (swap 8..15 with 16..23 in every 32)."""
    p = pal.reshape(8, 4, 8, 4).copy()
    p[:, [1, 2]] = p[:, [2, 1]]
    return p.reshape(-1, 4)


def parse_tim2(buf: bytes | memoryview, pos: int = 0) -> tuple[list[dict], int] | None:
    """Parse the TIM2 at `pos`. Returns (pictures, total length) or None if it is not one."""
    if buf[pos : pos + 4] != b"TIM2" or pos + HEADER > len(buf):
        return None
    version, align, count = struct.unpack_from("<BBH", buf, pos + 4)
    if version not in (3, 4) or align > 1 or count >= 1024:
        return None
    count = count or 1  # Namco leaves it 0
    p = pos + (128 if align else HEADER)
    pictures = []
    for _ in range(count):
        if p + PICTURE_HEADER > len(buf):
            return None
        (total, clut_size, image_size, header_size, clut_colours, fmt, mips, clut_type, image_type,
         w, h, tex0, _tex1, _texa, _texclut) = struct.unpack_from("<IIIHHBBBBHHQQII", buf, p)
        header_size = header_size or PICTURE_HEADER
        if clut_size == 0 and image_type in (4, 5) and total > header_size + image_size:
            clut_size = total - header_size - image_size  # Namco: CLUT size 0, but counted in the total
        total = max(total, header_size + image_size + clut_size)
        bpp = {1: 16, 2: 24, 3: 32, 4: 4, 5: 8}.get(image_type)
        if bpp and h and not w:
            w = image_size * 8 // bpp // h
        if bpp and w and not h:
            h = image_size * 8 // bpp // w
        if (header_size < PICTURE_HEADER or p + total > len(buf) or not bpp
                or not 0 < w <= 2048 or not 0 < h <= 2048 or image_size < (w * h * bpp + 7) // 8):
            return None
        image = p + header_size
        clut = image + image_size
        pictures.append(dict(w=w, h=h, image_type=image_type, clut_type=clut_type, clut_colours=clut_colours,
                             mips=mips, tex0=tex0, image=bytes(buf[image : image + image_size]),
                             clut=bytes(buf[clut : clut + clut_size])))
        p += total
    return pictures, p - pos


def picture_texels(pic: dict):
    """(indices or texels, palettes) for one parsed picture, or None if it is not supported."""
    w, h, t = pic["w"], pic["h"], pic["image_type"]
    img = np.frombuffer(pic["image"], np.uint8)
    if t == 5:  # 8-bit indexed
        if img.size < w * h:
            return None
        idx = img[: w * h].reshape(h, w)
    elif t == 4:  # 4-bit indexed, low nibble first
        row = (w + 1) // 2
        if img.size < row * h:
            return None
        packed = img[: row * h].reshape(h, row)
        idx = np.empty((h, row * 2), np.uint8)
        idx[:, 0::2] = packed & 0x0F
        idx[:, 1::2] = packed >> 4
        idx = idx[:, :w]
    elif t == 3:
        return (img[: w * h * 4].reshape(h, w, 4), []) if img.size >= w * h * 4 else None
    elif t == 2:
        return (img[: w * h * 3].reshape(h, w, 3), []) if img.size >= w * h * 3 else None
    else:
        return None

    colour_type = pic["clut_type"] & 0x3F
    clut = np.frombuffer(pic["clut"], np.uint8)
    if colour_type == 3:  # 32-bit entries
        entries = clut[: (clut.size // 4) * 4].reshape(-1, 4)
    elif colour_type == 2:  # 24-bit entries: opaque
        rgb = clut[: (clut.size // 3) * 3].reshape(-1, 3)
        entries = np.concatenate([rgb, np.full((len(rgb), 1), 0x80, np.uint8)], axis=1)
    else:
        return None  # 16-bit CLUTs need the draw's TEXA
    size = 256 if t == 5 else 16
    if len(entries) < size:
        return None
    if len(entries) >= 256 and not pic["clut_type"] & 0x80:
        # Stored for CSM1. A 4-bit image with a big CLUT picks 16-colour palettes out of it (by
        # CBP/CSA); unswizzled, each is 16 consecutive entries - the 8x2 patches of the stored
        # 16-wide CLUT image.
        whole = len(entries) // 256 * 256
        entries = np.concatenate([csm1_unswizzle(entries[k : k + 256]) for k in range(0, whole, 256)]
                                 + [entries[whole:]])
    palettes = [np.ascontiguousarray(entries[k * size : (k + 1) * size]).tobytes()
                for k in range(len(entries) // size)]
    return idx, palettes


def tim2_images(buf: bytes, where: str = "") -> Iterator[tuple[str, np.ndarray, list[bytes], dict]]:
    """Every supported TIM2 picture in `buf`: (key, texels, palettes, info). The key is the SHA-1
    of the picture's image and CLUT data, so the same texture found twice has one key."""
    mv = memoryview(buf)
    pos = buf.find(b"TIM2")
    while pos >= 0:
        parsed = parse_tim2(mv, pos)
        if parsed is None:
            pos = buf.find(b"TIM2", pos + 4)
            continue
        pictures, length = parsed
        for n, pic in enumerate(pictures):
            out = picture_texels(pic)
            if out is None:
                continue
            texels, palettes = out
            key = hashlib.sha1(pic["image"] + pic["clut"]).hexdigest()[:12]
            yield key, texels, palettes, {"where": f"{where}@{pos:x}#{n}", "psm": (pic["tex0"] >> 20) & 0x3F,
                                          "image_type": pic["image_type"], "clut_type": pic["clut_type"]}
        pos = buf.find(b"TIM2", pos + max(length, 4))
