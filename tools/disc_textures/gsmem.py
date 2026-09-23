"""The GS's local memory layout: write and read rectangles in any texture format, like the GS does.

Games often upload a texture in one format and draw it in another - most commonly an 8- or 4-bit
texture sent as PSMCT32 data (fewer, bigger transfers), so what is on the disc is the 32-bit view
of the memory and only the GS's swizzle turns it into indices. `GSMemory` reproduces that:

    mem = GSMemory()
    mem.write(PSMCT32, dbp, dbw, x, y, w, h, data)   # as the upload did
    indices = mem.read(PSMT8, tbp, tbw, 0, 0, tw, th)  # as the draw samples

It also reads VRAM out of a GS dump's state (`GSMemory.from_dump`), which is how palettes that
stay resident (loaded before the dumped frame) are found.

Layout (PCSX2 GSTables.cpp, which this copies): memory is 8 KB pages of 32 256-byte blocks; a
page is 64x32 texels for PSMCT32, 64x64 for PSMCT16, 128x64 for PSMT8, 128x128 for PSMT4. A page
index is `bp / 32 + (y / page height) * pages across + x / page width`, where pages across is the
buffer width in 64-texel units (halved for 8/4-bit formats, whose pages are 128 wide). Blocks sit
in the page by the format's block table; texels sit in a block by its column table. PSMT8H / PSMT4HL /
PSMT4HH / PSMCT24 are PSMCT32 words read partially.
"""

from __future__ import annotations

import numpy as np

PSMCT32, PSMCT24, PSMCT16, PSMCT16S = 0x00, 0x01, 0x02, 0x0A
PSMT8, PSMT4, PSMT8H, PSMT4HL, PSMT4HH = 0x13, 0x14, 0x1B, 0x24, 0x2C

BLOCK32 = np.array([[0, 1, 4, 5, 16, 17, 20, 21], [2, 3, 6, 7, 18, 19, 22, 23],
                    [8, 9, 12, 13, 24, 25, 28, 29], [10, 11, 14, 15, 26, 27, 30, 31]])
BLOCK16 = np.array([[0, 2, 8, 10], [1, 3, 9, 11], [4, 6, 12, 14], [5, 7, 13, 15],
                    [16, 18, 24, 26], [17, 19, 25, 27], [20, 22, 28, 30], [21, 23, 29, 31]])
BLOCK16S = np.array([[0, 2, 16, 18], [1, 3, 17, 19], [8, 10, 24, 26], [9, 11, 25, 27],
                     [4, 6, 20, 22], [5, 7, 21, 23], [12, 14, 28, 30], [13, 15, 29, 31]])
BLOCK8 = BLOCK32
BLOCK4 = BLOCK16

COL32 = np.array([[0, 1, 4, 5, 8, 9, 12, 13], [2, 3, 6, 7, 10, 11, 14, 15],
                  [16, 17, 20, 21, 24, 25, 28, 29], [18, 19, 22, 23, 26, 27, 30, 31],
                  [32, 33, 36, 37, 40, 41, 44, 45], [34, 35, 38, 39, 42, 43, 46, 47],
                  [48, 49, 52, 53, 56, 57, 60, 61], [50, 51, 54, 55, 58, 59, 62, 63]])
COL16 = np.array([
    [0, 2, 8, 10, 16, 18, 24, 26, 1, 3, 9, 11, 17, 19, 25, 27],
    [4, 6, 12, 14, 20, 22, 28, 30, 5, 7, 13, 15, 21, 23, 29, 31],
    [32, 34, 40, 42, 48, 50, 56, 58, 33, 35, 41, 43, 49, 51, 57, 59],
    [36, 38, 44, 46, 52, 54, 60, 62, 37, 39, 45, 47, 53, 55, 61, 63],
    [64, 66, 72, 74, 80, 82, 88, 90, 65, 67, 73, 75, 81, 83, 89, 91],
    [68, 70, 76, 78, 84, 86, 92, 94, 69, 71, 77, 79, 85, 87, 93, 95],
    [96, 98, 104, 106, 112, 114, 120, 122, 97, 99, 105, 107, 113, 115, 121, 123],
    [100, 102, 108, 110, 116, 118, 124, 126, 101, 103, 109, 111, 117, 119, 125, 127]])
