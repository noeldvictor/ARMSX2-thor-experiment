"""Build a disc-atlas texture pack from a disc image and its upscaled textures.

    python build_disc_pack.py hd-packs/<game>/extractor.py GAME.iso HD_DIR PACK_DIR

HD_DIR holds the upscaled textures under the names extract_native.py gave them; the native PNGs
work too, as a 1x pack that must render exactly like no pack. The output is a replacements folder
for `<DataRoot>/textures/<SERIAL>/replacements/`:

- `atlas/<key>.png` - each whole upscaled disc texture, alpha back on the PS2 scale (0x80 =
  opaque), which is what replacement textures use;
- `disc-atlas.a2at` - the index the emulator matches against (pcsx2/GS/Renderers/HW/GSDiscAtlas.*):
  per disc texture its palette hash (XXH3 of the 256/16 RGBA entries, as the texture cache keys
  the CLUT), size and palette indices, plus the XXH3 of every 16x16 block at 8-pixel positions.
  Blocks every 8 px (index version 2) let a menu sprite, whose tight UV region can start anywhere,
  find its disc image too.
- Palette-free images (the extractor gives no palette: fonts and anything else the game colours
  at runtime) get an HD *index map* instead: each upscaled pixel mapped back to the nearest entry
  (premultiplied RGBA) of the palette it was painted with for upscaling, stored as RGBA with the
  index in R. Their blocks go in a second table keyed by the block hash alone, and the emulator
  paints the map with the palette the game is using (index version 3).
- True-colour (PSMCT24) images keep their RGB in the index (three bytes a texel), their blocks are
  hashed over RGB in the same second table, and the HD image keeps the upscaled alpha (index
  version 4). The emulator rebuilds the GS's TEXA alpha for the exact check.

Why crops: what a draw samples is a rectangle of a disc image at a 16-pixel-aligned position
(Okage mostly draws pieces of 256x256 atlases), and PCSX2 keys a region texture by XXH3 over that
rectangle's indices. The emulator hashes the drawn texture's top-left block, looks up the disc
images holding that block, and takes the one whose crop hashes to the texture's own key - exact.

Another game needs only its own extractor (the contract is in extract_native.py); everything
here is game-independent.

Needs pycdlib, numpy, pillow, xxhash. Palette textures only (PSMT8/PSMT4) for now.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
import xxhash
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_native import load_extractor, raw_alpha, representative_palette  # noqa: E402

TILE = 16
STEP = 8  # block positions: every 8 px covers the 8x8 block grid of PSMT8H/PSMT4HL sheets (index v2)
FLAG_PALETTE_FREE = 1
FLAG_TRUE_COLOUR = 2


def nearest_index(hd: np.ndarray, pal: bytes) -> np.ndarray:
    """Map each RGBA (0..255) pixel to the nearest palette entry, compared premultiplied so the
    colour of a nearly transparent pixel does not decide it."""
    p = np.frombuffer(pal, np.uint8).reshape(-1, 4).astype(np.float32)
    p[:, 3] = np.minimum(p[:, 3] * 2, 255)
    p[:, :3] *= p[:, 3:4] / 255
    px = hd.reshape(-1, 4).astype(np.float32)
    px[:, :3] *= px[:, 3:4] / 255
    out = np.empty(len(px), np.uint8)
    for i in range(0, len(px), 1 << 16):
        d = ((px[i : i + (1 << 16), None, :] - p[None, :, :]) ** 2).sum(axis=2)
        out[i : i + (1 << 16)] = d.argmin(axis=1)
    return out.reshape(hd.shape[:2])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extractor", type=Path, help="the game's extractor.py")
    ap.add_argument("iso", type=Path)
    ap.add_argument("hd", type=Path)
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    extractor = load_extractor(a.extractor)
    (a.out / "atlas").mkdir(parents=True, exist_ok=True)

    images = []  # (clut_hash, w, h, index_offset, flags, file)
    tiles = []  # (clut_hash, tile_hash, image, x, y)
    free_tiles = []  # (tile_hash, image, x, y) for palette-free images
    index_data = bytearray()
    missing = 0
    scales = set()

    for key, indices, pals in extractor.disc_images(a.iso):
        h, w = indices.shape[:2]
        offset = len(index_data)
        index_data += np.ascontiguousarray(indices).tobytes()
        block_hashes = [(x, y, xxhash.xxh3_64_intdigest(np.ascontiguousarray(indices[y : y + TILE, x : x + TILE]).tobytes()))
                        for y in range(0, h - TILE + 1, STEP) for x in range(0, w - TILE + 1, STEP)]
        if indices.ndim == 3:
            # True colour: blocks keyed by their RGB alone, the HD image with its upscaled alpha.
            name = f"{key}.png"
            src = a.hd / name
            if not src.exists():
                missing += 1
                continue
            hd = Image.open(src).convert("RGBA")
            scale = hd.width // w
            if scale < 1 or hd.width != w * scale or hd.height != h * scale:
                print(f"skip {name}: {hd.width}x{hd.height} is not a multiple of {w}x{h}")
                continue
            scales.add(scale)
            rgba = np.asarray(hd).copy()
            rgba[..., 3] = (rgba[..., 3].astype(np.uint16) + 1) // 2  # 255 -> 128, the PS2 scale
            Image.fromarray(rgba, "RGBA").save(a.out / "atlas" / name)
            image_id = len(images)
            images.append((0, w, h, offset, FLAG_TRUE_COLOUR, f"atlas/{name}"))
            free_tiles += [(th, image_id, x, y) for x, y, th in block_hashes]
            continue
        if not pals:
            # Palette-free: the HD file was painted with a stand-in palette; store an HD index map.
            name = f"{key}.png"
            src = a.hd / name
            if not src.exists():
                missing += 1
                continue
            hd = Image.open(src).convert("RGBA")
            scale = hd.width // w
            if scale < 1 or hd.width != w * scale or hd.height != h * scale:
                print(f"skip {name}: {hd.width}x{hd.height} is not a multiple of {w}x{h}")
                continue
            scales.add(scale)
            idx = nearest_index(np.asarray(hd), representative_palette(extractor, key, indices))
            rgba = np.dstack([idx, idx, idx, np.full_like(idx, 255)])
            Image.fromarray(rgba, "RGBA").save(a.out / "atlas" / name)
            image_id = len(images)
            images.append((0, w, h, offset, FLAG_PALETTE_FREE, f"atlas/{name}"))
            free_tiles += [(th, image_id, x, y) for x, y, th in block_hashes]
            continue
        for p, pal in enumerate(pals):
            name = f"{key}.png" if len(pals) == 1 else f"{key}_p{p}.png"
            src = a.hd / name
            if not src.exists():
                missing += 1
                continue
            hd = Image.open(src).convert("RGBA")
            scale = hd.width // w
            if scale < 1 or hd.width != w * scale or hd.height != h * scale:
                print(f"skip {name}: {hd.width}x{hd.height} is not a multiple of {w}x{h}")
                continue
            scales.add(scale)
            rgba = np.asarray(hd).copy()
            if not raw_alpha(indices, pal):  # otherwise the image already holds PS2 alpha, up to 0xFF
                rgba[..., 3] = (rgba[..., 3].astype(np.uint16) + 1) // 2  # 255 -> 128, the PS2 scale
            Image.fromarray(rgba, "RGBA").save(a.out / "atlas" / name)

            clut_hash = xxhash.xxh3_64_intdigest(pal)
            image_id = len(images)
            images.append((clut_hash, w, h, offset, 0, f"atlas/{name}"))
            tiles += [(clut_hash, th, image_id, x, y) for x, y, th in block_hashes]

    tiles.sort(key=lambda t: (t[0], t[1]))
    free_tiles.sort(key=lambda t: t[0])
    # Version 3: header 40 bytes, images 72 bytes (flags added), then bound tiles (24 bytes),
    # then palette-free tiles (16 bytes), then the index data. Version 4: the same layout, and
    # images may be true colour (FLAG_TRUE_COLOUR, RGB index data, RGB block hashes).
    header_size = 40
    index_data_offset = header_size + 72 * len(images) + 24 * len(tiles) + 16 * len(free_tiles)
    with open(a.out / "disc-atlas.a2at", "wb") as f:
        f.write(struct.pack("<4sIIIIIQII", b"A2AT", 4, len(images), len(tiles), TILE, STEP, index_data_offset,
                            len(free_tiles), 0))
        for clut_hash, w, h, off, flags, file in images:
            f.write(struct.pack("<QIIQII40s", clut_hash, w, h, off, flags, 0, file.encode("ascii")))
        for clut_hash, th, image_id, x, y in tiles:
            f.write(struct.pack("<QQIHH", clut_hash, th, image_id, x, y))
        for th, image_id, x, y in free_tiles:
            f.write(struct.pack("<QIHH", th, image_id, x, y))
        f.write(index_data)

    size = (a.out / "disc-atlas.a2at").stat().st_size
    print(f"{len(images)} disc textures ({sum(1 for i in images if i[4] & FLAG_PALETTE_FREE)} palette-free, "
          f"{sum(1 for i in images if i[4] & FLAG_TRUE_COLOUR)} true-colour), {len(tiles) + len(free_tiles)} blocks, "
          f"index {size / 1e6:.1f} MB, scales {sorted(scales)}, "
          f"{missing} without an HD image")


if __name__ == "__main__":
    main()
