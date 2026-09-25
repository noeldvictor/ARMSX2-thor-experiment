---
name: hd-texture-pack
description: Build an HD texture pack for a PS2 game straight from its disc image (no gameplay dumping) - add a recipe folder under hd-packs/ (extractor for the game's disc format), upscale on the desktop GPU, build the disc-atlas pack, install it on the Thor and verify it matches exactly. Use for "make an HD pack (recipe) for X", "HD textures without playing", "why is this texture still blurry", or checking a disc pack.
---

# HD texture packs from the disc

User guide: `hd-packs/README.md` (recipes, requirements, adding a game); how it works:
`docs/hd-texture-packs.md`. Emulator side: `pcsx2/GS/Renderers/HW/GSDiscAtlas.*` (+ the probe in
`GSTextureCache.cpp`, the tight sprite region in `GSRendererHW.cpp`). Tools: `tools/disc_textures/`
(`make_pack.py` runs everything). Each game is a recipe folder `hd-packs/<SERIAL>-<name>/`:
`game.json`, `extractor.py`, `README.md`, `media/` - copy `hd-packs/SCUS-97129-okage/`. Python venv with everything: `F:\Projects\pcsx2-desktop\.venv` (pycdlib,
numpy, pillow, xxhash, zstandard, torch cu130, spandrel). Models live in
`F:\Projects\pcsx2-desktop\models\` (never in the repo). `chdman` is at `F:\Tools\MAME`.

## 0. Discover how the game stores its textures (the tools do most of it)

Shared tools in `tools/disc_textures/`, all game-independent:

| Tool | What it does |
| --- | --- |
| `disc.py` | CHD/CUE/ISO -> ISO (DVDs stored as a CD CHD with one MODE1/2048 track too) |
| `disc_codecs.py` | Decompressors, numba-compiled when numba is installed (`pip install numba`; pure Python is ~100x slower). `tales_lzss` + `find_tales_blobs` (Namco Tales games, nested blobs at any offset) |
| `tim2.py` | Finds and reads TIM2 anywhere in a buffer; lenient about writers that leave fields 0 (Namco) |
| `gsdump.py` | Lists every texture upload in a PCSX2 GS dump; `--locate DIR` finds each upload's bytes in unpacked disc files (or in a `corpus.bin` + `corpus.json` made from them); `--timeline [--tbp A-B]` lists uploads and textured draws in order |
| `gsmem.py` | GS memory swizzle (write/read any rect in CT32/16/T8/T4/H formats) and VRAM out of a GS dump's state |
| `verify_dumps.py` | The candidate test: an extractor vs a folder of PCSX2 texture dumps -> exact / pixels / palette only / no palette |

The loop, on desktop PCSX2 (`tools/pcsx2_mcp`, see `docs/cheat-tooling.md` for the lab):
1. Set `[EmuCore/GS] DumpReplaceableTextures/DumpDirectTextures/DumpPaletteTextures = true` in
   the desktop ini (back it up; restore after). Boot the game (`cli launch <iso>`), get to a few
   different scenes (title, field, battle, menu), and `press gs_dump` (F9) in each. Save a state.
2. Disc -> ISO, list the files (pycdlib), look at the biggest file families and magic bytes.
   Decode the archives: a known codec from `disc_codecs.py` first; the fan-translation community
   has usually documented a game's archive (Tales: comptoe). Unpack everything decoded (and
   every nested compressed blob) into a folder.
3. `gsdump.py DUMP --locate UNPACKED_DIR` - where each uploaded texture's bytes live. That names
   the container format far faster than reading files cold. Games re-upload textures every frame
   more often than not, so a single-frame dump holds most of a scene.
4. Palettes that stay resident are not in the frame's uploads: read them out of the dump's VRAM
   (`GSMemory.from_dump(state).read(PSMCT32, TEX0.CBP, 1, 0, 0, 16, 16)` -> unswizzle CSM1) and
   search for those bytes. Confirm the VRAM read first: hash the palettes the draws use and
   compare with the CLUT hashes in the texture dump names (Tales of Destiny: 17/22).
5. Write the extractor, then `verify_dumps.py extractor.py GAME.iso DUMPS_DIR`. A candidate game
   lands most dumps in `exact` + `pixels`. Okage: 35/35 exact. Work down the `no palette` list -
   it names what the extractor is missing.

Things that look like dead ends but are not:
- Size-correct decompression can still be wrong. Tales LZSS decodes to the exact size with the
  window start off by one and garbles everything after the first run; check structure (a pack's
  own offset table pointing at valid TIM2 headers), not just size.
- Data uploaded as PSMCT32 and drawn as PSMT8/4 (fewer, bigger transfers): the disc holds the
  32-bit view. `gsmem.py` writes it as the upload did and reads it as the draw does. Check with
  `gsdump.py --timeline` before assuming it: Tales of Destiny's 32-bit title uploads looked like
  that and were plain true-colour pictures, drawn as PSMCT32.
- A 4-bit image with several palettes (any CLUT of 32+ entries) uses 16-colour palettes that are
  8x2 patches of the stored 16-wide CLUT = 16 consecutive entries after CSM1 unswizzle. Tales of
  Destiny's title text (48 colours) missed every palette until small CLUTs got the same rule.
- Fonts, text strips and fades have palettes built at runtime: palette-free images (step 1).
- Composites: a game that assembles one texture in VRAM out of several uploads (sprite batches,
  text from glyphs) draws regions that are no single disc image. The emulator splits those into
  disc images itself (`Disc atlas: composite #...`), so the extractor only has to yield the
  pieces. Before calling something a composite, check it: Tales of Destiny's 512x512 maps looked
  like one and were single TIM2s the reader had skipped (`gsmem` + the dump's VRAM: search each
  quadrant's rows on the disc).

## 1. Extract

Create `hd-packs/<SERIAL>-<name>/extractor.py` with `disc_images(iso)` (contract in
`tools/disc_textures/extract_native.py`: key, HxW uint8 indices with 4-bit expanded low nibble
first, list of palette byte strings exactly as the GS gets them; an empty list = palette-free).
Only disc-format code goes there; `extract_native.py` writes the PNGs for every game. Run it and
check a contact sheet by eye; wrong palette order shows as stray pixels (Okage's palettes are NOT
CSM1-swizzled on disc - test both). Report failures, don't hide them (Okage had two header
variants: true-colour images have no palette block; some image blocks store the width where the
size should be).

Palette-free images (fonts the game colours at runtime): yield them with no palette and add
`palette_free_palette(key)` returning the game's real runtime palette. Build once with the grey
ramp default, open that screen, and read `Disc atlas: palette <hash>: ...` from the log (u32
0xAABBGGRR per entry). The upscaler only sees colours; a stand-in where neighbouring indices look
alike upscales into the wrong indices (Okage's index-1 ink came out hollow on a grey ramp). If
several layouts are plausible, emit each under its own key once, see which one the exact match
picks, then keep only that one.

## 2. Upscale

`make_pack.py` runs it (`upscale.py native/ hd4x/ --model 4x-UltraSharp.safetensors`; `--scale 2`
for a 2x set). Defaults already handle seams (wrap padding for opaque), transparent-pixel fill and
binary alpha. Look at a sheet before shipping: ESRGAN invents detail on tiny/soft art (Okage's
24 px portraits); swap the model for those if it looks wrong. UltraSharp is CC BY-NC-SA.
Okage on the RTX 3060: ~3.5 textures/s at 4x; small textures are batched per model call, so
Tales of Destiny's sprites run at ~11/s. Read the size estimate `make_pack.py` prints after the
extract step before starting a long upscale: 16 bytes per native texel at 4x.

## 3. Pack, then prove it exact

Packs are ASTC by default (`astcenc` from Arm's astc-encoder: on PATH, `ASTCENC`, or `--astcenc`;
the PC copy is `F:\Projects\armsx2-thor\raisr-data\tools\bin\astcenc-avx2.exe`). ASTC is lossy,
so the 1x exactness pack is built with `--format png`; check the ASTC pack against the PNG one
(same match counts, frames ~50 dB PSNR).

```
python tools/disc_textures/build_disc_pack.py hd-packs/<game>/extractor.py <iso> native/ pack_1x/
python tools/disc_textures/make_pack.py hd-packs/<game> --disc <chd> --model <model>   # the 4x pack
```
The 1x pack is the test: replay a GS dump of the game on the Thor with `pcsx2-gsrunner`
(`/data/local/tmp/gsr`, data root `cfg/ARMSX2`, pack under `cfg/ARMSX2/textures/<SERIAL>/replacements`,
`-set EmuCore/GS/LoadTextureReplacements=true -set EmuCore/GS/LoadTextureReplacementsAsync=false`)
with it and with an *empty* pack (`struct.pack("<4sIIIIIQII", b"A2AT", 4, 0, 0, 16, 8, 40, 0, 0)` as
`disc-atlas.a2at`). Frames must be bit-identical. Not against no pack: a loaded atlas narrows menu
sprites to their UV rect, which moves bilinear sprite edges by a pixel. Add
`-set EmuCore/GS/hw_mipmap=false` when the game mipmaps (Tales of Destiny's floors): the
replacement's generated mip levels differ slightly from the native texture's, a filtering
difference, not a wrong match. Then the 4x pack must change them. The log lists the first 16
misses (`Disc atlas: miss WxH psm ...`): the list of what the extractor or the matcher lacks.

Never change the extractor or `tim2.py` between the extract and build steps of one pack: the HD
files are named by key and palette number, so a changed palette order paints HD images with the
wrong palette (Tales of Destiny's first 4x pack had orange UI corners from exactly this). Rebuild
from `--from extract`.

## 4. Finish the recipe folder

Do one clean `make_pack.py` run into a fresh `--work` folder and put its numbers in the recipe:
the per-step times from `timings.json` with the PC's CPU/RAM/GPU, sizes of each output, the
ISO SHA-1, model SHA-256 and index SHA-256 (also in `game.json` `disc.iso_sha1` and
`reference.index_sha256`). Before/after shots at 3x go in `media/`. List what the pack covers
and, honestly, what it doesn't. The README follows `hd-packs/TEMPLATE.md` (before/after, build
estimate, coverage and gaps, format notes, checksums). Add the game to `hd-packs/GAMES.md` (status,
thumbnail, pack size, time, coverage) - and a game checked and dropped goes in its last table,
with why. Never commit textures or packs.

## 5. Install and verify on the Thor

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
4. Palette hash not on disc: the game builds that palette at runtime (Okage's menu font). Yield
   that image palette-free (see step 1); it then matches by TEX0 alone.
5. `lod` present: only single-level keys are matched.
6. True-colour images: PSMCT24 (yield an HxWx3 RGB array; set `TRUE_COLOUR_AEM` from a dump
   name - bit 23 of the last field) and PSMCT32 (HxWx4 RGBA, PS2 alpha) are supported. PSMCT16
   is not.
7. Never "fix" a miss by changing PCSX2's texture hash: standard packs and dumps depend on it. The
   atlas checks matches with its own content hash, so raw-block keys are already covered.

## Pitfalls already paid for

- A 4x pack can be wrong where the 1x proof is right: ASTC is lossy in alpha, and River King
  alpha-tests `EQUAL 0x80` on almost every draw - 41% of opaque texels decoded to 0x7F/0x81 and the
  scene speckled. `astc.py` now verifies alpha per image and falls back to PNG. After building a
  4x pack, replay it on the Thor and look at the frames, not just the match counts; and check a
  new game's alpha tests (`TEST` register in a GS dump) before trusting a lossy format.

- Namco's TIM2 writer leaves width and height 0 - both, in Tales of Destiny's 128x128 skies and
  1024x1024 map atlases (12,868 pictures). `tim2.py` assumes the power-of-two square that fits the
  image size; before that, the whole field art was missing and nothing said so. Compare the
  extract's image count and sizes with what the scenes draw.
- A big index (1 GB+) is fine: the emulator maps it. The size to watch is the 4x pack.
- A composite is assembled all-ASTC or all-PNG. One that mixes the two - an ASTC pack whose
  exact-alpha check kept some images as PNG - fails to load and the whole texture stays native
  (`Failed to cut disc atlas crop a2at:c:N` in the replay log; the match counters still look
  fine). Tales of Rebirth kept 65% as PNG and draws its split map sheets and sprites as
  composites, so the field stayed native at 4x; it is built with `--format png` (about the same
  size). Count `Failed to cut` lines in every 4x replay, and check a game's alpha tests first
  (`TEST` in a GS dump: Rebirth uses `GEQUAL 0x80`, River King `EQUAL 0x80` - both need exact alpha).
- Count palettes before upscaling. An image yields one HD image per palette, and map sheets with
  several palettes multiply the pack: Tales of Rebirth's 604 sheets carry 3.4 palettes each (39 GB
  at 4x). Check what the palettes are first - junk from a misread CLUT extent (Rebirth's bigger
  `anp3` files: up to 2,050 "palettes" per frame), flat silhouettes (drop them), or real region
  palettes. One HD index map shared by all palettes only works when they are lighting variants of
  one another; region palettes make a wrongly guessed block a garbage square.
- A font that uploads variable-height glyphs may upload more rows than the glyph has, or fewer
  than it stores: Legendia stores rows rounded up to even and bytes to 16 and uploads the even
  rows. Build the texture the way the upload does, and compare against dumps by colour with a
  palette rebuilt from the dumps, not by alpha mask (index-with-alpha-0 texels hide in masks).
- Numbers that look like register writes can be float data: Rebirth's `FLD.BIN` had 1,879
  "BITBLTBUF" qwords, all vertex floats. Look at the qwords around a hit before chasing it.
- Build gsrunner only with Gradle's ninja (`<Sdk>/cmake/3.22.1/bin/ninja.exe`); another ninja
  version resets `.ninja_log` and the next build recompiles everything. In Git Bash set
  `MSYS_NO_PATHCONV=1` before adb commands with device paths, or pushes land nowhere.
- A composite piece may differ from the texture in 1/256 of its texels (they stay native), and a
  composite is kept at half coverage or a 32x32 sprite's worth: UV bounding boxes of sprite
  batches span data the draws never sample.

- A tool module named `codecs.py` shadows Python's stdlib `codecs` and silently imports the wrong
  one. Compile-check every script after an edit
  (`for f in tools/disc_textures/*.py hd-packs/*/extractor.py; do python -m py_compile "$f"; done`) -
  a broken `disc.py` was pushed once.
- Searching 100k+ small files one by one is slow on Windows; concatenate them once (gsdump.py
  --locate does) and search the blob.

- `codex exec -i a.png b.png "prompt"` eats the prompt as an image; pipe the prompt on stdin.
- Pull device screenshots only after the dev server returns (it now waits for IEND); older builds
  returned early and pulls were truncated.
- A texture reload while the upscaler worker was busy crashed the app (fixed: shared_ptr sets).
- Pack index v2 = blocks every 8 px (PSMT8H/PSMT4HL sheets sit on an 8x8 grid); v1 indexes miss
  menu sprites. v3 adds palette-free images (flag, free-tile table, HD index map with the index in R).
- A candidate layout that is off by a few bytes can still "match": a shift of 4 bytes in a 4bpp
  sheet moves the crop 8 px, so the exact match finds it at another x. Prefer the layout the file
  header explains (Okage fonts: the 4-byte header) over whichever variant matched first.