COL8 = np.array([
    [0, 4, 16, 20, 32, 36, 48, 52, 2, 6, 18, 22, 34, 38, 50, 54],
    [8, 12, 24, 28, 40, 44, 56, 60, 10, 14, 26, 30, 42, 46, 58, 62],
    [33, 37, 49, 53, 1, 5, 17, 21, 35, 39, 51, 55, 3, 7, 19, 23],
    [41, 45, 57, 61, 9, 13, 25, 29, 43, 47, 59, 63, 11, 15, 27, 31],
    [96, 100, 112, 116, 64, 68, 80, 84, 98, 102, 114, 118, 66, 70, 82, 86],
    [104, 108, 120, 124, 72, 76, 88, 92, 106, 110, 122, 126, 74, 78, 90, 94],
    [65, 69, 81, 85, 97, 101, 113, 117, 67, 71, 83, 87, 99, 103, 115, 119],
    [73, 77, 89, 93, 105, 109, 121, 125, 75, 79, 91, 95, 107, 111, 123, 127],
    [128, 132, 144, 148, 160, 164, 176, 180, 130, 134, 146, 150, 162, 166, 178, 182],
    [136, 140, 152, 156, 168, 172, 184, 188, 138, 142, 154, 158, 170, 174, 186, 190],
    [161, 165, 177, 181, 129, 133, 145, 149, 163, 167, 179, 183, 131, 135, 147, 151],
    [169, 173, 185, 189, 137, 141, 153, 157, 171, 175, 187, 191, 139, 143, 155, 159],
    [224, 228, 240, 244, 192, 196, 208, 212, 226, 230, 242, 246, 194, 198, 210, 214],
    [232, 236, 248, 252, 200, 204, 216, 220, 234, 238, 250, 254, 202, 206, 218, 222],
    [193, 197, 209, 213, 225, 229, 241, 245, 195, 199, 211, 215, 227, 231, 243, 247],
    [201, 205, 217, 221, 233, 237, 249, 253, 203, 207, 219, 223, 235, 239, 251, 255]])


