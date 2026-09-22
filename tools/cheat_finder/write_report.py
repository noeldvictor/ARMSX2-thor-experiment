"""Write the list of games that STILL have no cheats, with what was tried for each.

    python write_report.py --adb --remote-dir /storage/2664-21DE/Roms/ps2 --work <workdir> --extra extra.txt --out docs/games-without-cheats.txt

Runs cheat_coverage.py against the device (so anything bundled since - including the
SERIAL_00000000.pnach files added from gamehacking - drops off the list), then annotates each
remaining game with the gamehacking sweep's result (<work>/gamehacking/report.json) and any
other-region hit in the XiGuanChi DB (<work>/xiguanchi_tree.json). Games that got a cheat file
are not listed; a footer just counts them.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
from online_cheats import db_by_serial, game_index, norm, parse_missing  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adb", action="store_true")
    ap.add_argument("--coverage")
    ap.add_argument("--remote-dir")
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--extra", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    if a.coverage:
        text = Path(a.coverage).read_text(encoding="utf-8", errors="replace")
    else:
        cmd = [sys.executable, str(REPO / "tools/cheat_coverage.py"), "--adb", "--show-unmatched"]
        if a.remote_dir:
            cmd += ["--remote-dir", a.remote_dir]
        text = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    missing, unresolved = parse_missing(text)
    bundled_dir = REPO / "platforms/android/app/src/main/assets/cheats"
    bundled_serials = {l.split("\t")[1] for l in (bundled_dir / "index.tsv").read_text(encoding="utf-8").splitlines()[1:] if "\t" in l}
    bundled_serials |= {m.group(1) for f in bundled_dir.glob("*.pnach") for m in [re.match(r"([A-Z]{4}-\d{5})_", f.name)] if m}
    extra = []
    if a.extra and a.extra.exists():
        for line in a.extra.read_text(encoding="utf-8").splitlines():
            if "|" in line and not line.startswith("#"):
                s, t = [x.strip() for x in line.split("|", 1)[:2]]
                extra.append((s, t))
    still = missing + [(s, t) for s, t in extra if s not in bundled_serials]
    still = [(s, t) for s, t in still if s not in bundled_serials]

    gh = json.loads((a.work / "gamehacking/report.json").read_text(encoding="utf-8")) if (a.work / "gamehacking/report.json").exists() else {}
    names = game_index()
    db = db_by_serial(a.work / "xiguanchi_tree.json") if (a.work / "xiguanchi_tree.json").exists() else {}
    db_titles = {s: norm(names.get(s, "")) for s in db}

    lines = ["Games on the Thor still without cheats", ""]
    lines.append("Checked: the bundle (assets/cheats), XiGuanChi/PCSX2-CheatsDB on GitHub (serial-exact), and")
    lines.append("gamehacking.org's PCSX2 export. What is left needs authoring (the ps2-cheat skill) or a")
    lines.append("region-specific port of the hit listed under it.")
    lines.append("")
    for s, t in still:
        r = gh.get(s)
        if r is None:
            status = "gamehacking: not searched yet"
        elif r.get("error"):
            status = "gamehacking: " + r["error"]
        elif r.get("skip"):
            seen = ", ".join(x[0] for x in r.get("results", [])[:3]) or "no results"
            status = f"gamehacking: no {r.get('region', '?')} PS2 entry (seen: {seen})"
        elif r.get("file"):
            status = "gamehacking: exported - bundle it"
        else:
            status = "gamehacking: nothing usable"
        lines.append(f"{s}  {t}")
        lines.append(f"    {status}")
        nt = norm(t)
        alt = [x for x, tt in db_titles.items() if tt and x != s and (tt == nt or (len(nt) > 12 and (tt.startswith(nt) or nt.startswith(tt))))]
        for x in alt[:3]:
            lines.append(f"    other region in GitHub DB: {x} {names.get(x, '?')} - https://raw.githubusercontent.com/XiGuanChi/PCSX2-CheatsDB/main/{db[x][0].replace(' ', '%20')}")
    if unresolved:
        lines += ["", "Filenames the library cannot resolve to a serial (status unknown until mapped in extra.txt):"]
        known = {s for s, _ in extra}
        lines += ["  " + u for u in unresolved]
    found = sum(1 for r in gh.values() if r.get("file"))
    lines += ["", f"Found and bundled from gamehacking so far: {found} games (assets/cheats/SERIAL_00000000.pnach, switched off).",
              f"Still without cheats: {len(still)}."]
    a.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"still without: {len(still)}; found: {found}; wrote {a.out}")


if __name__ == "__main__":
    main()
