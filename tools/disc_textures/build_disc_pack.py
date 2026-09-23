"""Build a disc-atlas texture pack for Okage: Shadow King from the disc image and upscaled textures.

    python build_disc_pack.py okage.iso HD_DIR PACK_DIR

HD_DIR holds the upscaled textures named like okage_xim.py names them (`<12 hex>.png`); the
native PNGs from okage_xim.py work too, as a 1x pack that must render exactly like no pack. The
output is a replacements folder for `<DataRoot>/textures/SCUS-97129/replacements/`:

- `atlas/<12 hex>.png` - each whole upscaled disc texture, alpha back on the PS2 scale (0x80 =
  opaque), which is what replacement textures use;
- `disc-atlas.a2at` - the index the emulator matches against (pcsx2/GS/Renderers/HW/GSDiscAtlas.*):
  per disc texture its palette hash (XXH3 of the 256/16 RGBA entries, as the texture cache keys
  the CLUT), size and palette indices, plus the XXH3 of every 16x16 block at 16-pixel positions.

Why crops: what a draw samples is a rectangle of a disc image at a 16-pixel-aligned position
(Okage mostly draws pieces of 256x256 atlases), and PCSX2 keys a region texture by XXH3 over that
rectangle's indices. The emulator hashes the drawn texture's top-left block, looks up the disc
images holding that block, and takes the one whose crop hashes to the texture's own key - exact.

Needs pycdlib, numpy, pillow, xxhash. Palette textures only (PSMT8/PSMT4) for now.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import struct
import sys
from pathlib import Path

import numpy as np
import xxhash
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import okage_xim  # noqa: E402

TILE = 16


def disc_images(iso_path: Path):
    """Yield (key, indices HxW uint8, [palette bytes per palette]) for each unique palette XIM."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))

    def read(path: str) -> bytes:
        b = io.BytesIO()
        iso.get_file_from_iso_fp(b, iso_path=path)
        return b.getvalue()

    seen: set[bytes] = set()
    for root, _dirs, files in iso.walk(iso_path="/"):
        for f in files:
            path = root.rstrip("/") + "/" + f
            upper = f.upper()
            if ".XIM" in upper:
                blobs = [(read(path), False)]
            elif ".XPF" in upper:
                blobs = [(b, True) for n, b in okage_xim.xpf_entries(read(path)) if n.lower().endswith(".xim")]
            else:
                continue
            for blob, compressed in blobs:
                digest = hashlib.sha1(blob).digest()
                if digest in seen:
                    continue
                seen.add(digest)
                d = okage_xim.lz_decode(blob) if compressed else blob
                psm = (struct.unpack_from("<I", d, 0)[0] >> 20) & 0x3F
                if psm not in (0x13, 0x14):
                    continue
                key = hashlib.sha1(d).hexdigest()[:12]
                pal_size, _, pal_count, entries = struct.unpack_from("<IIII", d, 0x10)
                img_off = 0x10 + pal_size
                _, _, h, w = struct.unpack_from("<IIII", d, img_off)
                px = np.frombuffer(d, np.uint8, len(d) - img_off - 0x10, img_off + 0x10)
                if psm == 0x14:
                    e = np.empty(px.size * 2, np.uint8)
                    e[0::2] = px & 0x0F
                    e[1::2] = px >> 4
                    px = e
                indices = px[: w * h].reshape(h, w)
                pals = [d[0x20 + p * entries * 4 : 0x20 + (p + 1) * entries * 4] for p in range(max(pal_count, 1))]
                yield key, indices, pals
    iso.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("iso", type=Path)
    ap.add_argument("hd", type=Path)
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    (a.out / "atlas").mkdir(parents=True, exist_ok=True)

    images = []  # (clut_hash, w, h, index_offset, file)
    tiles = []  # (clut_hash, tile_hash, image, x, y)
    index_data = bytearray()
    missing = 0
    scales = set()

    for key, indices, pals in disc_images(a.iso):
        h, w = indices.shape
        offset = len(index_data)
        index_data += indices.tobytes()
        block_hashes = [(x, y, xxhash.xxh3_64_intdigest(np.ascontiguousarray(indices[y : y + TILE, x : x + TILE]).tobytes()))
                        for y in range(0, h - TILE + 1, TILE) for x in range(0, w - TILE + 1, TILE)]
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
            rgba[..., 3] = (rgba[..., 3].astype(np.uint16) + 1) // 2  # 255 -> 128, the PS2 scale
            Image.fromarray(rgba, "RGBA").save(a.out / "atlas" / name)

            clut_hash = xxhash.xxh3_64_intdigest(pal)
            image_id = len(images)
            images.append((clut_hash, w, h, offset, f"atlas/{name}"))
            tiles += [(clut_hash, th, image_id, x, y) for x, y, th in block_hashes]

    tiles.sort(key=lambda t: (t[0], t[1]))
    header_size = 32
    index_data_offset = header_size + 64 * len(images) + 24 * len(tiles)
    with open(a.out / "disc-atlas.a2at", "wb") as f:
        f.write(struct.pack("<4sIIIIIQ", b"A2AT", 1, len(images), len(tiles), TILE, 0, index_data_offset))
        for clut_hash, w, h, off, file in images:
            f.write(struct.pack("<QIIQ40s", clut_hash, w, h, off, file.encode("ascii")))
        for clut_hash, th, image_id, x, y in tiles:
            f.write(struct.pack("<QQIHH", clut_hash, th, image_id, x, y))
        f.write(index_data)

    size = (a.out / "disc-atlas.a2at").stat().st_size
    print(f"{len(images)} disc textures, {len(tiles)} blocks, index {size / 1e6:.1f} MB, scales {sorted(scales)}, "
          f"{missing} without an HD image")


if __name__ == "__main__":
    main()