def _col4() -> np.ndarray:
    """PCSX2's columnTable4 (16 x 32 nibble offsets), built from its four-column pattern."""
    base = [
        [0, 8, 32, 40, 64, 72, 96, 104, 2, 10, 34, 42, 66, 74, 98, 106,
         4, 12, 36, 44, 68, 76, 100, 108, 6, 14, 38, 46, 70, 78, 102, 110],
        [16, 24, 48, 56, 80, 88, 112, 120, 18, 26, 50, 58, 82, 90, 114, 122,
         20, 28, 52, 60, 84, 92, 116, 124, 22, 30, 54, 62, 86, 94, 118, 126],
        [65, 73, 97, 105, 1, 9, 33, 41, 67, 75, 99, 107, 3, 11, 35, 43,
         69, 77, 101, 109, 5, 13, 37, 45, 71, 79, 103, 111, 7, 15, 39, 47],
        [81, 89, 113, 121, 17, 25, 49, 57, 83, 91, 115, 123, 19, 27, 51, 59,
         85, 93, 117, 125, 21, 29, 53, 61, 87, 95, 119, 127, 23, 31, 55, 63],
    ]
    odd = [  # columns 1 and 3 swap the halves, as in the table
        [192, 200, 224, 232, 128, 136, 160, 168, 194, 202, 226, 234, 130, 138, 162, 170,
         196, 204, 228, 236, 132, 140, 164, 172, 198, 206, 230, 238, 134, 142, 166, 174],
        [208, 216, 240, 248, 144, 152, 176, 184, 210, 218, 242, 250, 146, 154, 178, 186,
         212, 220, 244, 252, 148, 156, 180, 188, 214, 222, 246, 254, 150, 158, 182, 190],
        [129, 137, 161, 169, 193, 201, 225, 233, 131, 139, 163, 171, 195, 203, 227, 235,
         133, 141, 165, 173, 197, 205, 229, 237, 135, 143, 167, 175, 199, 207, 231, 239],
        [145, 153, 177, 185, 209, 217, 241, 249, 147, 155, 179, 187, 211, 219, 243, 251,
         149, 157, 181, 189, 213, 221, 245, 253, 151, 159, 183, 191, 215, 223, 247, 255],
    ]
    rows = []
    for col in range(4):
        src = base if col % 2 == 0 else odd
        add = 256 * (col // 2)
        rows += [[v + add for v in r] for r in src]
    return np.array(rows)


COL4 = _col4()

# psm: (page w, page h, block table, block w, block h, column table, texel bits, pages-across divisor)
FORMATS = {
    PSMCT32: (64, 32, BLOCK32, 8, 8, COL32, 32, 1),
    PSMCT16: (64, 64, BLOCK16, 16, 8, COL16, 16, 1),
    PSMCT16S: (64, 64, BLOCK16S, 16, 8, COL16, 16, 1),
    PSMT8: (128, 64, BLOCK8, 16, 16, COL8, 8, 2),
    PSMT4: (128, 128, BLOCK4, 32, 16, COL4, 4, 2),
}


def _addresses(psm: int, bp: int, bw: int, x0: int, y0: int, w: int, h: int) -> np.ndarray:
    """Texel address of every texel in the rectangle, in units of the format's texel (bytes for
    PSMT8, nibbles for PSMT4, 16-bit words for PSMCT16, 32-bit words for PSMCT32)."""
    pw, ph, block, bwid, bhgt, col, bits, div = FORMATS[psm]
    ys, xs = np.mgrid[y0:y0 + h, x0:x0 + w]
    across = max(bw // div, 1)
    page = (ys // ph) * across + (xs // pw)
    blk = block[(ys % ph) // bhgt, (xs % pw) // bwid]
    bn = (bp + page * 32 + blk) & 0x3FFF  # 16384 blocks in 4 MB
    texels_per_block = 2048 // bits
    return bn.astype(np.int64) * texels_per_block + col[ys % bhgt, xs % bwid]


class GSMemory:
    SIZE = 4 * 1024 * 1024

    def __init__(self, vram: bytes | None = None):
        self.vm = np.frombuffer(bytearray(vram) if vram else bytearray(self.SIZE), np.uint8).copy()

    @classmethod
    def from_dump(cls, state: bytes) -> "GSMemory":
        """VRAM out of a GS dump's state blob (gsdump.parse): the last 4 MB before the four GIF
        paths (tag + register each) and Q."""
        tail = 4 * (16 + 4) + 4
        start = len(state) - tail - cls.SIZE
        return cls(state[start:start + cls.SIZE])

    def write(self, psm: int, bp: int, bw: int, x: int, y: int, w: int, h: int, data: bytes) -> None:
        a = _addresses(_base(psm), bp, bw, x, y, w, h)
        src = np.frombuffer(data, np.uint8)
        if psm in (PSMCT32,):
            self.vm.view("<u4")[a.ravel()] = src[: w * h * 4].view("<u4")
        elif psm == PSMCT24:
            words = self.vm.view("<u4")
            rgb = src[: w * h * 3].reshape(-1, 3).astype(np.uint32)
            idx = a.ravel()
            words[idx] = (words[idx] & 0xFF000000) | rgb[:, 0] | (rgb[:, 1] << 8) | (rgb[:, 2] << 16)
        elif psm in (PSMCT16, PSMCT16S):
            self.vm.view("<u2")[a.ravel()] = src[: w * h * 2].view("<u2")
        elif psm == PSMT8:
            self.vm[a.ravel()] = src[: w * h]
        elif psm == PSMT4:
            nib = np.empty(w * h, np.uint8)
            nib[0::2] = src[: (w * h + 1) // 2] & 0x0F
            nib[1::2] = src[: w * h // 2] >> 4
            self._put_nibbles(a.ravel(), nib)
        else:
            raise NotImplementedError(f"write PSM {psm:#x}")

    def read(self, psm: int, bp: int, bw: int, x: int, y: int, w: int, h: int) -> np.ndarray:
        """HxW indices (palette formats) or HxWx4 RGBA bytes (PSMCT32/24; 24 leaves alpha as stored)."""
        a = _addresses(_base(psm), bp, bw, x, y, w, h)
        if psm in (PSMCT32, PSMCT24):
            return self.vm.view("<u4")[a].view(np.uint8).reshape(h, w, 4)
        if psm == PSMT8:
            return self.vm[a]
        if psm == PSMT4:
            b = self.vm[a >> 1]
            return np.where(a & 1, b >> 4, b & 0x0F).astype(np.uint8)
        if psm == PSMT8H:
            return (self.vm.view("<u4")[a] >> 24).astype(np.uint8)
        if psm == PSMT4HL:
            return ((self.vm.view("<u4")[a] >> 24) & 0x0F).astype(np.uint8)
        if psm == PSMT4HH:
            return (self.vm.view("<u4")[a] >> 28).astype(np.uint8)
        raise NotImplementedError(f"read PSM {psm:#x}")

    def _put_nibbles(self, addr: np.ndarray, nib: np.ndarray) -> None:
        byte = addr >> 1
        hi = (addr & 1).astype(bool)
        cur = self.vm[byte]
        self.vm[byte[~hi]] = (cur[~hi] & 0xF0) | nib[~hi]
        cur = self.vm[byte]
        self.vm[byte[hi]] = (cur[hi] & 0x0F) | (nib[hi] << 4)


def _base(psm: int) -> int:
    """The layout a format uses: the H formats and PSMCT24 are PSMCT32 words."""
    return PSMCT32 if psm in (PSMCT24, PSMT8H, PSMT4HL, PSMT4HH) else psm
