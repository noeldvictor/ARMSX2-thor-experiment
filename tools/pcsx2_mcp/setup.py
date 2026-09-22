"""One-shot desktop PCSX2 setup for cheat authoring.

    python setup.py --dest F:\Projects\pcsx2-desktop [--bios-from-thor] [--game "Okage"] [--save file.max ...]

- downloads the latest stable PCSX2 Windows Qt build into <dest>/pcsx2 (portable mode)
- writes inis/PCSX2.ini: wizard skipped, PINE on 28011, cheats on, uncompressed save states,
  explicit keyboard pad bindings, native resolution, no pause on focus loss
- optionally pulls a USA BIOS and a game from the Thor over adb
- optionally imports memory-card saves (.max/.cbs/.psu/.xps) with mymcplus, one per card
- writes tools/pcsx2_mcp/config.local.json so server.py finds the install
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

INI = """[UI]
SettingsVersion = 1
SetupWizardIncomplete = false
ConfirmShutdown = false
PauseOnFocusLoss = false
StartFullscreen = false
RenderToSeparateWindow = false
[AutoUpdater]
CheckAtStartup = false
[Filenames]
BIOS = {bios}
[GameList]
RecursivePaths = {games}
[EmuCore]
EnablePINE = true
PINESlot = 28011
EnableCheats = true
EnableWideScreenPatches = false
EnableNoInterlacingPatches = false
SavestateCompressionType = 0
SavestateCompressionRatio = 0
[EmuCore/GS]
upscale_multiplier = 1
ScreenshotFormat = 0
[MemoryCards]
Slot1_Enable = true
Slot1_Filename = Mcd001.ps2
Slot2_Enable = true
Slot2_Filename = Mcd002.ps2
[InputSources]
Keyboard = true
SDL = true
[Pad]
MultitapPort1 = false
MultitapPort2 = false
[Pad1]
Type = DualShock2
Up = Keyboard/Up
Right = Keyboard/Right
Down = Keyboard/Down
Left = Keyboard/Left
Triangle = Keyboard/I
Circle = Keyboard/L
Cross = Keyboard/K
Square = Keyboard/J
Select = Keyboard/Backspace
Start = Keyboard/Return
L1 = Keyboard/Q
L2 = Keyboard/1
R1 = Keyboard/E
R2 = Keyboard/3
L3 = Keyboard/2
R3 = Keyboard/4
LUp = Keyboard/W
LRight = Keyboard/D
LDown = Keyboard/S
LLeft = Keyboard/A
RUp = Keyboard/T
RRight = Keyboard/H
RDown = Keyboard/G
RLeft = Keyboard/F
[Hotkeys]
Screenshot = Keyboard/F8
GSDumpSingleFrame = Keyboard/F9
SaveStateToSlot = Keyboard/F1
LoadStateFromSlot = Keyboard/F3
NextSaveStateSlot = Keyboard/F2
PreviousSaveStateSlot = Keyboard/Shift & Keyboard/F2
TogglePause = Keyboard/Space
ToggleTurbo = Keyboard/Tab
OpenPauseMenu = Keyboard/Escape
ToggleFullscreen = Keyboard/Alt & Keyboard/Return
"""


def sh(*args: str, check: bool = True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}\n{r.stdout}{r.stderr}")
    return r.stdout


def latest_release() -> tuple[str, str]:
    with urllib.request.urlopen("https://api.github.com/repos/PCSX2/pcsx2/releases/latest") as r:
        data = json.load(r)
    for a in data["assets"]:
        if a["name"].endswith("windows-x64-Qt.7z"):
            return data["tag_name"], a["browser_download_url"]
    raise RuntimeError("no windows-x64-Qt.7z asset in the latest release")


def install_pcsx2(dest: Path) -> Path:
    pcsx2 = dest / "pcsx2"
    if (pcsx2 / "pcsx2-qt.exe").exists():
        print("PCSX2 already at", pcsx2)
    else:
        tag, url = latest_release()
        archive = dest / url.rsplit("/", 1)[1]
        print("downloading", tag, url)
        urllib.request.urlretrieve(url, archive)
        seven = next((p for p in [Path(r"C:\Program Files\7-Zip\7z.exe")] if p.exists()), None)
        if seven:
            sh(str(seven), "x", "-y", f"-o{pcsx2}", str(archive))
        else:
            import py7zr  # pip install py7zr if 7-Zip is missing

            py7zr.SevenZipFile(archive).extractall(pcsx2)
    # v2.8+ reads portable.txt's CONTENT as the data root: it must exist and be empty.
    (pcsx2 / "portable.txt").write_text("")
    for d in ("inis", "bios", "memcards", "cheats", "sstates", "snaps"):
        (pcsx2 / d).mkdir(exist_ok=True)
    return pcsx2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=Path(r"F:\Projects\pcsx2-desktop"))
    ap.add_argument("--bios", default="SCPH-70012.bin", help="BIOS filename to select")
    ap.add_argument("--bios-from-thor", action="store_true", help="adb pull the BIOS from /sdcard/armsxdata/bios")
    ap.add_argument("--game", help="substring of a PS2 image on the Thor SD card to adb pull into <dest>/games")
    ap.add_argument("--save", action="append", default=[], help="memory-card save to import (.max/.cbs/.psu/.xps); repeat for more, one per card")
    ap.add_argument("--no-ini", action="store_true", help="keep an existing PCSX2.ini")
    args = ap.parse_args()

    dest = args.dest
    dest.mkdir(parents=True, exist_ok=True)
    games = dest / "games"
    games.mkdir(exist_ok=True)
    pcsx2 = install_pcsx2(dest)

    if args.bios_from_thor:
        sh("adb", "pull", f"/sdcard/armsxdata/bios/{args.bios}", str(pcsx2 / "bios" / args.bios))
        print("pulled BIOS", args.bios)
    if args.game:
        listing = sh("adb", "shell", "ls", "/storage/2664-21DE/Roms/ps2/")
        hits = [l.strip() for l in listing.splitlines() if args.game.lower() in l.lower()]
        if len(hits) != 1:
            raise SystemExit(f"--game matched {hits}")
        sh("adb", "pull", f"/storage/2664-21DE/Roms/ps2/{hits[0]}", str(games / hits[0]))
        print("pulled", hits[0])

    ini = pcsx2 / "inis" / "PCSX2.ini"
    if not args.no_ini or not ini.exists():
        ini.write_text(INI.format(bios=args.bios, games=str(games)), encoding="utf-8", newline="\n")
        print("wrote", ini)

    mymc = HERE / ".venv" / "Scripts" / "mymcplus.exe"
    for i, save in enumerate(args.save, start=1):
        card = pcsx2 / "memcards" / f"Mcd{i:03d}.ps2"
        if not card.exists():
            sh(str(mymc), "-i", str(card), "format")
        print(sh(str(mymc), "-i", str(card), "import", save).strip())

    (HERE / "config.local.json").write_text(json.dumps({"pcsx2_dir": str(pcsx2), "games_dir": str(games)}, indent=2))
    print("config.local.json ->", pcsx2)
    print("done; launch with: python server.py cli launch <game>")


if __name__ == "__main__":
    sys.exit(main())
