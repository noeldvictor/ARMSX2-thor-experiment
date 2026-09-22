"""Which of the games without bundled cheats have PNACH files online, and where.

    python online_cheats.py --adb [--remote-dir /storage/2664-21DE/Roms/ps2] [--extra serials.txt]
    python online_cheats.py --coverage coverage.txt

Runs (or reads) tools/cheat_coverage.py, then checks the one public PNACH database that is
serial-keyed - XiGuanChi/PCSX2-CheatsDB (23k files named SERIAL_CRC.pnach) - and writes:

  online_cheats_report.txt   found / other-region-only / nothing, with raw download URLs
  gamehacking_targets.txt    SERIAL|Title lines for gamehacking_export.py (the "nothing" set
                             plus the "other region" set, since gamehacking is region-exact)

The CRC-named collections (xs1l3n7x, shadowninja826) are the NetherSX2 set already bundled
under assets/cheats, so they are not queried. `--extra` adds `SERIAL|Title` lines for games
the coverage tool could not resolve (translation patches keep the original serial - look it
up in bin/resources/GameIndex.yaml, `name-en:` for Japanese releases).
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TREE_URL = "https://api.github.com/repos/XiGuanChi/PCSX2-CheatsDB/git/trees/main?recursive=1"
RAW = "https://raw.githubusercontent.com/XiGuanChi/PCSX2-CheatsDB/main/"


def game_index() -> dict[str, str]:
    names: dict[str, str] = {}
    cur = None
    for line in open(REPO / "bin/resources/GameIndex.yaml", encoding="utf-8"):
        m = re.match(r"^([A-Z]{4}-\d{5}):", line)
        if m:
            cur = m.group(1)
            continue
        if cur and line.startswith("  name:") and cur not in names:
            names[cur] = line.split(":", 1)[1].strip().strip('"')
    return names


def norm(t: str) -> str:
    t = re.sub(r"\[.*?\]|\(.*?\)", " ", t.lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def coverage_text(args: argparse.Namespace) -> str:
    if args.coverage:
        return Path(args.coverage).read_text(encoding="utf-8", errors="replace")
    cmd = [sys.executable, str(REPO / "tools/cheat_coverage.py"), "--adb", "--show-unmatched"]
    if args.remote_dir:
        cmd += ["--remote-dir", args.remote_dir]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def parse_missing(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    missing, unresolved, mode = [], [], None
    for l in text.splitlines():
        if l.startswith("--- missing"):
            mode = "m"
            continue
        if l.startswith("--- unresolved"):
            mode = "u"
            continue
        if mode == "m" and re.match(r"\s+[A-Z]{4}-\d{5}\s", l):
            s, t = l.strip().split("  ", 1)
            missing.append((s, t.strip()))
        elif mode == "u" and l.strip().endswith((".chd", ".iso", ".m3u", ".bin", ".cso", ".gz")):
            unresolved.append(l.strip())
    return missing, unresolved


def db_by_serial(cache: Path) -> dict[str, list[str]]:
    if not cache.exists():
        req = urllib.request.Request(TREE_URL, headers={"Accept": "application/vnd.github+json", "User-Agent": "armsx2-cheat-finder"})
        cache.write_bytes(urllib.request.urlopen(req).read())
    tree = json.loads(cache.read_text(encoding="utf-8"))["tree"]
    out: dict[str, list[str]] = collections.defaultdict(list)
    for e in tree:
        p = e["path"]
        m = re.search(r"([A-Z]{4}-\d{5})_[0-9A-Fa-f]{8}", p)
        if m and p.lower().endswith(".pnach") and "cheats" in p.lower():
            out[m.group(1)].append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--adb", action="store_true")
    g.add_argument("--coverage", help="saved output of cheat_coverage.py --show-unmatched")
    ap.add_argument("--remote-dir")
    ap.add_argument("--extra", type=Path, help="SERIAL|Title lines for unresolved files")
    ap.add_argument("--out", type=Path, default=Path("."))
    a = ap.parse_args()

    missing, unresolved = parse_missing(coverage_text(a))
    extra = []
    if a.extra:
        for line in a.extra.read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.startswith("#"):
                s, t = [x.strip() for x in line.split("|", 1)[:2]]
                extra.append((s, t))
    names = game_index()
    bundled = {l.split("\t")[1] for l in (REPO / "platforms/android/app/src/main/assets/cheats/index.tsv").read_text(encoding="utf-8").splitlines()[1:] if "\t" in l}
    db = db_by_serial(a.out / "xiguanchi_tree.json")
    db_titles = {s: norm(names.get(s, "")) for s in db}

    found, other, none, targets = [], [], [], []
    for serial, title in missing + [(s, t) for s, t in extra if s not in bundled]:
        hits = db.get(serial, [])
        if hits:
            found.append((serial, title, hits))
            continue
        nt = norm(title)
        alt = [s for s, t in db_titles.items() if t and (t == nt or (len(nt) > 12 and (t.startswith(nt) or nt.startswith(t))))]
        (other if alt else none).append((serial, title, alt))
        targets.append((serial, title))

    lines = [f"Games without bundled cheats - online check", ""]
    lines.append(f"=== FOUND in XiGuanChi/PCSX2-CheatsDB, exact serial ({len(found)}) ===")
    for s, t, hits in found:
        lines.append(f"{s}  {t}")
        for h in hits[:6]:
            lines.append("    " + RAW + urllib.parse.quote(h))
    lines += ["", f"=== OTHER REGION ONLY in that DB - not usable as-is ({len(other)}) ==="]
    for s, t, alt in other:
        lines.append(f"{s}  {t}")
        for x in alt[:4]:
            lines.append(f"    {x}  {names.get(x, '?')}: " + RAW + urllib.parse.quote(db[x][0]))
    lines += ["", f"=== NOT IN THAT DB ({len(none)}) - try gamehacking_export.py, then the ps2-cheat skill ==="]
    for s, t, _ in none:
        lines.append(f"{s}  {t}")
    if extra:
        lines += ["", "=== hand-resolved files (already bundled = the badge just could not read the filename) ==="]
        for s, t in extra:
            lines.append(f"{s}  {t}  {'[bundled]' if s in bundled else ''}")
    if unresolved:
        lines += ["", "=== unresolved filenames (pass --extra with their serials) ==="] + ["  " + u for u in unresolved]
    (a.out / "online_cheats_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (a.out / "gamehacking_targets.txt").write_text("\n".join(f"{s}|{t}" for s, t in targets) + "\n", encoding="utf-8", newline="\n")
    print(f"found={len(found)} other-region={len(other)} none={len(none)} -> {a.out / 'online_cheats_report.txt'}, {len(targets)} gamehacking targets")


if __name__ == "__main__":
    main()
