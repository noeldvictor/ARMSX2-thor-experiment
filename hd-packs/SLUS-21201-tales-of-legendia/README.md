# Tales of Legendia (SLUS-21201) - disc HD texture pack recipe

**Status:** in progress. NTSC-U disc (the ReUndub v1.4 build - undubbing changes audio, not
textures). The extraction is proven exact on four scenes; the 4x pack is being built.

A 4x HD pack for Tales of Legendia, built from your own disc. No gameplay dumping: every texture
is read off the disc, upscaled with 4x-UltraSharp and matched exactly by the emulator. How that
works: [docs/hd-texture-packs.md](../../docs/hd-texture-packs.md).

## Before / after

Not yet: screenshots come once the 4x pack has been judged at 3x on the Thor.

## Make it

Requirements: [hd-packs/README.md](../README.md#making-a-pack-from-a-recipe). From the repository
root:

```bash
python tools/disc_textures/make_pack.py hd-packs/SLUS-21201-tales-of-legendia \
    --disc "Tales of Legendia (USA).chd" --model 4x-UltraSharp.safetensors
```

### Measured

Not yet. The extract takes about 6 minutes (~19,600 images, 563 M texels); the 4x pack will be
about 9 GB (16 bytes a native texel, before PNG fallbacks for exact alpha).

## Coverage

A 1x pack against an empty pack, `gsrunner` replays of desktop GS dumps on the Thor at 3x,
2026-09-24: every frame bit-identical in all four scenes (title, main menu, the boat, a portrait
conversation), so every match is exact. What does not match yet is listed below.

Against the 179 textures desktop PCSX2 dumped in those scenes, 66 reproduce from disc data - every
8-bit texture (maps, models, the skit portraits). The rest:

## Not yet

- **The dialogue font**: 67 of the dumps are single glyphs, one 64x64 4-bit texture each,
  coloured with a runtime palette. The glyphs are in `SYS_REG.AFS`'s `system_regident.mcd`
  (4-bit, 32 pixels wide, variable height with small per-glyph records); the table is not decoded
  yet.
- **True-colour UI sheets**: the title screen's 1024x256 PSMCT32 sheets (42 dumps) are not checked
  yet; PCSX2 names them by raw GS memory, so they match only if the disc data covers the whole
  sheet.
- Four small 4-bit textures whose palette is not paired right yet.
- Only four scenes are checked.

## How the disc stores its textures

Full details in [`extractor.py`](extractor.py)'s docstring. In short:

- CRI AFS archives (`/AFS/*.AFS`); every entry a `CPS` file - a 16-byte header and Namco's Tales
  compression, version 1. The ISO's UDF bridge is stale after patching, so the disc is read with
  `isofs.py` (ISO 9660 only).
- The unpacked files hold no images: they hold the GS packets that upload them. Each texture
  goes up as PSMCT32 data into one streaming slot (block 0x3A80, its CLUT at 0x3A70) and is drawn
  from it as PSMT8/PSMT4. `gifscan.py` finds the uploads and TEX0 writes in any file and reads each
  texture back through the GS swizzle the way the draw does.
- Things that cost time: a 16x16 PSMCT32 upload drawn as PSMT8 is a 32x32 texture, not 64x16 (the
  block layouts differ - the size comes from marking the memory and reading it back); models
  upload the CLUT before the image, portraits after it, so both candidates are kept and the exact
  match picks; skit files set TEX0 before the upload, and one upload holds two 256x256 portrait
  frames, each a crop of the whole sheet.

## Checksums

| | |
| --- | --- |
| Disc ISO SHA-1 (after `disc.py`) | `9279255de74f48686c7c5084fb7b3a580d67d688` |
| Upscale model SHA-256 (`4x-UltraSharp.safetensors`) | `36a340b5509b699d2c06cb445ddc1d3d39199ac734d889ed6d7915f60e05bcbc` |
| Index `disc-atlas.a2at` SHA-256 (ASTC pack) | not recorded yet |
