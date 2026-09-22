"""Merge online_cheats_report.txt (GitHub DB) with gamehacking/report.json into one text file.

    python write_report.py <workdir> <output.txt>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

work, out = Path(sys.argv[1]), Path(sys.argv[2])
db_report = (work / "online_cheats_report.txt").read_text(encoding="utf-8") if (work / "online_cheats_report.txt").exists() else ""
gh = json.loads((work / "gamehacking" / "report.json").read_text(encoding="utf-8")) if (work / "gamehacking" / "report.json").exists() else {}

lines = [
    "Games on the Thor without bundled cheats - what exists online (2026-09-22)",
    "",
    "How to use a file: copy it to /sdcard/armsxdata/cheats/ on the Thor keeping its name",
    "(SERIAL_00000000.pnach matches the app's serial-scoped cheat glob), rescan the library, then",
    "flip the switches in the pause menu's cheat manager. gamehacking exports are community codes,",
    "unverified here; encrypted GameShark v1 sections were stripped (see 'dropped').",
    "",
]

got = [(s, r) for s, r in gh.items() if r.get("file")]
none = [(s, r) for s, r in gh.items() if not r.get("file")]
lines.append(f"=== gamehacking.org: PNACH exported ({len(got)}) - files in F:\\Projects\\pcsx2-desktop\\gamehacking\\ ===")
for s, r in got:
    lines.append(f"{s}  {r['title']}  ->  {r['picked']}  | {r['kept']} codes kept, {r['dropped']} dropped")
    lines.append(f"    {r['url']}")
    lines.append(f"    file: {Path(r['file']).name}")
lines.append("")
lines.append(f"=== gamehacking.org: nothing usable ({len(none)}) ===")
for s, r in none:
    why = r.get("error") or ("no PS2 match for region " + r.get("region", "?") + "; seen: " + ", ".join(x[0] for x in r.get("results", [])[:3]) if r.get("results") is not None else "not searched")
    lines.append(f"{s}  {r['title']}  -  {why}")
lines.append("")
lines.append("=== GitHub (XiGuanChi/PCSX2-CheatsDB) check ===")
lines.append(db_report.strip())
out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"{len(got)} exported, {len(none)} without; wrote {out}")
