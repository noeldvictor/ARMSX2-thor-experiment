"""Remove duplicate cheat sections from PNACH files.

    python dedupe_pnach.py <file-or-dir> [...] [--dry-run]

A section is a `[header]` plus everything up to the next header. Two sections are duplicates
when their patch lines are the same set (the `// ` comment prefix the bundle uses is ignored,
so an enabled and a disabled copy still match). The first occurrence is kept with its text
untouched; later copies go, whatever they were called - community exports carry the same code
under "Have Gifts\\Abacus" and "Have Gift Codes\\Abacus" from two hackers, and the NetherSX2 set
has "Max Money" and "Infinite Money" writing the same word. Sections without patch lines are
left alone. Prints one line per changed file.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

PATCH = re.compile(r"^\s*(?://\s*)?(patch=.+?)\s*$")
# Device-only sections: GameShark/CodeBreaker/AR hooks that PCSX2 never needs. The address
# filter in gamehacking_export.py already drops most (they decode to invalid types); this
# catches any that decoded to something legal-looking.
MASTER = re.compile(r"^\[(?:master codes?\\|.*\(m\) must be on|.*enable code \(must be on\)|.*\bmaster code\b)", re.IGNORECASE)
# Group labels that are advice for cartridge users, not a category: keep the section, lose the prefix.
NOISE_GROUP = re.compile(r"^\[this game requires a code ?breaker[^\\]*\\", re.IGNORECASE)


def leaf(header: str) -> str:
    """The cheat's own name: header without brackets, group prefix, slot suffix, case, punctuation."""
    name = header.strip("[]").split(chr(92))[-1].split("/")[-1]
    name = re.sub(r"[-\s]*slot\s*\d+$", "", name, flags=re.IGNORECASE)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def dedupe(text: str) -> tuple[str, int, int]:
    lines = text.splitlines()
    # split into a preamble and sections
    starts = [i for i, l in enumerate(lines) if l.startswith("[")]
    if not starts:
        return text, 0, 0
    preamble = lines[: starts[0]]
    sections = [lines[a:b] for a, b in zip(starts, starts[1:] + [len(lines)])]
    seen: dict[frozenset[str], int] = {}  # patch set -> index into kept
    kept: list[list[str]] = []
    removed = 0
    for sec in sections:
        if MASTER.match(sec[0]):
            removed += 1
            continue
        if NOISE_GROUP.match(sec[0]):
            sec = ["[" + NOISE_GROUP.sub("", sec[0], count=1)] + sec[1:]
        patches = frozenset(m.group(1).replace(" ", "").upper() for l in sec if (m := PATCH.match(l)))
        if patches and patches in seen:
            # Same lines, so the same effect; only the label differs. When the names really
            # differ (not just the group prefix or a slot number), the source claimed two things
            # for one code - keep the second name as an alias so that is not lost.
            first = kept[seen[patches]]
            a, b = leaf(first[0]), leaf(sec[0])
            if b and b != a and b not in leaf(first[0][:-1]) and first[0].count(" / ") < 2:
                raw = sec[0].strip("[]").split(chr(92))[-1].split("/")[-1].strip()
                first[0] = first[0][:-1] + " / " + raw + "]"
            removed += 1
            continue
        if patches:
            seen[patches] = len(kept)
        kept.append(sec)
    out = preamble + [l for sec in kept for l in sec]
    # normalise the tail: one newline, no run of blank lines at the end
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n", len(sections), removed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    files = [f for p in a.paths for f in (sorted(p.glob("*.pnach")) if p.is_dir() else [p])]
    total_removed = changed = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        new, n, removed = dedupe(text)
        if removed or new != text:
            changed += 1
            total_removed += removed
            print(f"{f.name}: {n} sections, {removed} duplicates removed")
            if not a.dry_run:
                f.write_text(new, encoding="utf-8", newline="\n")
    print(f"{len(files)} files, {changed} changed, {total_removed} duplicate sections removed")


if __name__ == "__main__":
    main()
