# Tales of Destiny: Director's Cut (SLPS-25842) - disc HD texture pack recipe

**Status:** in progress. The English fan-translated disc (v1.6). The extraction and the matching
are proven exact on two scenes; the 4x pack is being built and has not been judged on the Thor
yet.

A 4x HD pack for Tales of Destiny: Director's Cut, built from your own disc. No gameplay dumping:
every texture is read off the disc, upscaled with 4x-UltraSharp and matched exactly by the
emulator. How that works: [docs/hd-texture-packs.md](../../docs/hd-texture-packs.md).

## Before / after

Not yet: screenshots come once the 4x pack has been judged at 3x on the Thor.

## Make it

Requirements: [hd-packs/README.md](../README.md#making-a-pack-from-a-recipe). From the repository
root:

```bash
python tools/disc_textures/make_pack.py hd-packs/SLPS-25842-tales-of-destiny-dc \
    --disc "Tales of Destiny DC.chd" --model 4x-UltraSharp.safetensors
```

Install: unzip `work/SLPS-25842-disc-hd4x.zip` into `<DataRoot>/textures/`.

### Measured so far (not a clean run)

On the Core i7-11700 / 32 GB / RTX 3060 12 GB, 2026-09-23, with Android builds running on the same
PC part of the time, so these are upper bounds:

| Step | Time | Output |
| --- | --- | --- |
| extract | 2-3 min | `native/`: 101,328 PNGs (862 M texels) |
| upscale 4x | 2 h 15 min for the first 99,606 images (small textures batched, ~11 a second), then ~18 s for each of the 328 1024x1024 map atlases (~1 h 40 min) | `hd4x/` |
| build, zip | not measured yet | ~13.6 GB installed (estimate: 16 bytes per native texel) |

It is a big pack because the game has a lot of art: 328 field and town map atlases of 1024x1024
are 5.5 GB of it, 256x256 and 512x512 textures another 3.8 GB. An audit found 0.2 GB of
duplicates and blank images, which the build leaves out; the rest is distinct art. Plan for
tens of GB of free disk for the work folder.

## Coverage

A full 1x pack (every disc image at native size) against an empty pack, `gsrunner` replays of
desktop GS dumps on the Thor at 3x, 2026-09-23 (with `hw_mipmap=false`: the deck floor is
mipmapped):

| Scene | Textures matched | 1x vs empty pack |
| --- | --- | --- |
| Ship's deck | 47 of 47 | bit-identical |
| Title screen | 16 of 17 (the miss is a 1024x1024 32-bit texture, most likely one the game renders; not checked) | bit-identical |

What that covers: map textures and the field/town map atlases, character sprites (single frames
and the batches the game assembles in VRAM), the font (2,013 glyphs from the executable), the
title art (true colour) and the title menu text.

## Not yet

- **The 4x pack**: not judged on the Thor, no screenshots, no clean timings.
- **Only two scenes are proven.** Towns, battles, menus and cut-ins have not been dumped yet.
- **Size**: ~13.6 GB installed. The index alone is 1.1 GB; the emulator memory-maps it (about
  40 MB resident on the Thor), but it is still download and disk.
- The first 4x pack (3.1 GB, PNG, before most of the fixes below) had wrongly coloured UI corners
  because the palette slicing changed between its extract and build steps.

## How the disc stores its textures

Full details in [`extractor.py`](extractor.py)'s docstring. In short:

- Everything but the movies is in `DAT.BIN`, indexed by `DAT.TBL`. Files are Namco's Tales
  compression (an LZSS; the window start differs by version, and getting it one byte wrong still
  decodes to the right size) or raw, and decoded files hold more compressed sub-files at any
  offset (`disc_codecs.find_tales_blobs`).
- Textures are TIM2 files written by Namco's own tool, which leaves the picture count, header
  size, CLUT size - and in 12,868 pictures both width and height - at 0. `tim2.py` is lenient
  about all of it; until it took the power-of-two square for a picture with no size, the game's
  whole field art (the 1024x1024 map atlases) was missing and nothing said so.
- 4-bit pictures with several palettes use 8x2 patches of a 16-wide CLUT, even small ones: the
  title text's 48-colour CLUT is drawn as entries 0-7 + 16-23 and 8-15 + 24-31.
- Character sprites are `anp3` files: frames back to back before one CLUT, every palette of it
  bound (one HD image per palette).
- The font is in the executable: 24x24 4-bit glyphs the game colours with a palette it makes at
  runtime, read out of a GS dump's VRAM.
- The game assembles textures in VRAM - sprite batches, text lines, and a party sprite page whose
  UV box also spans unrelated data - and parks a few bytes inside one deck map. The emulator's
  composites cover all of these; nothing game-specific was needed.
- Dead end worth knowing: the title's 32-bit uploads looked like 8-bit data in disguise; the
  upload/draw timeline (`gsdump.py --timeline`) showed they are plain true-colour pictures.

## Checksums

| | |
| --- | --- |
| Disc ISO SHA-1 (after `disc.py`) | `de934800cd81835a5f5335a0eab58299a16371eb` |
| Upscale model SHA-256 (`4x-UltraSharp.safetensors`) | `36a340b5509b699d2c06cb445ddc1d3d39199ac734d889ed6d7915f60e05bcbc` |
| Index `disc-atlas.a2at` SHA-256 (ASTC pack) | not recorded yet |

## Playing with it

Not judged yet. The target is the same as Okage's: 3x internal resolution on the Thor, RAISR-HD
left on, 2x fast-forward.
