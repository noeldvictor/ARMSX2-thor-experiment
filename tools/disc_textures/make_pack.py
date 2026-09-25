"""Make a game's disc HD texture pack from its recipe folder, start to finish.

    python tools/disc_textures/make_pack.py hd-packs/<SERIAL>-<name> --disc GAME.chd --model 4x-UltraSharp.safetensors
                                            [--work DIR] [--chdman PATH] [--scale 4|2] [--from STEP]

The recipe folder holds `game.json` (serial, extractor, upscale settings, reference checksums)
and the game's `extractor.py`. The steps, each printed as the command it amounts to so it can be
rerun or debugged on its own:

1. disc    - disc image -> ISO (`disc.py`; needs MAME's chdman for .chd)
2. extract - every texture on the disc -> native PNGs (`extract_native.py` + the extractor)
3. upscale - native PNGs -> HD PNGs on the GPU (`upscale.py`; resumes where it stopped)
4. build   - HD PNGs + disc data -> the pack's `replacements/` folder (`build_disc_pack.py`)
5. zip     - `<SERIAL>-disc-hd<scale>x.zip`, unzipped into `<DataRoot>/textures/` to install

Work goes to `--work` (default `<recipe>/work`, which git ignores). Finished steps are skipped
(`disc`, `extract`) or resume (`upscale` skips images it already made); `--from STEP` redoes
that step and everything after it (`--from upscale` upscales everything again).
Each step's wall time goes to `<work>/timings.json`, next to the GPU it ran on.

At the end the index checksum is compared with the recipe's reference. The index
(`disc-atlas.a2at`) depends only on the disc and the extractor, never on the GPU, so a match
means your extraction is the same as the reference pack's. The HD images themselves can differ in
the last bit between GPUs and drivers; that is expected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from disc import sha1 as file_sha1  # noqa: E402

STEPS = ("disc", "extract", "upscale", "build", "zip")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def gpu_name() -> str:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True)
        return out.stdout.strip().splitlines()[0]
    except (OSError, IndexError):
        return "unknown"


def run(cmd: list[str]) -> None:
    print("\n$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recipe", type=Path, help="hd-packs/<SERIAL>-<name> folder")
    ap.add_argument("--disc", type=Path, required=True, help="the game's .chd, .cue or .iso")
    ap.add_argument("--model", type=Path, required=True, help="upscale model file (see game.json for which)")
    ap.add_argument("--work", type=Path, help="work folder (default <recipe>/work)")
    ap.add_argument("--chdman", help="path to MAME's chdman, if it is not on PATH")
    ap.add_argument("--scale", type=int, choices=(2, 4), help="override game.json's scale")
    ap.add_argument("--from", dest="start", choices=STEPS, help="redo this step and the ones after it")
    ap.add_argument("--until", choices=STEPS, help="stop after this step")
    ap.add_argument("--format", choices=("astc", "png"), default="astc", help="HD image format in the pack")
    ap.add_argument("--astcenc", help="path to Arm's astcenc, if it is not on PATH or in ASTCENC")
    a = ap.parse_args()

    game = json.loads((a.recipe / "game.json").read_text())
    serial, scale = game["serial"], a.scale or game["upscale"]["scale"]
    work = a.work or a.recipe / "work"
    work.mkdir(parents=True, exist_ok=True)
    extractor = a.recipe / game["extractor"]
    iso = a.disc if a.disc.suffix.lower() == ".iso" else work / "disc.iso"
    native, hd, pack = work / "native", work / f"hd{scale}x", work / f"pack_hd{scale}x"
    zip_path = work / f"{serial}-disc-hd{scale}x.zip"
    py = sys.executable
    redo = STEPS[STEPS.index(a.start):] if a.start else ()
    timings_path = work / "timings.json"
    timings = json.loads(timings_path.read_text()) if timings_path.exists() else {}
    timings["gpu"] = gpu_name()

    def step(name: str, fn) -> None:
        if a.until and STEPS.index(name) > STEPS.index(a.until):
            return
        t0 = time.perf_counter()
        fn()
        timings[name] = round(time.perf_counter() - t0, 1)
        timings_path.write_text(json.dumps(timings, indent=1))
        print(f"[{name}] {timings[name]:.0f} s", flush=True)

    if "disc" in redo or not iso.exists():
        if iso != a.disc:
            step("disc", lambda: run([py, str(HERE / "disc.py"), str(a.disc), str(work)] + (["--chdman", a.chdman] if a.chdman else [])))
    expected = game.get("disc", {}).get("iso_sha1")
    if expected:
        got = file_sha1(iso)
        if got != expected:
            print(f"\nWARNING: this disc's ISO SHA-1 is {got}; the recipe was made from {expected}.\n"
                  "A different dump or region can still work, but the reference checksum below will not match.")

    if "extract" in redo or not (native / "manifest.json").exists():
        step("extract", lambda: run([py, str(HERE / "extract_native.py"), str(extractor), str(iso), str(native)]))

    # The size, before the GPU hours: an HD image costs scale^2 bytes a native texel as ASTC 4x4
    # (a whole game at 4x is 16 bytes a texel - Tales of Destiny's 0.86 billion come to ~14 GB).
    manifest = json.loads((native / "manifest.json").read_text())["textures"]
    texels = sum(t["width"] * t["height"] for t in manifest.values())
    print(f"\n{len(manifest)} disc images, {texels / 1e6:.0f} M texels: the {scale}x pack will be about "
          f"{texels * scale * scale / 1e9:.1f} GB as ASTC (less the duplicates and blanks the build leaves out)",
          flush=True)

    up = game["upscale"]
    model_sha = up.get("model_sha256")
    if model_sha and sha256(a.model) != model_sha:
        print(f"\nNOTE: {a.model.name} is not the model the recipe names ({up['model']}); the pack will look different.")
    step("upscale", lambda: run([py, str(HERE / "upscale.py"), str(native), str(hd), "--model", str(a.model), "--scale", str(scale)]
                                + (["--force"] if a.start == "upscale" else [])))

    replacements = pack / "replacements"
    step("build", lambda: run([py, str(HERE / "build_disc_pack.py"), str(extractor), str(iso), str(hd), str(replacements),
                               "--format", a.format] + (["--astcenc", a.astcenc] if a.astcenc else [])
                              + (["--alpha-rule", game["astc_alpha"]] if game.get("astc_alpha") else [])))

    def make_zip() -> None:
        print(f"\nwriting {zip_path}", flush=True)
        info = (f"{game['title']} ({serial}) disc HD texture pack, {scale}x, {up['model']}.\n"
                f"Made with ARMSX2 Thor experiment's tools/disc_textures/make_pack.py from {a.recipe.name}.\n"
                "Works only in the ARMSX2 Thor experiment fork (disc-atlas packs), not stock PCSX2/ARMSX2.\n"
                f"Install: unzip into <DataRoot>/textures/ so this folder is textures/{serial}/.\n"
                f"Upscale model licence: {up.get('model_licence', 'see the model')}.\n")
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr(f"{serial}/PACK-INFO.txt", info)
            for f in sorted(replacements.rglob("*")):
                if f.is_file():
                    # PNGs are already compressed; the index is not.
                    kind = zipfile.ZIP_STORED if f.suffix in (".png", ".zst") else zipfile.ZIP_DEFLATED
                    z.write(f, f"{serial}/replacements/{f.relative_to(replacements).as_posix()}", compress_type=kind)

    step("zip", make_zip)

    if a.until and STEPS.index(a.until) < STEPS.index("build"):
        print(f"\nstopped after {a.until}")
        return
    index_sha = sha256(replacements / "disc-atlas.a2at")
    # The index names each image's file, so an ASTC and a PNG pack have different checksums.
    ref = game.get("reference", {}).get("index_sha256_png" if a.format == "png" else "index_sha256")
    total = sum(v for k, v in timings.items() if k in STEPS)
    print(f"\nGPU {timings['gpu']}; steps: " + ", ".join(f"{k} {timings[k]:.0f} s" for k in STEPS if k in timings)
          + f"; total {total / 60:.1f} min")
    print(f"pack: {zip_path} ({zip_path.stat().st_size / 1e9:.2f} GB)")
    print(f"index SHA-256 {index_sha}: " + ("matches the reference" if index_sha == ref else
                                            f"DIFFERS from the reference {ref}" if ref else "no reference in game.json"))


if __name__ == "__main__":
    main()
