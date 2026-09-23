"""Turn a PS2 disc image into a plain ISO 9660 file the extractors can read with pycdlib.

    python disc.py GAME.chd|GAME.cue|GAME.iso OUT_DIR [--chdman PATH]

- `.iso`: used as it is.
- `.chd`: MAME's `chdman` extracts it - `extractdvd` for a DVD CHD (most PS2 games; already
  2048-byte sectors), `extractcd` for a CD CHD (the `CHT2` metadata tag), which then goes through
  the `.cue` step.
- `.cue` / `.bin`: raw 2352-byte sectors cut to their 2048 user bytes (offset 24 in MODE2, 16 in
  MODE1). Only the first track is read; PS2 CDs keep their data there.

Prints the ISO path and its SHA-1 (the recipe's `game.json` records it, so a different dump of
the same game shows up before hours of upscaling).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
from pathlib import Path


def find_chdman(given: str | None) -> str:
    for c in (given, "chdman"):
        if c and Path(c).is_file():
            return str(c)
        if c and shutil.which(c):
            return shutil.which(c)
    raise SystemExit("chdman not found: put MAME's chdman on PATH or pass --chdman PATH")


def cue_to_iso(cue: Path, iso: Path) -> Path:
    text = cue.read_text(errors="replace")
    track = re.search(r"TRACK\s+\d+\s+(MODE[12])/(\d+)", text)
    if not track:
        raise SystemExit(f"{cue}: no data track")
    mode, sector = track.group(1), int(track.group(2))
    bin_name = re.search(r'FILE\s+"([^"]+)"', text).group(1)
    src = cue.parent / bin_name
    if sector == 2048:
        shutil.copyfile(src, iso)
        return iso
    offset = 24 if mode == "MODE2" else 16
    with open(src, "rb") as f, open(iso, "wb") as o:
        while chunk := f.read(sector * 4096):
            for i in range(0, len(chunk) - sector + 1, sector):
                o.write(chunk[i + offset : i + offset + 2048])
    return iso


def to_iso(disc: Path, out: Path, chdman: str | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    suffix = disc.suffix.lower()
    if suffix == ".iso":
        return disc
    iso = out / "disc.iso"
    if suffix in (".cue", ".bin"):
        return cue_to_iso(disc.with_suffix(".cue") if suffix == ".bin" else disc, iso)
    if suffix != ".chd":
        raise SystemExit(f"{disc}: expected .chd, .cue or .iso")
    tool = find_chdman(chdman)
    info = subprocess.run([tool, "info", "-i", str(disc)], capture_output=True, text=True).stdout
    if "CHT2" in info or "CHTR" in info or "CHCD" in info:
        cue = out / "disc.cue"
        subprocess.run([tool, "extractcd", "-f", "-i", str(disc), "-o", str(cue), "-ob", str(out / "disc.bin")], check=True)
        cue_to_iso(cue, iso)
        (out / "disc.bin").unlink()
        cue.unlink()
    else:
        subprocess.run([tool, "extractdvd", "-f", "-i", str(disc), "-o", str(iso)], check=True)
    return iso


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("disc", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--chdman")
    a = ap.parse_args()
    iso = to_iso(a.disc, a.out, a.chdman)
    print(iso, sha1(iso))


if __name__ == "__main__":
    main()
