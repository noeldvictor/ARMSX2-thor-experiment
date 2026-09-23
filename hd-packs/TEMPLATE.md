# <Title> (<SERIAL>) - disc HD texture pack recipe

<!-- Copy this file to hd-packs/<SERIAL>-<name>/README.md and fill it in. Keep every number measured,
not guessed; write "not measured yet" where there is none. Then add a row to ../GAMES.md.
Links here are written for that location (hd-packs/<SERIAL>-<name>/), not for this file. -->

**Status:** finished / in progress. <One line: which release of the disc, what the pack covers.>

A <scale>x HD pack for <Title> (<region>), built from your own disc. No gameplay dumping: every
texture is read off the disc, upscaled with <model> and matched exactly by the emulator. How
that works: [docs/hd-texture-packs.md](../../docs/hd-texture-packs.md).

## Before / after

<!-- The AYN Thor at 3x internal resolution, original on the left, pack on the right. One wide
shot of a typical scene plus close-ups of what the pack changes most (text, faces, maps). Put the
images in media/ as JPG, a few hundred KB at most. -->

![<What the shot shows>, original vs HD pack](media/<shot>.jpg)

## Make it

Requirements: [hd-packs/README.md](../README.md#making-a-pack-from-a-recipe). From the repository
root:

```bash
python tools/disc_textures/make_pack.py hd-packs/<SERIAL>-<name> \
    --disc "<Game>.chd" --model <model file>
```

Install: unzip `work/<SERIAL>-disc-hd<scale>x.zip` into `<DataRoot>/textures/`.

### Measured

A clean run (`make_pack.py` into a fresh `--work` folder, nothing else running), <date>, on
<CPU>, <RAM>, <GPU>:

| Step | Time | Output |
| --- | --- | --- |
| disc | | `disc.iso`, <size> |
| extract | | `native/`: <count> PNGs, <size> |
| upscale <scale>x | | `hd<scale>x/`: <size> |
| build | | `pack_hd<scale>x/replacements/`: <size> (<n> ASTC images, <index size> index) |
| zip | | `<SERIAL>-disc-hd<scale>x.zip`, <size> |
| **total** | | |

Free disk needed for the work folder: <size>, plus the disc image.

## Coverage

What the pack makes HD, and how it was checked: the scenes replayed on the Thor with `gsrunner`,
matches per scene, and whether a 1x pack renders bit-identical frames to an empty pack.

| Scene | Textures matched | 1x vs empty pack |
| --- | --- | --- |
| | | |

## Not yet

What the pack does not cover, what has not been tested, and what looks wrong. Be specific.

## How the disc stores its textures

The format notes for whoever works on this recipe next: the archive and compression formats,
the image format, palettes (CSM1 or not, how 4-bit palettes are cut), anything drawn from
runtime palettes (palette-free images), textures the game assembles in VRAM, and the dead ends.
The details belong in `extractor.py`'s docstring; summarise and point there.

## Checksums

| | |
| --- | --- |
| Disc ISO SHA-1 (after `disc.py`) | |
| Upscale model SHA-256 | |
| Index `disc-atlas.a2at` SHA-256 (ASTC pack) | |

`make_pack.py` checks them against `game.json`. The index depends only on the disc and
`extractor.py`; HD images can differ in the last bit between GPUs.

## Playing with it

<!-- Settings it was judged with on the Thor (internal resolution, RAISR-HD, fast-forward) and any
measured frame rates. -->
