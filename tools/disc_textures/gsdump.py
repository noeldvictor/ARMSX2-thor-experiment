"""Read a PCSX2 GS dump (`.gs.zst` / `.gs.xz` / `.gs`) and list the texture uploads in it.

    python gsdump.py DUMP.gs.zst            # summary of the uploads
    python gsdump.py DUMP.gs.zst --locate FILES_DIR_OR_BLOB

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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", type=Path)
    ap.add_argument("--locate", type=Path, help="a file (or folder of files) to search for each upload's bytes")
    ap.add_argument("--min", type=int, default=256, help="ignore uploads smaller than this many bytes")
    a = ap.parse_args()
    header, _, _ = parse(a.dump)
    print(f"{a.dump.name}: {header['serial']} CRC {header['crc']:08X}")
    ups = [u for u in uploads(a.dump) if len(u.data) >= a.min]
    # The same texture is often uploaded every frame: report each distinct payload once.
    distinct: dict[bytes, tuple[Upload, int]] = {}
    for u in ups:
        first, n = distinct.get(u.data, (u, 0))
        distinct[u.data] = (first, n + 1)
    corpus = starts = names = None
    if a.locate:
        import bisect
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
