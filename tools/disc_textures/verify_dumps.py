"""Check a game's extractor against textures PCSX2 dumped while the game ran - the candidate test.

    python verify_dumps.py hd-packs/<game>/extractor.py GAME.iso DUMPS_DIR

DUMPS_DIR is `<pcsx2>/textures/<SERIAL>/dumps` after playing a few scenes with Texture
Replacement > Dump Textures on. Each dump is named `TEX0hash[-CLUThash][-rWxH]-bits`. For each
palette dump the tool looks for a disc image with that palette (XXH3 of the 16/256 entries in index
order), then:

- `exact`: a crop of that image whose XXH3 over its indices is the TEX0 hash - PCSX2's own name
  reproduced from disc data. Region textures and textures smaller than a GS block are hashed that
  way.
- `pixels`: for a full-size texture (PCSX2 names those by raw GS memory, not by indices), the
  dump's colours equal the disc image drawn through that palette - the same texture.
- `palette only`: the palette is on the disc but no image matched - a different crop rule, or
  the texture is built in memory.
- `no palette`: the palette is not on the disc - made at runtime (fonts, fades) or from a file the
  extractor does not read yet.

A game is a candidate when most dumps land in the first two. Needs numpy, pillow, xxhash.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

import numpy as np
import xxhash
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_native import load_extractor  # noqa: E402


def parse_name(name: str):
    parts = name.rsplit(".", 1)[0].split("-")
    bits = int(parts[-1], 16)
    tex0 = int(parts[0], 16)
    clut = None
    region = None
    for p in parts[1:-1]:
        if p.startswith("r") and "x" in p:
            region = tuple(int(v) for v in p[1:].split("x"))
        else:
            clut = int(p, 16)
    return tex0, clut, region, bits & 0x3F, 1 << ((bits >> 6) & 0xF), 1 << ((bits >> 10) & 0xF)


def find_crop(idx: np.ndarray, rw: int, rh: int, tex0: int):
    h, w = idx.shape
    for step in (8, 1):
        for y in range(0, h - rh + 1, step):
            for x in range(0, w - rw + 1, step):
                if step == 1 and x % 8 == 0 and y % 8 == 0:
                    continue
                if xxhash.xxh3_64_intdigest(np.ascontiguousarray(idx[y:y + rh, x:x + rw]).tobytes()) == tex0:
                    return x, y
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extractor", type=Path)
    ap.add_argument("iso", type=Path)
    ap.add_argument("dumps", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per dump")
    a = ap.parse_args()

    by_palette: dict[int, list] = collections.defaultdict(list)
    count = 0
    for key, texels, palettes in load_extractor(a.extractor).disc_images(a.iso):
        count += 1
        if texels.ndim != 2:
            continue
        for pal in palettes:
            by_palette[xxhash.xxh3_64_intdigest(pal)].append((key, texels, pal))
    print(f"{count} disc images, {len(by_palette)} palettes")

    results = collections.Counter()
    for f in sorted(a.dumps.glob("*.png")):
        tex0, clut, region, psm, tw, th = parse_name(f.name)
        if clut is None:
            results["true colour (not checked)"] += 1
            continue
        cands = by_palette.get(clut)
        if not cands:
            verdict = "no palette"
        else:
            rw, rh = region or (tw, th)
            verdict = "palette only"
            for key, idx, pal in cands:
                h, w = idx.shape
                if region or tw * th <= 64 * 64:
                    if rw <= w and rh <= h and find_crop(idx, rw, rh, tex0) is not None:
                        verdict = "exact"
                        break
                else:
                    dump = np.asarray(Image.open(f).convert("RGBA"))
                    ph, pw = min(h, dump.shape[0]), min(w, dump.shape[1])
                    colours = np.frombuffer(pal, np.uint8).reshape(-1, 4)[idx[:ph, :pw]][..., :3]
                    if np.array_equal(colours, dump[:ph, :pw, :3]):
                        verdict = "pixels"
                        break
        results[verdict] += 1
        if a.verbose:
            print(f"{f.name:56s} psm {psm:#04x} {verdict}")
    total = sum(results.values())
    for k in ("exact", "pixels", "palette only", "no palette", "true colour (not checked)"):
        if results[k]:
            print(f"{k:26s} {results[k]:5d}  ({100 * results[k] / total:.0f}%)")


if __name__ == "__main__":
    main()
