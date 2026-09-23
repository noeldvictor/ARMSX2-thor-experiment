"""Decompressors that PS2 games use for their disc files, shared by the extractors.

Each codec is plain Python; with `numba` installed (`pip install numba`) the inner loops are
compiled, which is the difference between minutes and hours on a 2 GB archive. Output is checked
by the caller against the size the file declares - a codec that is almost right fails loudly.

- `tales_lzss(data, version, out_size)` - Namco's "Tales" compression (Tales of Destiny DC,
  Rebirth, Legendia, ...), the format the fan tools call comptoe. A 9-byte header in front of the
  data - u8 version (1 or 3; 0 = stored), u32 compressed size, u32 decompressed size - is read by
  `tales_header()`. Okumura LZSS: a zero-filled 4096-byte window; flag bytes read LSB first,
  1 = literal; a reference is 12-bit window position + 4-bit length (+3). Version 3 adds runs: a
  reference whose length nibble is 0xF is a run - high nibble 0: `first + 19` copies of the next
  byte; otherwise `high nibble + 3` copies of the first byte. The window's write position starts at
  4078 for version 1 and 4079 for version 3 - getting that one byte wrong still gives the exact
  output size, but garbles every reference after the first run, so the size check alone proves
  nothing. Verified on Tales of Destiny DC by the TIM2 offset tables inside the decoded packs.
"""

from __future__ import annotations

import struct

import numpy as np

try:
    from numba import njit
except ImportError:  # pure Python: correct, just slow
    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f


@njit(cache=True)
def _tales_lzss(src, version, out_size):
    n_win = 4096
    text = np.zeros(n_win, np.uint8)
    r = n_win - 18 if version == 1 else n_win - 17
    out = np.empty(out_size + 4096, np.uint8)  # slack: a bad stream must not write past the end
    o = 0
    flags = 0
    p = 0
    n = src.shape[0]
    limit = out.shape[0]
    while p < n:
        flags >>= 1
        if (flags & 0x100) == 0:
            flags = src[p] | 0xFF00
            p += 1
            if p >= n:
                break
        if flags & 1:
            c = src[p]
            p += 1
            if o >= limit:
                return out[:0], -1
            out[o] = c
            o += 1
            text[r] = c
            r = (r + 1) & (n_win - 1)
            continue
        if p + 1 >= n:
            break
        i = np.int64(src[p])
        j = np.int64(src[p + 1])
        p += 2
        if version == 3 and (j & 0x0F) == 0x0F:
            k = j >> 4
            if k == 0:
                if p >= n:
                    break
                length = i + 19
                c = src[p]
                p += 1
            else:
                length = k + 3
                c = np.uint8(i)
            if o + length > limit:
                return out[:0], -1
            for _ in range(length):
                out[o] = c
                o += 1
                text[r] = c
                r = (r + 1) & (n_win - 1)
            continue
        pos = i | ((j & 0xF0) << 4)
        length = (j & 0x0F) + 3
        if o + length > limit:
            return out[:0], -1
        for k in range(length):
            c = text[(pos + k) & (n_win - 1)]
            out[o] = c
            o += 1
            text[r] = c
            r = (r + 1) & (n_win - 1)
    return out[:o], o


def tales_header(blob: bytes):
    """(version, compressed size, decompressed size) of a Tales-compressed blob, or None."""
    if len(blob) < 9 or blob[0] not in (0, 1, 3):
        return None
    comp, dec = struct.unpack_from("<II", blob, 1)
    return blob[0], comp, dec


def tales_lzss(data: bytes, version: int, out_size: int) -> bytes:
    """Decode Tales compression (data after the 9-byte header). Raises if the size is wrong."""
    if version == 0:
        return bytes(data[:out_size])
    out, n = _tales_lzss(np.frombuffer(data, np.uint8), version, out_size)
    if n != out_size:
        raise ValueError(f"tales_lzss: got {n} bytes, expected {out_size}")
    return out.tobytes()


def tales_unpack(blob: bytes) -> bytes | None:
    """A whole Tales-compressed blob (header + data) decoded, or None if it is not one."""
    h = tales_header(blob)
    if h is None:
        return None
    version, comp, dec = h
    if 9 + comp > len(blob) or dec == 0:
        return None
    try:
        return tales_lzss(blob[9 : 9 + comp], version, dec)
    except ValueError:
        return None
