"""Write a game's disc textures as native-size PNGs, ready for upscale.py.

    python extract_native.py hd-packs/<game>/extractor.py GAME.iso OUT_DIR

The game-specific part is the extractor: a Python file (usually `hd-packs/<game>/extractor.py`)
that knows the game's disc formats and provides

- `disc_images(iso_path)` - a generator of `(key, indices, palettes)`, one per unique palette
  image on the disc:
  - `key`: a stable name. The HD file is `<key>.png`, or `<key>_p<N>.png` when an image has
    several palettes.
  - `indices`: an HxW uint8 array of palette indices (4-bit images expanded, low nibble first),
    in the layout the game uploads to the GS - the emulator hashes exactly these.
  - `palettes`: a list of raw palettes (RGBA bytes, PS2 alpha where 0x80 is opaque, index order,
    16 or 256 entries) exactly as the GS receives them. An empty list marks a *palette-free*
    image, one the game colours at runtime (fonts): the pack stores an HD index map for it and
    the emulator paints it with whatever palette the game is using.
- `palette_free_palette(key)` (optional) - the palette a palette-free image is painted with for
  upscaling, in the same format. Use the real runtime palette: build a pack once, then read it
  from the emulator log (`Disc atlas: palette <hash>: ...`, one u32 per entry, 0xAABBGGRR).
  It matters: the upscaler only sees colours, so a palette whose neighbouring indices look alike
  (a grey ramp where the ink is index 1, say) is upscaled into the wrong indices. Without the
  function a grey ramp (grey = index * 17, opaque) is used.

Output: `<key>[_p<N>].png` (RGBA, alpha scaled to 0..255) and `manifest.json` (per file: width,
height, palette count, whether it is palette-free). Needs numpy and pillow, plus whatever the
extractor imports (pycdlib for ISO 9660 discs).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


def load_extractor(path: Path):
    """Import an extractor file as a module."""
    spec = importlib.util.spec_from_file_location(f"extractor_{path.parent.name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def grey_ramp(entries: int = 16) -> bytes:
    step = 255 // (entries - 1)
    return b"".join(bytes([i * step, i * step, i * step, 0x80]) for i in range(entries))


def representative_palette(extractor, key: str, indices: np.ndarray) -> bytes:
    """The palette a palette-free image is painted with for upscaling (RGBA bytes, PS2 alpha)."""
    fn = getattr(extractor, "palette_free_palette", None)
    pal = fn(key) if fn else None
    return pal if pal else grey_ramp(16 if int(indices.max()) < 16 else 256)


def paint(indices: np.ndarray, pal: bytes) -> np.ndarray:
    """RGBA 0..255 image of `indices` in palette `pal` (PS2 alpha)."""
    p = np.frombuffer(pal, np.uint8).reshape(-1, 4).astype(np.uint16)
    rgba = p[indices]
    rgba[..., 3] = np.minimum(rgba[..., 3] * 2, 255)  # PS2 0x80 is opaque
    return rgba.astype(np.uint8)


def main() -> None:
    from PIL import Image

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extractor", type=Path, help="the game's extractor.py")
    ap.add_argument("iso", type=Path)
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    extractor = load_extractor(a.extractor)

    manifest: dict[str, dict] = {}
    for key, indices, pals in extractor.disc_images(a.iso):
        h, w = indices.shape
        if not pals:
            Image.fromarray(paint(indices, representative_palette(extractor, key, indices)), "RGBA").save(a.out / f"{key}.png")
            manifest[f"{key}.png"] = {"width": w, "height": h, "palettes": 0, "palette_free": True}
            continue
        for p, pal in enumerate(pals):
            name = f"{key}.png" if len(pals) == 1 else f"{key}_p{p}.png"
            Image.fromarray(paint(indices, pal), "RGBA").save(a.out / name)
            manifest[name] = {"width": w, "height": h, "palettes": len(pals), "palette_free": False}

    (a.out / "manifest.json").write_text(json.dumps({"textures": manifest}, indent=1))
    free = sum(1 for m in manifest.values() if m["palette_free"])
    print(f"{len(manifest)} textures written to {a.out} ({free} palette-free)")


if __name__ == "__main__":
    main()
