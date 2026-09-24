"""Formats shared by Namco's Tales games (Tales of Destiny DC, Tales of Rebirth) for disc extractors.

- `anp3_frames(buf)`: character sprite files (magic `anp3`; u32 at 12 = the CLUT's offset). The
  frames sit back to back before the CLUT, each behind a 16-byte record (u8 width, u8 height, two
  bytes the games use differently - Rebirth keeps the frame's byte count there - and 12 zero
  bytes), all at the file's one bit depth (8 or 4). The frame chain is found by walking from each
  plausible record to exactly the CLUT offset. The CLUT is 256 CSM1 entries for 8-bit files; for
  4-bit files a 16-wide CLUT image whose 8x2 patches are the palettes.
"""

from __future__ import annotations

import struct

import numpy as np

from tim2 import csm1_unswizzle


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
