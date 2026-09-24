# Tales of Rebirth (SLPS-25450) - disc HD texture pack recipe

**Status:** in progress. Japanese disc with the English fan translation v1.0 (the translation keeps
the serial). The extractor reads every map sheet, sprite, UI sheet and the font, and a 1x pack is
proven exact on the Thor in four scenes; no 4x pack has been built yet, because a straight 4x pack
would be about 39 GB (see *Size* below).

A 4x HD pack for Tales of Rebirth, built from your own disc. No gameplay dumping: every texture is
read off the disc, upscaled with 4x-UltraSharp and matched exactly by the emulator. How that
works: [docs/hd-texture-packs.md](../../docs/hd-texture-packs.md).

## Before / after

Not yet.

## Make it

Requirements: [hd-packs/README.md](../README.md#making-a-pack-from-a-recipe). From the repository
root:

```bash
python tools/disc_textures/make_pack.py hd-packs/SLPS-25450-tales-of-rebirth \
    --disc "Tales of Rebirth (English v1.0).chd" --model 4x-UltraSharp.safetensors
```

Read the size estimate `make_pack.py` prints after the extract step before letting it upscale.

### Measured

Extract: 7 min 19 s on a Core i7-11700 (45,281 images once the fixes below are in; the first run
wrote 436,329 PNGs, most of them junk palettes).

### Size

What the extractor yields, in native texels (one image per palette - an exact match needs the HD
image painted with the palette the game draws with), and what a 4x ASTC pack of it would take at
16 bytes per native texel:

| | Images | Native texels | 4x pack |
| --- | --- | --- | --- |
| Map sheets (1024x1024, 8-bit) | 604 | 2,125 M | 34.0 GB |
| Sprite frames and small UI | 44,071 | 252 M | 4.0 GB |
| 256-511 px images | 507 | 60 M | 1.0 GB |
| Font glyphs | 99 | 0.1 M | - |
| **Total** | 45,281 | 2,437 M | **~39 GB** |

The map sheets are the problem: 634 M texels of unique art, but each carries 3.4 palettes on
average, and the palettes are not lighting variants of one another (correlation 0.6-0.9 with large
per-entry differences, 2% true variants): different parts of one sheet are drawn with different
palettes. The field in the first scene uses three palettes of one sheet at once. So each palette's
HD image is mostly art that palette never draws, but there is no way to know which part from the
disc alone. Tried and dropped: one HD index map per sheet (upscale once, map back to indices, paint
with any palette) - 40 dB against a real upscale where it works, but a block given the wrong
palette turns into a garbage square, and the palettes are not variants enough to make that safe.

## Coverage

A 1x pack (from the extractor before the anp3 fixes - 291,017 images, a 1.7 GB index) against an
empty pack, `gsrunner` replays of desktop GS dumps on the Thor at 3x, 2026-09-24:

| Scene | Matched | Misses | 1x vs empty pack |
| --- | --- | --- | --- |
| Title | 41 (35 font glyphs, 1 true-colour) | 1 glyph | 3/3 frames identical |
| Attract-mode battle | 31 (27 sprite composites) | 70 | 3/3 identical with nearest filtering; 1 frame differs with PS2 bilinear (below) |
| First field | 55 (26 glyphs, 7 composites) | 3 sprites | 3/3 identical |
| Field after the first battle | 59 (26 glyphs, 5 composites) | 4 sprites | 3/3 identical |

So every match is exact. The battle frame that differs under bilinear filtering differs only on the
floor, by 2 levels on average: the floor is a minified 256x256 8-bit texture, and the emulator
samples a replacement there differently from the native palette texture; with filtering forced to
nearest the frames are identical. Sprites are drawn as composites - several frames placed in one
buffer - and match at 55-99% of their drawn texels (the rest stays native).

Against the desktop texture dumps (same scenes plus the first real battle), before the anp3 fixes:
in normal play 3,044 sprite regions have their palette on the disc and 639 do not. The title demo
adds ~6,300 dumps with faded palettes made at runtime; the battle's 70 misses are mostly its
256x1024 UI sheet under such palettes.

## Not yet

- A pack. The size above needs deciding first.
- Palettes the game makes at runtime (fades, hit flashes) never match; those draws stay native.
- `FLD.BIN` (nine files, 0.8 GB) was looked at and holds geometry (float vertex data), no
  textures; every texture seen so far comes from `DAT.BIN` or the executable.

## How the disc stores its textures

Full details in [`extractor.py`](extractor.py)'s docstring. In short:

- `DAT.BIN`, `MOV.BIN` and `FLD.BIN`, indexed by tables inside the executable (DAT.BIN's at
  0xD76B0 in the English v1.0 `SLPS_254.50`), in Tales of Destiny's DAT.TBL entry format. Files are
  Tales-compressed (versions 1 and 3) or raw; packs (`SCPK`, plain offset tables) nest more of them.
- Map sheets are TIM2, 1024x1024 8-bit with several palettes; some palettes are flat silhouettes
  (shadows), which are dropped.
- Sprites are `anp3` files, as in Tales of Destiny (`tools/disc_textures/tales.py`): 4-bit frames
  behind 16-byte records, uploaded one frame at a time into a 256- or 512-wide buffer and drawn as
  composites. The bigger files run their frames past the header's CLUT offset to a 128/256-byte
  CLUT at the very end; reading the header's offset as the CLUT made up to 2,050 junk palettes per
  frame.
- The font is 24x24 4-bit glyphs in the executable just before DAT.BIN's table (English letters,
  digits, punctuation), coloured with a palette that stays resident in VRAM: palette-free images.

## Checksums

| | |
| --- | --- |
| Disc ISO SHA-1 (after `disc.py`) | `75d36646266334e7091268e24fe2fc4abd4a3331` |
| Upscale model SHA-256 (`4x-UltraSharp.safetensors`) | `36a340b5509b699d2c06cb445ddc1d3d39199ac734d889ed6d7915f60e05bcbc` |
| Index `disc-atlas.a2at` SHA-256 (ASTC pack) | not built yet |

## Playing with it

GameDB: the fork's entry sets `disableSafeFeatures` and `forceEvenSpritePosition` for this serial
(the English patch's recommended settings).
