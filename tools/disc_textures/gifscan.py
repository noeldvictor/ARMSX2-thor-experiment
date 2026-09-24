"""Find GS texture uploads and TEX0 writes inside raw game data (packets stored on the disc).

Some games keep their textures not as image files but as the GS packets that upload them: a GIF
A+D packet setting BITBLTBUF / TRXPOS / TRXREG / TRXDIR, then an IMAGE packet with the texels,
usually inside VIF DIRECT blocks of a model or scene file (Tales of Legendia's `.mcd`). The draws
that use them set TEX0 the same way. `events(buf)` scans every 16-byte aligned qword for those
register writes, so it works whatever container the packets sit in:

- ("upload", offset, Upload) - Upload as in gsdump.py (dbp, dbw, dpsm, x, y, w, h, data);
- ("tex0", offset, value) - a TEX0_1/TEX0_2 write (A+D address 0x06/0x07).

`textures(buf)` pairs them the way the GS would see them: each image upload with the CLUT
uploads before it and the first TEX0 after it that points at it, and yields the texture as the
draw reads it, through gsmem.GSMemory (so an upload made as PSMCT32 and drawn as PSMT8 comes out
as PSMT8 indices).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

import gsmem
from gsdump import BITS, Upload

A_D = 0xE
BITBLTBUF, TRXPOS, TRXREG, TRXDIR = 0x50, 0x51, 0x52, 0x53
TEX0_1, TEX0_2 = 0x06, 0x07
PSM_BY_ID = {0x00: gsmem.PSMCT32, 0x01: gsmem.PSMCT24, 0x02: gsmem.PSMCT16, 0x13: gsmem.PSMT8,
             0x14: gsmem.PSMT4, 0x1B: gsmem.PSMT8H, 0x24: gsmem.PSMT4HL, 0x2C: gsmem.PSMT4HH}


def events(buf: bytes):
    """Uploads and TEX0 writes in `buf`, in file order."""
    # Only qwords whose address half is one of the registers matter; numpy finds those, so a
    # 240 MB file (Tales of Rebirth's FLD.BIN) is not walked qword by qword in Python.
    qwords = np.frombuffer(buf, "<u8", len(buf) // 16 * 2).reshape(-1, 2)
    hits = np.nonzero(np.isin(qwords[:, 1], (BITBLTBUF, TRXPOS, TRXREG, TRXDIR, TEX0_1, TEX0_2)))[0]
    bitblt = trxpos = trxreg = None
    resume = 0
    for q in (int(i) * 16 for i in hits):
        if q < resume:
            continue  # inside an image just read
        value, addr = struct.unpack_from("<QQ", buf, q)
        reg = addr & 0xFF
        if addr >> 8 == 0 and reg in (BITBLTBUF, TRXPOS, TRXREG):
            if reg == BITBLTBUF:
                bitblt = value
            elif reg == TRXPOS:
                trxpos = value
            else:
                trxreg = value
        elif addr == TRXDIR and (value & 3) == 0 and bitblt is not None and trxreg is not None:
            # The IMAGE GIFtag follows (possibly after a VIF code or two): find it.
            dpsm = (bitblt >> 56) & 0x3F
            w, h = trxreg & 0xFFF, (trxreg >> 32) & 0xFFF
            need = w * h * BITS.get(dpsm, 32) // 8
            for skip in range(16, 64 + 1, 16):
                t = q + skip
                if t + 16 > len(buf):
                    break
                tag = struct.unpack_from("<Q", buf, t)[0]
                if (tag >> 58) & 3 == 2 and (tag & 0x7FFF) * 16 >= need:
                    data = buf[t + 16 : t + 16 + need]
                    if len(data) == need and w and h:
                        pos = trxpos or 0
                        yield ("upload", t + 16, Upload(dbp=(bitblt >> 32) & 0x3FFF, dbw=(bitblt >> 48) & 0x3F,
                                                        dpsm=dpsm, x=(pos >> 32) & 0x7FF, y=(pos >> 48) & 0x7FF,
                                                        w=w, h=h, data=data, frame=0))
                        resume = t + 16 + ((need + 15) // 16) * 16
                    break
            bitblt = trxpos = trxreg = None
        elif addr in (TEX0_1, TEX0_2) and ((value >> 20) & 0x3F) in PSM_BY_ID:
            yield ("tex0", q, value)


@dataclass
class Texture:
    tex0: int
    texels: np.ndarray  # HxW indices, or HxWx4 RGBA (PSMCT32)
    palettes: list[bytes]  # candidate CLUTs, 16 or 256 RGBA entries in index order; [] for true colour
    width: int
    height: int
    blob: bytes  # the upload bytes the texture came from, for a stable key


def _write(mem: gsmem.GSMemory, u: Upload) -> None:
    psm = PSM_BY_ID.get(u.dpsm)
    if psm != gsmem.PSMCT32:
        # Only 32-bit uploads are handled; every Legendia upload is one (textures ride along as
        # PSMCT32 whatever they are drawn as).
        raise NotImplementedError(f"upload PSM {u.dpsm:#x}")
    mem.write(psm, u.dbp, u.dbw, u.x, u.y, u.w, u.h, np.frombuffer(u.data, np.uint8).reshape(u.h, u.w, 4))


def textures(buf: bytes, clut_lookback: int = 4):
    """Each texture a draw in `buf` reads, as it reads it. See the module docstring."""
    evs = list(events(buf))
    uploads = [(i, e[2]) for i, e in enumerate(evs) if e[0] == "upload"]
    for k, (i, u) in enumerate(uploads):
        if u.dpsm != 0 or u.w * u.h <= 16 * 16 and k + 1 < len(uploads) and uploads[k + 1][1].dbp == u.dbp + 0x10:
            continue  # a CLUT (the texture follows at the next block) - taken with its texture below
        # The TEX0 that draws it: normally after the upload; a file that sets up the draw first
        # (Legendia's skit portraits) has it before.
        at = next((j for j in range(i + 1, len(evs)) if evs[j][0] == "tex0" and (evs[j][2] & 0x3FFF) == u.dbp), None)
        if at is None:
            at = next((j for j in range(i - 1, -1, -1) if evs[j][0] == "tex0" and (evs[j][2] & 0x3FFF) == u.dbp), None)
        if at is None:
            continue
        tex0 = evs[at][2]
        psm_id = (tex0 >> 20) & 0x3F
        tbw = (tex0 >> 14) & 0x3F
        tw, th = 1 << ((tex0 >> 26) & 0xF), 1 << ((tex0 >> 30) & 0xF)
        cbp, csa = (tex0 >> 37) & 0x3FFF, (tex0 >> 56) & 0x1F
        psm = PSM_BY_ID[psm_id]
        mem = gsmem.GSMemory()
        try:
            _write(mem, u)
        except NotImplementedError:
            continue
        bits = BITS.get(psm_id, 32)
        # The image is what the upload covers, read the way the draw reads it - found through the
        # GS layout, not by arithmetic: a 16x16 PSMCT32 upload read as PSMT8 is a 32x32 square
        # (the block tables differ), not 64x16. Mark the uploaded memory and read the mask back.
        # Not capped at TEX0's size: one upload can hold several textures the game draws from
        # later TBPs (a skit's 256x512 sheet of two 256x256 portraits), and each is a crop of it.
        mark = gsmem.GSMemory()
        mark.write(gsmem.PSMCT32, u.dbp, u.dbw, u.x, u.y, u.w, u.h, b"\xff" * (u.w * u.h * 4))
        span_w = min(max(1, tbw) * 64, 1024)
        span_h = min(1024, max(1, len(u.data) * 8 // bits // max(1, span_w)) * 2 + 64)
        covered = np.asarray(mark.read(psm, u.dbp, max(1, tbw), 0, 0, span_w, span_h))
        covered = covered != 0 if covered.ndim == 2 else covered[..., 0] != 0
        ys, xs = np.nonzero(covered)
        if len(xs) == 0 or ys.min() != 0 or xs.min() != 0:
            continue
        width, height = int(xs.max()) + 1, int(ys.max()) + 1
        texels = np.asarray(mem.read(psm, u.dbp, max(1, tbw), 0, 0, width, height))
        palettes: list[bytes] = []
        if psm_id != 0:
            # Which CLUT the draw sees depends on the order the GS runs the packets in, which the
            # file order need not be (DMA chains call packets). Legendia's models upload the CLUT
            # before the image, its portraits and small 4-bit textures after it. So both
            # candidates are kept - the last CLUT covering CBP before the image, and the last one
            # between the image and the TEX0 - and the exact match picks the right one.
            def covers(c: Upload) -> bool:
                return c is not u and c.dpsm == 0 and c.dbp <= cbp < c.dbp + 0x20

            window = uploads[max(0, k - clut_lookback) : k + clut_lookback + 1]
            before = [c for j, c in window if j < i and covers(c)]
            after = [c for j, c in window if i < j < at and covers(c)]
            for clut in ([before[-1]] if before else []) + ([after[-1]] if after else []):
                cmem = gsmem.GSMemory()
                _write(cmem, clut)
                if bits == 8:
                    raw = np.asarray(cmem.read(gsmem.PSMCT32, cbp, 1, 0, 0, 16, 16)).reshape(-1, 4).astype(np.uint8)
                    from tim2 import csm1_unswizzle
                    pal = csm1_unswizzle(raw).tobytes()
                else:
                    # CSM1, 16 colours: an 8x2 patch; CSA picks one of the four in a block.
                    x0, y0 = (csa & 1) * 8, ((csa >> 1) & 3) * 2
                    pal = np.asarray(cmem.read(gsmem.PSMCT32, cbp, 1, x0, y0, 8, 2)).astype(np.uint8).tobytes()
                if pal not in palettes:
                    palettes.append(pal)
            if not palettes:
                continue
        yield Texture(tex0=tex0, texels=texels.astype(np.uint8), palettes=palettes, width=width, height=height,
                      blob=u.data)
