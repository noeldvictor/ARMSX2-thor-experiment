#!/usr/bin/env python3
"""Report which of your PS2 games have bundled cheats and which do not.

The bundled cheat set is keyed by CRC, and a CRC is only knowable by reading the
disc - which means a filename alone cannot tell you whether a game has cheats. This
works around that by going through the serial instead: PCSX2's GameIndex.yaml maps
serial -> title, assets/cheats/index.tsv maps CRC -> serial, so a filename that can
be resolved to a serial can be answered.

Matching is best-effort by design. A game whose filename resolves to no serial is
reported as UNMATCHED rather than silently counted as missing cheats - those two
outcomes mean very different things and collapsing them would make the summary lie.

Usage:
    python tools/cheat_coverage.py --adb                     # pull list from device
    python tools/cheat_coverage.py --dir /path/to/ps2        # local folder
    python tools/cheat_coverage.py --list games.txt          # one filename per line
    python tools/cheat_coverage.py --adb --show-unmatched    # include the misses
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAME_INDEX = os.path.join(REPO_ROOT, "bin", "resources", "GameIndex.yaml")
CHEAT_INDEX = os.path.join(
    REPO_ROOT, "platforms", "android", "app", "src", "main", "assets", "cheats", "index.tsv"
)

DISC_EXTENSIONS = (".chd", ".iso", ".cso", ".zso", ".gz", ".bin", ".m3u")

# SLUS-20495 and friends. Also matches the SCES/SLPM/SCAJ family.
SERIAL_RE = re.compile(r"\b(S[LC][UAEPK][SPMDJ]?-?\d{5})\b", re.IGNORECASE)

# Trailing release tags: (USA), [NTSC-U], [T-En by ...], (Undub), and so on. These are
# scene-convention noise, not part of the title, and they are what stops a naive
# filename comparison from ever matching.
TAG_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")


def normalize_title(text: str) -> str:
    """Reduce a title to something comparable across naming conventions."""
    text = TAG_RE.sub(" ", text)
    text = text.lower()
    # "Final Fantasy X-2" vs "Final Fantasy X2", "Ratchet & Clank" vs "Ratchet and Clank"
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    # Leading articles move around between regions and databases.
    text = re.sub(r"^(the|a|an) ", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def normalize_serial(serial: str) -> str:
    s = serial.upper().replace("-", "")
    return f"{s[:4]}-{s[4:]}" if len(s) >= 9 else serial.upper()


def load_game_index(path: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return (serial -> title, normalized title -> [serials]).

    Hand-parsed rather than via a YAML library: the file is 2.7MB and only two fields
    are needed, so a full parse costs seconds and buys nothing. The format is stable -
    a serial at column 0, `name:` indented under it.
    """
    if not os.path.exists(path):
        sys.exit(f"GameIndex.yaml not found at {path}")

    serial_to_title: dict[str, str] = {}
    title_to_serials: dict[str, list[str]] = {}
    current: str | None = None

    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line[:1].isalpha() and line.rstrip().endswith(":"):
                current = line.rstrip()[:-1].strip().strip('"')
                continue
            if current and line.lstrip().startswith("name:"):
                title = line.split("name:", 1)[1].strip().strip('"')
                serial_to_title[current] = title
                title_to_serials.setdefault(normalize_title(title), []).append(current)
                current = None

    return serial_to_title, title_to_serials


def load_cheat_index(path: str) -> tuple[set[str], int]:
    """Return (serials with a bundled cheat file, number of cheat rows)."""
    if not os.path.exists(path):
        sys.exit(f"cheat index.tsv not found at {path}")

    serials: set[str] = set()
    rows = 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle):
            if line_number == 0 or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip():
                serials.add(normalize_serial(parts[1].strip()))
                rows += 1
    # Serial-named files (SERIAL_CRC.pnach, the modern PCSX2 name) sit next to the index
    # without a row in it; the app's badge index reads the serial off the filename, so the
    # coverage report has to as well.
    for name in os.listdir(os.path.dirname(path)):
        match = re.match(r"([A-Z]{4}-\d{5})_[0-9A-Fa-f]{8}\.pnach$", name, re.IGNORECASE)
        if match:
            serials.add(normalize_serial(match.group(1)))
            rows += 1
    return serials, rows


def list_from_adb(device: str | None, remote_dir: str) -> list[str]:
    command = ["adb"]
    if device:
        command += ["-s", device]
    command += ["shell", f"ls {remote_dir}"]
    try:
        output = subprocess.run(command, capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"adb failed: {exc}")
    return [line.strip() for line in output.replace("\r", "").splitlines() if line.strip()]


