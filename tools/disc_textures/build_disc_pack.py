"""Build a disc-atlas texture pack from a disc image and its upscaled textures.

    python build_disc_pack.py hd-packs/<game>/extractor.py GAME.iso HD_DIR PACK_DIR

HD_DIR holds the upscaled textures under the names extract_native.py gave them; the native PNGs
work too, as a 1x pack that must render exactly like no pack. The output is a replacements folder
for `<DataRoot>/textures/<SERIAL>/replacements/`:

- `atlas/<key>.astc` - each whole upscaled disc texture as ASTC 4x4 (see astc.py), alpha back on
  the PS2 scale (0x80 = opaque), which is what replacement textures use. `--format png` writes
  PNG instead: lossless, for the 1x exactness test and for 2x packs (ASTC needs every crop on the
  4x4 block grid, which only a 4x pack guarantees). Palette-free index maps are always PNG. A pack
  with ASTC images has index version 5, so an emulator that cannot crop them refuses the pack;
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
- True-colour images keep their texels in the index - RGB for PSMCT24, RGBA for PSMCT32 - with
  their blocks hashed over RGB in the same second table, and the HD image keeps the upscaled
  alpha (index version 4). The emulator rebuilds the GS's TEXA alpha for a PSMCT24 check.

Why crops: what a draw samples is a rectangle of a disc image at a 16-pixel-aligned position
(Okage mostly draws pieces of 256x256 atlases), and PCSX2 keys a region texture by XXH3 over that
rectangle's indices. The emulator hashes the drawn texture's top-left block, looks up the disc
images holding that block, and takes the one whose crop hashes to the texture's own key - exact.

Another game needs only its own extractor (the contract is in extract_native.py); everything
here is game-independent.

Needs pycdlib, numpy, pillow, xxhash, and Arm's astcenc for ASTC.
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
from astc import AstcBatch, find_astcenc  # noqa: E402
from extract_native import load_extractor, raw_alpha, representative_palette, rgba32_raw_alpha  # noqa: E402

TILE = 16
STEP = 8  # block positions: every 8 px covers the 8x8 block grid of PSMT8H/PSMT4HL sheets (index v2)
FLAG_PALETTE_FREE = 1
FLAG_TRUE_COLOUR = 2  # PSMCT24: RGB in the index
FLAG_RGBA32 = 4  # PSMCT32: RGBA in the index


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
    ap.add_argument("--format", choices=("astc", "png"), default="astc",
                    help="HD image format (default astc; png for 1x test packs and 2x packs)")
    ap.add_argument("--astcenc", help="path to Arm's astcenc (else ASTCENC or PATH)")
    a = ap.parse_args()
    extractor = load_extractor(a.extractor)
    (a.out / "atlas").mkdir(parents=True, exist_ok=True)
    batch = None
    if a.format == "astc":
        exe = find_astcenc(a.astcenc)
        if not exe:
            raise SystemExit("astcenc not found: install Arm's astc-encoder, or pass --format png")
        batch = AstcBatch(exe)

    def save(rgba: np.ndarray, stem: str, scale: int) -> str:
        """Write an HD image (RGBA, PS2 alpha) as ASTC when the pack is 4x, else PNG; its file name."""
        if batch is not None and scale % 4 == 0:
            batch.add(a.out / "atlas" / f"{stem}.astc", rgba)
            return f"{stem}.astc"
        Image.fromarray(rgba, "RGBA").save(a.out / "atlas" / f"{stem}.png")
        return f"{stem}.png"

    images = []  # (clut_hash, w, h, index_offset, flags, file)
    tiles = []  # (clut_hash, tile_hash, image, x, y)
    free_tiles = []  # (tile_hash, image, x, y) for palette-free images
    index_data = bytearray()
    missing = 0
    scales = set()

    # What a disc repeats: the same texels under another key (the same picture stored with a
    # different CLUT blob) share one copy of index data, and the same texels with the same
    # palette are one image. Blank images (one value everywhere) are left out: upscaled they are
    # still blank, so matching them buys nothing. Tales of Destiny: 4,700 duplicates, 100 blanks.
    offsets: dict[bytes, int] = {}
    seen: set[tuple[bytes, bytes]] = set()
    duplicates = blanks = 0

    for key, indices, pals in extractor.disc_images(a.iso):
        h, w = indices.shape[:2]
        texels = np.ascontiguousarray(indices).tobytes()
        flat = indices.reshape(h * w, -1)
        if (flat == flat[0]).all():
            blanks += 1
            continue
        digest = xxhash.xxh3_128_digest(texels) + struct.pack("<II", w, h)
        fresh = [(p, pal) for p, pal in enumerate(pals) if (digest, pal) not in seen] if pals else []
        if pals and not fresh or not pals and (digest, b"") in seen:
            duplicates += len(pals) or 1
            continue
        duplicates += len(pals) - len(fresh)
        seen.update((digest, pal) for _, pal in fresh)
        if not pals:
            seen.add((digest, b""))
        offset = offsets.get(digest)
        if offset is None:
            offset = offsets[digest] = len(index_data)
            index_data += texels
        # True-colour blocks are keyed by RGB only (a PSMCT32 image's alpha is left out, as the
        # emulator's probe leaves it out), so the probe is the same whatever the alpha.
        keyed = indices[..., :3] if indices.ndim == 3 else indices
        block_hashes = [(x, y, xxhash.xxh3_64_intdigest(np.ascontiguousarray(keyed[y : y + TILE, x : x + TILE]).tobytes()))
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
            rgba32 = indices.shape[2] == 4
            rgba = np.asarray(hd).copy()
            if not (rgba32 and rgba32_raw_alpha(indices)):
                rgba[..., 3] = (rgba[..., 3].astype(np.uint16) + 1) // 2  # 255 -> 128, the PS2 scale
            file = save(rgba, key, scale)
            image_id = len(images)
            images.append((0, w, h, offset, FLAG_RGBA32 if rgba32 else FLAG_TRUE_COLOUR, f"atlas/{file}"))
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
        for p, pal in fresh:  # p keeps its place in the extractor's list: the HD files are named by it
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
            file = save(rgba, name[:-4], scale)

            clut_hash = xxhash.xxh3_64_intdigest(pal)
            image_id = len(images)
            images.append((clut_hash, w, h, offset, 0, f"atlas/{file}"))
            tiles += [(clut_hash, th, image_id, x, y) for x, y, th in block_hashes]

    kept_png = 0
    if batch is not None:
        batch.close()
        # Images whose alpha ASTC could not keep exact were written as PNG (astc.py): point the
        # index at those files.
        fell_back = {f"atlas/{p.name}" for p in batch.fallbacks}
        kept_png = len(fell_back)
        images = [(c, w, h, o, fl, f[:-5] + ".png" if f in fell_back else f) for c, w, h, o, fl, f in images]
    astc_images = sum(1 for i in images if i[5].endswith(".astc"))
    tiles.sort(key=lambda t: (t[0], t[1]))
    free_tiles.sort(key=lambda t: t[0])
    # Version 3: header 40 bytes, images 72 bytes (flags added), then bound tiles (24 bytes),
    # then palette-free tiles (16 bytes), then the index data. Version 4: the same layout, and
    # images may be true colour (FLAG_TRUE_COLOUR, RGB index data, RGB block hashes). Version 5:
    # the same layout, and images may be ASTC files (cropped block by block).
    header_size = 40
    index_data_offset = header_size + 72 * len(images) + 24 * len(tiles) + 16 * len(free_tiles)
    with open(a.out / "disc-atlas.a2at", "wb") as f:
        f.write(struct.pack("<4sIIIIIQII", b"A2AT", 5 if astc_images else 4, len(images), len(tiles), TILE, STEP,
                            index_data_offset,
                            len(free_tiles), 0))
        for clut_hash, w, h, off, flags, file in images:
            f.write(struct.pack("<QIIQII40s", clut_hash, w, h, off, flags, 0, file.encode("ascii")))
        for clut_hash, th, image_id, x, y in tiles:
            f.write(struct.pack("<QQIHH", clut_hash, th, image_id, x, y))
        for th, image_id, x, y in free_tiles:
            f.write(struct.pack("<QIHH", th, image_id, x, y))
        f.write(index_data)

    # Images a previous build left behind (a PNG pack rebuilt as ASTC, a key the extractor no
    # longer yields) would otherwise ride along into the zip.
    used = {i[5] for i in images}
    stale = [p for p in (a.out / "atlas").iterdir()
             if p.suffix in (".png", ".astc") and f"atlas/{p.name}" not in used]
    for p in stale:
        p.unlink()
    size = (a.out / "disc-atlas.a2at").stat().st_size
    print(f"{len(images)} disc textures ({sum(1 for i in images if i[4] & FLAG_PALETTE_FREE)} palette-free, "
          f"{sum(1 for i in images if i[4] & (FLAG_TRUE_COLOUR | FLAG_RGBA32))} true-colour), {len(tiles) + len(free_tiles)} blocks, "
          f"index {size / 1e6:.1f} MB, scales {sorted(scales)}, {astc_images} ASTC "
          f"({kept_png} kept as PNG: alpha not exact in ASTC), "
          f"{missing} without an HD image, {duplicates} duplicates and {blanks} blank images left out, "
          f"{len(stale)} stale images removed")


if __name__ == "__main__":
    main()
