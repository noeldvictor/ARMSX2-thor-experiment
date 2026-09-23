"""Read a PCSX2 GS dump (`.gs.zst` / `.gs.xz` / `.gs`) and list the texture uploads in it.

    python gsdump.py DUMP.gs.zst            # summary of the uploads
    python gsdump.py DUMP.gs.zst --locate FILES_DIR_OR_BLOB
    python gsdump.py DUMP.gs.zst --timeline [--tbp 0x1a40-0x2040]   # uploads and draws in order

A GS dump is what the GS received for one or more frames: a header, the GS state (VRAM
included), the privileged registers, then packets - 0 transfer (u8 path, u32 size, GIF data),
1 vsync (u8 field), 2 FIFO readback (u32 size), 3 registers (0x2000 bytes). Transfers are GIF
packets; an upload is a BITBLTBUF/TRXPOS/TRXREG/TRXDIR register write followed by IMAGE data.

`uploads(path)` yields each upload: destination block pointer, buffer width, PSM, rectangle and
the bytes the game sent. Those bytes are exactly what the game read from its disc files (after
decompression) in the vast majority of games, so searching the decompressed disc for them is the
fastest way to learn where a game keeps its textures and in what format (see --locate).
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path

PSM_NAMES = {0x00: "CT32", 0x01: "CT24", 0x02: "CT16", 0x0A: "CT16S", 0x13: "T8", 0x14: "T4", 0x1B: "T8H",
             0x24: "T4HL", 0x2C: "T4HH", 0x30: "Z32", 0x31: "Z24", 0x32: "Z16", 0x3A: "Z16S"}
BITS = {0x00: 32, 0x01: 24, 0x02: 16, 0x0A: 16, 0x13: 8, 0x14: 4, 0x1B: 8, 0x24: 4, 0x2C: 4, 0x30: 32,
        0x31: 24, 0x32: 16, 0x3A: 16}


@dataclass
class Upload:
    dbp: int
    dbw: int
    dpsm: int
    x: int
    y: int
    w: int
    h: int
    data: bytes
    frame: int

    @property
    def psm_name(self) -> str:
        return PSM_NAMES.get(self.dpsm, hex(self.dpsm))


def read_dump(path: Path) -> bytes:
    raw = path.read_bytes()
    if path.suffix == ".zst":
        import zstandard
        return zstandard.ZstdDecompressor().stream_reader(raw).read()
    if path.suffix == ".xz":
        import lzma
        return lzma.decompress(raw)
    return raw


def parse(path: Path):
    """(header dict, state bytes, iterator over packets (kind, path, payload))."""
    d = read_dump(path)
    magic, header_size = struct.unpack_from("<II", d, 0)
    if magic != 0xFFFFFFFF:
        raise ValueError("not a GS dump (old format?)")
    (state_version, state_size, serial_off, serial_size, crc, sw, sh, shot_off, shot_size) = struct.unpack_from("<9I", d, 8)
    hdr_base = 8
    serial = d[hdr_base + serial_off: hdr_base + serial_off + serial_size].decode("ascii", "replace")
    p = 8 + header_size
    state = d[p: p + state_size]
    p += state_size + 0x2000
    header = {"serial": serial, "crc": crc, "state_version": state_version, "state_size": state_size}

    def packets():
        q = p
        n = len(d)
        while q < n:
            kind = d[q]
            q += 1
            if kind == 0:
                idx = d[q]
                size = struct.unpack_from("<I", d, q + 1)[0]
                q += 5
                yield 0, idx, d[q: q + size]
                q += size
            elif kind == 1:
                yield 1, d[q], b""
                q += 1
            elif kind == 2:
                q += 4
            elif kind == 3:
                q += 0x2000
            else:
                raise ValueError(f"unknown packet {kind} at {q - 1}")

    return header, state, packets()


def uploads(path: Path):
    """Every host->local image transfer in the dump, in order."""
    _, _, packets = parse(path)
    bitblt = trxpos = trxreg = 0
    frame = 0
    pending = None  # Upload being filled by IMAGE data
    for kind, _path, data in packets:
        if kind == 1:
            frame += 1
            continue
        if kind != 0:
            continue
        q = 0
        n = len(data)
        while q + 16 <= n:
            lo, hi = struct.unpack_from("<QQ", data, q)
            q += 16
            nloop = lo & 0x7FFF
            flg = (lo >> 58) & 3
            nreg = (lo >> 60) & 0xF or 16
            regs = [(hi >> (4 * k)) & 0xF for k in range(nreg)]
            if flg == 0:  # PACKED
                for _ in range(nloop):
                    for r in regs:
                        if q + 16 > n:
                            break
                        if r == 0xE:  # A+D
                            val, addr = struct.unpack_from("<QQ", data, q)
                            addr &= 0xFF
                            if addr == 0x50:
                                bitblt = val
                            elif addr == 0x51:
                                trxpos = val
                            elif addr == 0x52:
                                trxreg = val
                            elif addr == 0x53 and (val & 3) == 0:
                                if pending is not None and pending.data:
                                    yield pending
                                pending = Upload(dbp=(bitblt >> 32) & 0x3FFF, dbw=(bitblt >> 48) & 0x3F,
                                                 dpsm=(bitblt >> 56) & 0x3F, x=(trxpos >> 32) & 0x7FF,
                                                 y=(trxpos >> 48) & 0x7FF, w=trxreg & 0xFFF,
                                                 h=(trxreg >> 32) & 0xFFF, data=b"", frame=frame)
                        q += 16
            elif flg == 1:  # REGLIST: 8 bytes a register
                q += ((nloop * nreg + 1) // 2) * 16
            else:  # IMAGE
                size = nloop * 16
                if pending is not None:
                    pending.data += data[q: q + size]
                    need = pending.w * pending.h * BITS.get(pending.dpsm, 32) // 8
                    if len(pending.data) >= need:
                        pending.data = pending.data[:need]
                        yield pending
                        pending = None
                q += size
    if pending is not None and pending.data:
        yield pending


def timeline(path: Path):
    """Uploads and textured draws in the order the GS got them: ("upload", dbp, dbw, psm, w, h) and
    ("draw", TEX0, CLAMP, vertices), consecutive vertices with the same TEX0/CLAMP merged. Answers
    "what is this upload drawn as" - Tales of Destiny's 32-bit title uploads looked like 8-bit data
    in disguise until the timeline showed them drawn as PSMCT32."""
    _, _, packets = parse(path)
    tex0, clamp, prim, bitblt, trxreg = [0, 0], [0, 0], 0, 0, 0
    events: list = []

    def write(addr: int, val: int) -> None:
        nonlocal prim, bitblt, trxreg
        if addr == 0x00:
            prim = val
        elif addr in (0x06, 0x07):
            tex0[addr - 0x06] = val
        elif addr in (0x08, 0x09):
            clamp[addr - 0x08] = val
        elif addr == 0x50:
            bitblt = val
        elif addr == 0x52:
            trxreg = val
        elif addr == 0x53 and (val & 3) == 0:
            events.append(("upload", (bitblt >> 32) & 0x3FFF, (bitblt >> 48) & 0x3F, (bitblt >> 56) & 0x3F,
                           trxreg & 0xFFF, (trxreg >> 32) & 0xFFF))

    def kick() -> None:
        if not (prim >> 4) & 1:  # untextured
            return
        ctx = (prim >> 9) & 1
        t, c = tex0[ctx] & ((1 << 62) - 1), clamp[ctx]
        if events and events[-1][0] == "draw" and events[-1][1] == t and events[-1][2] == c:
            events[-1] = ("draw", t, c, events[-1][3] + 1)
        else:
            events.append(("draw", t, c, 1))

    for kind, _path, data in packets:
        if kind != 0:
            continue
        q, n = 0, len(data)
        while q + 16 <= n:
            lo, hi = struct.unpack_from("<QQ", data, q)
            q += 16
            nloop = lo & 0x7FFF
            if (lo >> 46) & 1:  # PRE: the tag sets PRIM
                prim = (lo >> 47) & 0x7FF
            flg = (lo >> 58) & 3
            nreg = (lo >> 60) & 0xF or 16
            regs = [(hi >> (4 * k)) & 0xF for k in range(nreg)]
            if flg == 0:  # PACKED
                for _ in range(nloop):
                    for r in regs:
                        if q + 16 > n:
                            break
                        a, b = struct.unpack_from("<QQ", data, q)
                        q += 16
                        if r == 0xE:  # A+D
                            write(b & 0xFF, a)
                            if (b & 0xFF) in (0x04, 0x05):
                                kick()
                        elif r in (0x6, 0x7, 0x8, 0x9):
                            write(r, a)
                        elif r in (0x4, 0x5) and not (b >> 47) & 1:  # XYZF2/XYZ2 without ADC
                            kick()
            elif flg == 1:  # REGLIST: 8 bytes a register
                for k in range(nloop * nreg):
                    r = regs[k % nreg]
                    v = struct.unpack_from("<Q", data, q + 8 * k)[0]
                    if r in (0x0, 0x6, 0x7, 0x8, 0x9):
                        write(r, v)
                    elif r in (0x4, 0x5):
                        kick()
                q += ((nloop * nreg + 1) // 2) * 16
            else:  # IMAGE
                q += nloop * 16
    return events


def print_timeline(path: Path, tbp: tuple[int, int] | None) -> None:
    def near(bp: int) -> bool:
        return tbp is None or tbp[0] <= bp < tbp[1]

    last, repeat = None, 0
    lines = []
    for ev in timeline(path):
        if ev[0] == "upload":
            _, dbp, dbw, psm, w, h = ev
            if not near(dbp):
                continue
            line = f"upload {PSM_NAMES.get(psm, hex(psm)):5} dbp {dbp:#06x} bw {dbw:2} {w}x{h}"
        else:
            _, t, c, verts = ev
            bp = t & 0x3FFF
            if not near(bp):
                continue
            psm, tw, th = (t >> 20) & 0x3F, 1 << ((t >> 26) & 0xF), 1 << ((t >> 30) & 0xF)
            clut = f" cbp {(t >> 37) & 0x3FFF:#06x} csa {(t >> 56) & 0x1F}" if psm in (0x13, 0x14, 0x1B, 0x24, 0x2C) else ""
            region = ""
            if c & 3 == 2 or (c >> 2) & 3 == 2:
                region = f" region u {(c >> 4) & 0x3FF}-{(c >> 14) & 0x3FF} v {(c >> 24) & 0x3FF}-{(c >> 34) & 0x3FF}"
            line = (f"draw   {PSM_NAMES.get(psm, hex(psm)):5} tbp {bp:#06x} bw {(t >> 14) & 0x3F:2} {tw}x{th}"
                    f"{clut}{region} ({verts} vertices)")
        if line == last:
            repeat += 1
            continue
        if last is not None:
            lines.append(last + (f"  x{repeat + 1}" if repeat else ""))
        last, repeat = line, 0
    if last is not None:
        lines.append(last + (f"  x{repeat + 1}" if repeat else ""))
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", type=Path)
    ap.add_argument("--locate", type=Path, help="a file (or folder of files) to search for each upload's bytes; "
                                                "a concatenated corpus X.bin with an X.json of names/starts works too")
    ap.add_argument("--min", type=int, default=256, help="ignore uploads smaller than this many bytes")
    ap.add_argument("--timeline", action="store_true", help="uploads and textured draws in order (see timeline())")
    ap.add_argument("--tbp", help="with --timeline: only this block range, e.g. 0x1a40-0x2040")
    a = ap.parse_args()
    header, _, _ = parse(a.dump)
    print(f"{a.dump.name}: {header['serial']} CRC {header['crc']:08X}")
    if a.timeline:
        rng = tuple(int(v, 16) for v in a.tbp.split("-")) if a.tbp else None
        print_timeline(a.dump, rng)
        return
    ups = [u for u in uploads(a.dump) if len(u.data) >= a.min]
    # The same texture is often uploaded every frame: report each distinct payload once.
    distinct: dict[bytes, tuple[Upload, int]] = {}
    for u in ups:
        first, n = distinct.get(u.data, (u, 0))
        distinct[u.data] = (first, n + 1)
    corpus = starts = names = None
    import bisect
    sidecar = a.locate.with_suffix(".json") if a.locate and a.locate.is_file() else None
    if sidecar and sidecar.exists():
        # A corpus made once from many small files (searching 100k files one by one is slow).
        import json
        index = json.loads(sidecar.read_text())
        names, starts = index["names"], index["starts"]
        corpus = a.locate.read_bytes()
    elif a.locate:
        files = sorted(a.locate.rglob("*")) if a.locate.is_dir() else [a.locate]
        parts, starts, names, pos = [], [], [], 0
        for f in files:
            if f.is_file():
                b = f.read_bytes()
                parts.append(b)
                starts.append(pos)
                names.append(f.name)
                pos += len(b)
        corpus = b"".join(parts)
    for data, (u, n) in distinct.items():
        where = ""
        if corpus is not None:
            # Probe with a stretch that is not all one byte, so a run of zeros does not "match".
            probe = data[:64]
            for k in range(0, len(data) - 64, 64):
                if len(set(data[k: k + 64])) > 4:
                    probe = data[k: k + 64]
                    break
            hits = []
            at = corpus.find(probe)
            while at >= 0 and len(hits) < 3:
                i = bisect.bisect_right(starts, at) - 1
                hits.append((names[i], at - starts[i] - data.find(probe)))
                at = corpus.find(probe, at + 1)
            where = f"  found in {hits}" if hits else "  not found"
        print(f"x{n:<3} {u.psm_name:5} dbp {u.dbp:#06x} bw {u.dbw:2} {u.w}x{u.h} at {u.x},{u.y} "
              f"{len(data)} bytes{where}")
    print(f"{len(ups)} uploads of {a.min}+ bytes, {len(distinct)} distinct")


if __name__ == "__main__":
    main()
