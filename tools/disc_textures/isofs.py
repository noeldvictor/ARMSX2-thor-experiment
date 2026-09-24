"""A minimal ISO 9660 reader: list and read the files of a PS2 disc image without pycdlib.

    python isofs.py GAME.iso            # every file, biggest first, with its first bytes

pycdlib refuses images whose UDF descriptors are damaged ("Expected at least 2 UDF Anchors") -
Tales of Legendia's ReUndub build and Tales of Rebirth's English patch are like that: the patching
tools rewrite the ISO 9660 side and leave the UDF bridge stale. A PS2 only reads ISO 9660, so this
reads just that: the primary volume descriptor at sector 16, then the directory records from the
root. Plain 2048-byte sectors (what disc.py writes).

`files(iso)` -> {"/PATH/NAME": (lba, size)}; `read(iso, path)` -> bytes; `open_file(iso, path)`
-> (file object positioned at the file, size) for files too big to hold in memory.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

SECTOR = 2048


def _records(f, lba: int, size: int):
    f.seek(lba * SECTOR)
    data = f.read(size)
    pos = 0
    while pos < len(data):
        length = data[pos]
        if length == 0:  # records do not cross sectors: skip to the next one
            pos = (pos // SECTOR + 1) * SECTOR
            continue
        rec = data[pos : pos + length]
        ext_lba = struct.unpack_from("<I", rec, 2)[0]
        ext_size = struct.unpack_from("<I", rec, 10)[0]
        flags = rec[25]
        name_len = rec[32]
        name = rec[33 : 33 + name_len]
        yield name, ext_lba, ext_size, bool(flags & 2)
        pos += length


def files(iso: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    with open(iso, "rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        if pvd[1:6] != b"CD001":
            raise ValueError(f"{iso}: no ISO 9660 primary volume descriptor at sector 16")
        root_lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
        root_size = struct.unpack_from("<I", pvd, 156 + 10)[0]
        todo = [("", root_lba, root_size)]
        seen = set()
        while todo:
            prefix, lba, size = todo.pop()
            if lba in seen:
                continue
            seen.add(lba)
            for name, ext_lba, ext_size, is_dir in _records(f, lba, size):
                if name in (b"\x00", b"\x01"):
                    continue
                text = name.decode("ascii", "replace").split(";")[0]
                if is_dir:
                    todo.append((f"{prefix}/{text}", ext_lba, ext_size))
                else:
                    out[f"{prefix}/{text}"] = (ext_lba, ext_size)
    return out


def open_file(iso: Path, path: str):
    lba, size = files(iso)[path]
    f = open(iso, "rb")
    f.seek(lba * SECTOR)
    return f, size


def read(iso: Path, path: str) -> bytes:
    f, size = open_file(iso, path)
    with f:
        return f.read(size)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("iso", type=Path)
    a = ap.parse_args()
    listing = files(a.iso)
    with open(a.iso, "rb") as f:
        for path, (lba, size) in sorted(listing.items(), key=lambda kv: -kv[1][1]):
            f.seek(lba * SECTOR)
            head = f.read(16).hex(" ")
            print(f"{size / 1e6:10.1f} MB  {path}  {head}")
    print(f"{len(listing)} files, {sum(s for _, s in listing.values()) / 1e9:.2f} GB")


if __name__ == "__main__":
    main()
