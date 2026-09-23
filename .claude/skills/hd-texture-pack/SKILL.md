---
name: hd-texture-pack
description: Build an HD texture pack for a PS2 game straight from its disc image (no gameplay dumping) - extract every texture, upscale on the desktop GPU, build the disc-atlas pack, install it on the Thor and verify it matches exactly. Use for "make an HD pack for X", "HD textures without playing", "why is this texture still blurry", or checking a disc pack.
---

# HD texture packs from the disc

User-facing guide: `docs/hd-texture-packs.md`. Emulator side: `pcsx2/GS/Renderers/HW/GSDiscAtlas.*`
(+ the probe in `GSTextureCache.cpp`, the tight sprite region in `GSRendererHW.cpp`). Tools:
`tools/disc_textures/`. Python venv with everything: `F:\Projects\pcsx2-desktop\.venv` (pycdlib,
numpy, pillow, xxhash, zstandard, torch cu130, spandrel). Models live in
`F:\Projects\pcsx2-desktop\models\` (never in the repo). `chdman` is at `F:\Tools\MAME`.

## 0. Is the game a candidate? (30 minutes, before any tooling)

1. Disc image -> ISO. CHD: `chdman info` first; a CD (`MODE2_RAW`) needs `extractcd` and cutting
   each 2352-byte sector to bytes 24..2072; a DVD CHD uses `extractdvd`. List files with pycdlib.
2. Find the texture container. Grep for `TIM2`; look at the biggest file families and their magic
   bytes. Okage: `.XPF` archives (`XPFX`, 32-byte entries, bit-flag LZ, see `okage_xim.py`) holding
   `.XIM` images (u32 GS TEX0 word -> PSM, 256-entry RGBA palette in index order with PS2 alpha,
   then height, width, linear indices). The PSM in a header word is a strong hint the file is
   GS-ready.
3. Prove it before building anything: in desktop PCSX2 (`tools/pcsx2_mcp`, F9 = single-frame GS
   dump, or Texture Replacement -> Dump Textures) dump one scene's textures. For each dumped
   `TEX0HASH-CLUTHASH[-rWxH]-bits.png`, check that the image is an exact crop of a disc image and
   that `xxh3_64(crop indices, row by row)` == TEX0HASH and `xxh3_64(palette bytes)` == CLUTHASH.
   Okage: 35/35. `bits` 0x2a93 = PSM 0x13, TW=TH=10: the game declares 1024x1024 and uses a clamp
   region (`-rWxH`) or UVs - that is normal, not a bug.

## 1. Extract

Write `tools/disc_textures/<game>_<format>.py` with a `disc_images(iso)` generator (contract in
`okage_xim.disc_images`: key, HxW uint8 indices with 4-bit expanded low nibble first, list of
palette byte strings exactly as the GS gets them) plus a CLI that writes native PNGs + manifest
(copy okage_xim.py's `main`). Check a contact sheet by eye; wrong palette order shows as stray
pixels (Okage's palettes are NOT CSM1-swizzled on disc - test both). Report failures, don't hide
them (Okage had two header variants: true-colour images have no palette block; some image blocks
store the width where the size should be).

## 2. Upscale

`python tools/disc_textures/upscale.py native/ hd4x/ --model 4x-UltraSharp.safetensors` (and
`--scale 2` for a 2x set). Defaults already handle seams (wrap padding for opaque), transparent-
pixel fill and binary alpha. Look at a sheet before shipping: ESRGAN invents detail on tiny/soft
art (Okage's 24 px portraits); swap the model for those if it looks wrong. UltraSharp is CC BY-NC-SA.

## 3. Pack, then prove it exact

```
python tools/disc_textures/build_disc_pack.py <iso> native/ pack_1x/ --extractor <module>
python tools/disc_textures/build_disc_pack.py <iso> hd4x/  pack_4x/ --extractor <module>
```
The 1x pack is the test: replay a GS dump of the game on the Thor with `pcsx2-gsrunner`
(`/data/local/tmp/gsr`, data root `cfg/ARMSX2`, pack under `cfg/ARMSX2/textures/<SERIAL>/replacements`,
`-set EmuCore/GS/LoadTextureReplacements=true -set EmuCore/GS/LoadTextureReplacementsAsync=false`)
with and without it. Frames must be bit-identical. Then the 4x pack must change them.

## 4. Install and verify on the Thor

Push the pack to `/sdcard/armsxdata/textures/<SERIAL>/replacements/` (only `disc-atlas.a2at` when
just the index changed). Dev server (`docs/mcp-server.md`): boot, `load_state`, then
`texture_stats` -> `discAtlasMatches` / `discAtlasMisses`; `hd_test {"pack":false|true}` for A/B
shots (reload the same state before each shot); `gs_dump` to take a scene home to gsrunner.
Measure fps at the resolution the user plays (3x on the 8 Gen 2 Thor) and at 2x fast-forward.

## When a texture is still blurry

1. `texture_dump` on for a few seconds on that screen; read the dumped names.
2. No `-rWxH` and 1024x1024: a UV-selected sheet. With a disc atlas loaded the renderer narrows it
   to the sprite's UV rect - if it did not, check the draw qualifies (palette PSM, TW/TH >= 512,
   UV or clamped ST, no region clamp already).
3. Region one texel too big (`r257x320` for a 256-wide image): the last-texel rounding.
4. Palette hash not on disc: the game builds that palette at runtime (Okage's menu font). Not
   supported yet - needs palette-independent matching.
5. `lod` present: only single-level keys are matched.
6. True-colour images: not supported yet.

## Pitfalls already paid for

- `codex exec -i a.png b.png "prompt"` eats the prompt as an image; pipe the prompt on stdin.
- Pull device screenshots only after the dev server returns (it now waits for IEND); older builds
  returned early and pulls were truncated.
- A texture reload while the upscaler worker was busy crashed the app (fixed: shared_ptr sets).
- Pack index v2 = blocks every 8 px (PSMT8H/PSMT4HL sheets sit on an 8x8 grid); v1 indexes miss
  menu sprites.