def resolve_serial(
    filename: str,
    title_to_serials: dict[str, list[str]],
) -> tuple[str | None, str]:
    """Map a filename to a serial. Returns (serial, how) where how explains the match."""
    stem = os.path.splitext(filename)[0]

    found = SERIAL_RE.search(stem)
    if found:
        return normalize_serial(found.group(1)), "serial-in-filename"

    candidates = title_to_serials.get(normalize_title(stem))
    if candidates:
        # Several regional releases share a title. Any of them having cheats is a
        # better answer than arbitrarily picking one, so the caller checks them all;
        # returning the first is only for display.
        return candidates[0], "title-match"

    return None, "unmatched"


def main() -> int:
    # Titles carry accented characters (Xenosaga's "Bose", Pokemon, various JP releases) and
    # the Windows console defaults to a codepage that cannot encode them. Without this the
    # tool dies partway through printing results it has already correctly computed.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--adb", action="store_true", help="list games from a connected device")
    source.add_argument("--dir", help="local directory of disc images")
    source.add_argument("--list", help="text file with one filename per line")
    source.add_argument("--library", help="JSON from the on-device dev server's `library` tool: real serials, no filename guessing")
    parser.add_argument("--device", help="adb device serial, for --adb with more than one device")
    parser.add_argument(
        "--remote-dir",
        default="/storage/2664-21DE/Roms/ps2/",
        help="device path to scan with --adb",
    )
    parser.add_argument("--show-unmatched", action="store_true", help="list files no serial could be resolved for")
    args = parser.parse_args()

    library: dict[str, str] = {}  # filename -> serial, when the app's scan is available
    if args.library:
        # The app reads SYSTEM.CNF, so this is the truth the filename heuristics only guess at:
        # "Xenosaga Episode I (USA)" is SLUS-20469, not the Asian SCAJ the title match picked.
        import json
        import urllib.parse

        with open(args.library, encoding="utf-8") as handle:
            data = json.load(handle)
        for game in data.get("games", data) if isinstance(data, dict) else data:
            if (game.get("platform") or "PS2") != "PS2" or not game.get("serial"):
                continue
            library[urllib.parse.unquote(game["uri"].rsplit("/", 1)[-1])] = normalize_serial(game["serial"])
        names = sorted(library)
    elif args.adb:
        names = list_from_adb(args.device, args.remote_dir)
    elif args.dir:
        names = sorted(os.listdir(args.dir))
    else:
        with open(args.list, encoding="utf-8", errors="replace") as handle:
            names = [line.strip() for line in handle if line.strip()]

    names = [n for n in names if n.lower().endswith(DISC_EXTENSIONS)]
    if not names:
        sys.exit("no disc images found")

    serial_to_title, title_to_serials = load_game_index(GAME_INDEX)
    cheat_serials, cheat_rows = load_cheat_index(CHEAT_INDEX)

    have: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []
    unmatched: list[str] = []

    for name in names:
        if name in library:
            serial, how = library[name], "serial-in-filename"
        else:
            serial, how = resolve_serial(name, title_to_serials)
        if serial is None:
            unmatched.append(name)
            continue

        # Check every serial sharing this title, not just the display one.
        stem = os.path.splitext(name)[0]
        related = (
            [serial] if how == "serial-in-filename" else title_to_serials.get(normalize_title(stem), [serial])
        )
        title = serial_to_title.get(serial, stem)
        if any(candidate in cheat_serials for candidate in related):
            have.append((serial, title))
        else:
            missing.append((serial, title))

    total = len(names)
    resolved = len(have) + len(missing)

    print(f"Bundled cheat set: {cheat_rows} files covering {len(cheat_serials)} serials")
    print(f"Games scanned:     {total}")
    print(f"  resolved to a serial: {resolved}")
    print(f"  unresolved:           {len(unmatched)}")
    if resolved:
        print(f"\nOf the {resolved} resolved:")
        print(f"  HAVE cheats:    {len(have)}  ({100 * len(have) // resolved}%)")
        print(f"  MISSING cheats: {len(missing)}  ({100 * len(missing) // resolved}%)")

    if missing:
        print("\n--- missing cheats ---")
        for serial, title in sorted(missing, key=lambda item: item[1].lower()):
            print(f"  {serial}  {title}")

    if unmatched:
        print(f"\n--- unresolved ({len(unmatched)}) ---")
        print("  No serial could be derived, so cheat status is UNKNOWN, not absent.")
        print("  Usually a translation patch, an undub, or a title the database spells differently.")
        if args.show_unmatched:
            for name in sorted(unmatched, key=str.lower):
                print(f"  {name}")
        else:
            print("  Re-run with --show-unmatched to list them.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
