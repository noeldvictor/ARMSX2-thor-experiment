# Okage: Shadow King (SCUS-97129) - disc HD texture pack recipe

A 4x HD pack for Okage: Shadow King (NTSC-U), built from your own disc. No gameplay dumping:
every texture is read off the disc, upscaled with 4x-UltraSharp and matched exactly by the
emulator. How that works: [docs/hd-texture-packs.md](../../docs/hd-texture-packs.md). Game notes:
[docs/games/okage.md](../../docs/games/okage.md).

![Status menu: portraits and menu fonts, original vs HD pack](media/menu-fonts.jpg)

| | |
| --- | --- |
| ![Inn door and Ari, original vs HD pack](media/inn.jpg) | ![Thatched roof, original vs HD pack](media/roof.jpg) |

Screenshots: the AYN Thor at 3x internal resolution (the status menu is a GS dump replayed on the
Thor with and without the pack).

## Make it

Requirements are in [hd-packs/README.md](../README.md#making-a-pack-from-a-recipe). From the
repository root:

```bash
python tools/disc_textures/make_pack.py hd-packs/SCUS-97129-okage \
    --disc "Okage Shadow King.chd" --model 4x-UltraSharp.safetensors
```

Or step by step (what `make_pack.py` runs, with `work/` as the work folder):

```bash
python tools/disc_textures/disc.py "Okage Shadow King.chd" work/                    # -> work/disc.iso
python tools/disc_textures/extract_native.py hd-packs/SCUS-97129-okage/extractor.py work/disc.iso work/native
python tools/disc_textures/upscale.py work/native work/hd4x --model 4x-UltraSharp.safetensors --scale 4
python tools/disc_textures/build_disc_pack.py hd-packs/SCUS-97129-okage/extractor.py work/disc.iso work/hd4x work/pack_hd4x/replacements
```

Install: unzip `work/SCUS-97129-disc-hd4x.zip` into `<DataRoot>/textures/` (or copy
`work/pack_hd4x/replacements` to `<DataRoot>/textures/SCUS-97129/replacements`).

## Measured on an RTX 3060 12 GB

A clean run from the `.chd`, 2026-09-22 (Core i7-11700, 32 GB RAM, RTX 3060 12 GB, CUDA fp16):

| Step | Time | Output |
| --- | --- | --- |
| disc (chdman + sector strip) | 23 s | `disc.iso`, 436 MB |
| extract | 28 s | `native/`: 2,636 PNGs, 37 MB (3 of them palette-free font sheets) |
| upscale 4x | 12 min 7 s (3.6 textures/s) | `hd4x/`: 571 MB |
| build | 1 min 47 s | `pack_hd4x/replacements/`: 604 MB (2,638 images + a 35.7 MB index) |
| zip | 13 s | `SCUS-97129-disc-hd4x.zip`, 610 MB |
| **total** | **about 15 min** | |

Build and zip were timed in a second run: in the first they overlapped an Android build and
took 5 and 2 minutes. Plan for about 2.5 GB of free disk for the work folder, plus the disc image. `--scale 2` makes a 2x pack (the model
still runs at 4x and the result is downscaled, so the time is the same; the pack is about a
quarter of the size).

## Checksums

| | |
| --- | --- |
| Disc ISO SHA-1 (after `disc.py`) | `e25313668c682df863f0dde7c21bef0a3d8b79fa` |
| Upscale model SHA-256 (`4x-UltraSharp.safetensors`) | `36a340b5509b699d2c06cb445ddc1d3d39199ac734d889ed6d7915f60e05bcbc` |
| Index `disc-atlas.a2at` SHA-256 | `7c32e6b1f09352a3db6f4f78238a39bca05d811048f872cc5a8f3775c5d88811` |

`make_pack.py` checks all three. The index is determined by the disc and `extractor.py`; the HD
images can differ slightly between GPUs.

## What it covers

- **World and characters**: every palette texture in the game's `.XIM` images, loose or inside
  `.XPF` archives (2,633 images).
- **Menus and portraits**: the status menu sheet, character portraits, the 640x480 backgrounds.
- **Fonts**: `BM.FNT`, `BMUI.FNT` and the bold 24 px `IQ24.FNT`. The game colours these at
  runtime, so the pack stores an HD index map and the emulator paints it with the game's
  palette. The palette used for upscaling is the one the game uses (read from the emulator
  log), see `extractor.py`. BM and BMUI are verified on the status menu; IQ24 is in the pack
  but no screen that draws it has been checked yet.

## Not yet

- **True-colour images** (174 `.XIM` files are PSMCT24/32) are not in the pack. The
  matching only handles palette textures so far.
- **Small or soft art** (the 24-pixel field portraits) is where ESRGAN models invent detail;
  some of it looks painted rather than sharpened.
- **Neighbouring atlas pieces** can bleed a pixel into each other's edges, because whole
  atlases are upscaled in one go.

## Playing with it

What the pack was tuned for on the Thor (8 Gen 2): 3x internal resolution, the RAISR-HD upscaler
left on (it covers whatever the pack doesn't), and 2x fast-forward on Select + R1. Measured with
the pack at 3x: Tenel 119.9 fps at 2x fast-forward (GPU 63%), the World Library ~108 fps.
