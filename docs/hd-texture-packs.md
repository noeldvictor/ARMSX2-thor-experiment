# HD Texture Packs

This fork can load HD textures two ways. They work together: a texture with its own file uses
it, and a disc pack fills in everything else.

| | Standard packs (PCSX2 / ARMSX2) | Disc packs (this fork) |
| --- | --- | --- |
| Where the textures come from | Dumped by the emulator while someone plays | Read straight off the game disc |
| Per-game work | Play through every area with dumping on | Work out the game's disc format and write an extractor (reverse engineering, usually with an AI agent) - once per game |
| After that | Upscale and clean up the dumps | One command per pack: [a recipe](../hd-packs/README.md) |
| Coverage | Whatever the player reached | Every palette, 24-bit and 32-bit texture on the disc |
| Files in the pack | One PNG per texture, named by its hash | The whole upscaled disc images + one index file |
| Works in stock PCSX2 | Yes | No, this fork only |
| Games | Any | Only games someone has written a recipe for: [hd-packs/](../hd-packs/README.md) |

Both go in the same folder: `<DataRoot>/textures/<SERIAL>/replacements/`.

## Using a disc pack

1. Unzip the pack `make_pack.py` made into `<DataRoot>/textures/`, so
   `textures/<SERIAL>/replacements/` holds `disc-atlas.a2at` and an `atlas/` folder.
2. Texture packs are on by default (Settings -> Graphics, "Load texture replacements").
3. For a 1080p screen play at 3x internal resolution (the Thor default on Snapdragon 8 Gen 2);
   a 4x pack has the detail for it.

The log says `Disc atlas: N disc images ...` at boot and `Disc atlas: match #...` as textures
are replaced.

## How it works

On the PS2, what a draw samples is a rectangle of an image the game loaded from the disc.
Okage's are all exact crops of its `.XIM` files, mostly pieces of 256x256 atlases picked by a
clamp region, and menus pick sprites out of a big sheet by UV. PCSX2 names a texture by
**XXH3 over the rectangle's palette indices** plus XXH3 of the palette, so that name can be
computed from the disc.

A disc pack ships each upscaled disc image whole, plus `disc-atlas.a2at`: every image's palette
hash, size and palette indices, and the hash of every 16x16 block every 8 pixels. When a texture
has no file of its own, the emulator hashes one 16x16 block of it, looks up the disc images
holding that block under the same palette, and takes the one whose crop hashes the same as the
texture's own pixels. **A match is exact, never a guess.**

That last check uses the pack's own hash - XXH3 over the texture's texels as the GS reads them
(palette indices, or RGBA as expanded) - not PCSX2's texture name. PCSX2's name is the same bytes
for a region texture but raw GS memory blocks for some full-size ones, which the disc cannot
reproduce; the pack's own hash does not care which. PCSX2's names are never changed: a match is
registered under the texture's normal name, standard packs and dumps are untouched, and the extra
hash is only computed for a texture with no file of its own once a probe block has a candidate. The HD texture is that crop of the upscaled image.
For menu sprites the emulator narrows the texture to the sprite's own UV rectangle first, so it
can be found too. Code: `pcsx2/GS/Renderers/HW/GSDiscAtlas.*`.

Verified on the Thor: a pack built from the *original* disc images renders frames bit-identical to
an empty pack (an index with no images), so every crop is exact; and all 35 textures desktop PCSX2
dumped in an Okage scene have their names reproduced from disc data. Compare with an empty pack,
not with no pack: while any disc pack is loaded, menu sprites are narrowed to their UV rectangle,
so bilinear filtering at a sprite's edge clamps instead of reading the next texel of the sheet -
a one-pixel difference at sprite edges that has nothing to do with the replacements.

## ASTC

A 4x pack stores its HD images as ASTC 4x4 (`atlas/*.astc`, index version 5): 8 bits a pixel, a
quarter of RGBA8, sampled natively by the Thor's GPU (every Android GPU has ASTC LDR). The
emulator never decodes them - at 4x one native texel is exactly one 4x4 block, so a crop is a copy
of whole blocks, and a composite copies each piece's blocks and fills every uncovered texel with a
constant ("void-extent") block of its native colour. Palette-free index maps stay PNG (the
emulator paints them per pixel). Okage: 470 MB instead of 632 MB on disk, a quarter of the GPU
memory, the same matches in every test scene, frames at 47-55 dB PSNR against the PNG pack.
`--format png` builds a lossless pack - for the 1x exactness test and for 2x packs, where crops do
not land on the block grid. Encoding is Arm's `astcenc`, many images per call (`astc.py`).

## Composites (textures a game assembles in VRAM)

Many games do not draw a disc image as it is: they upload several - sprite frames into one sheet,
glyphs into a line of text - and a single draw samples a region spanning several of them. No crop
of any one disc image equals that region. When a texture misses, the emulator splits it instead:
every 16x16 block of the texture that some disc image holds votes for that image at one position,
and a placement is accepted if the texels it covers equal the texture, with up to 1/256 of them
allowed to differ. Those, and texels no accepted image covers, keep their native colour. The HD
texture is the HD images laid out the same way, so every texel shown is either an exact match or
native, and a composite is as exact as a crop - a 1x pack still renders bit-identical frames.
Tales of Destiny DC's ship scene: sprite batches of 2-4 frames (143x31, 63x31, 87x87) match, and
the deck map, which differs from its disc image in 11 texels the game parks in the texture's
memory, is a one-image composite (`11 texels native`). Palette textures only;
`Disc atlas: composite #...` in the log.

## True-colour images

24-bit (PSMCT24) textures have no palette; PCSX2 hashes them expanded to 32 bits, the RGB plus
an alpha the GS makes from TEXA: TA0, or 0 for black when AEM is set. That is computable from
the disc too - all 8 PSMCT24 textures dumped from Okage's World Library have their names
reproduced from disc RGB with TA0 0x80 and AEM on. The pack stores their RGB (index version 4)
and indexes their blocks by RGB alone, so the probe does not depend on TEXA; the exact check
rebuilds the alpha from the key's TEXA, and the loader applies it to the HD image. An extractor
flags `TRUE_COLOUR_AEM` when the game uses AEM, so black is transparent before the upscale and
the model does not smear it into the edges.

32-bit (PSMCT32) images keep their RGBA in the index and carry their own alpha. PCSX2 names a
full-size PSMCT32 texture by its raw GS blocks, which is why the check uses the pack's own hash.
16-bit textures are not handled yet.

## Alpha above 0x80

PS2 alpha is 0..0x80 for 0..1 and can go up to 0xFF. The upscaler wants 0..255, so the tools
double it - which would clip anything above 0x80. Images whose texels use such alpha (16 of
Okage's palettes; the steam from the pot in a house is one) keep the raw PS2 value through the
upscale instead; `raw_alpha()` in `extract_native.py` decides, the same way on both sides.

## Making a pack

Recipes, requirements, timings and how to add a game: [hd-packs/README.md](../hd-packs/README.md).
The tools are in `tools/disc_textures/`; only `hd-packs/<game>/extractor.py` is game-specific.

## Palette-free images (fonts)

Some images have no palette on the disc: the game makes one at runtime. Okage's menu fonts are
like that. Their indices are still on the disc, and the index hash (TEX0) does not depend on the
palette, so the pack indexes their blocks by TEX0 alone (index version 3). Instead of an HD
colour image it ships an HD *index map*: the font painted with a stand-in palette, upscaled, and
each pixel mapped back to the nearest palette entry. At runtime the emulator paints that map with
whatever palette the game is using, so recoloured text stays right.

The stand-in palette matters. The upscaler only sees colours: Okage's ink is index 1, and on a
plain grey ramp that is nearly black next to the transparent index 0, so the model shaved the
letters hollow. The fix is the game's real palette, which the emulator logs on the first match
(`Disc atlas: palette <hash>: ...`); the extractor returns it from `palette_free_palette()`.

## Testing a pack on the device

The dev server (`docs/mcp-server.md`) has what you need:

- `hd_test {"pack": false}` / `{"pack": true}` switches the pack live and reloads textures;
  `{"filters": false}` also turns off the texture upscaler and bilinear filtering, so only the
  pack changes the picture.
- `texture_stats` reports `discAtlasImages`, `discAtlasMatches` and `discAtlasMisses`.
- `gs_dump {"frames": 1}` saves the current frame; `pcsx2-gsrunner` replays it on the device with
  and without the pack for exact comparisons.

## Known limits

- Upscaling a whole atlas lets neighbouring pieces blend a pixel or two into each other's edges;
  a repeating floor can show a faint seam. The fix is to upscale known pieces separately.
- ESRGAN-type models invent detail on tiny or soft art (Okage's 24-pixel portraits). Pick the
  model per kind of art if it matters.
- Palette textures (PSMT8/PSMT4 and their H variants), palette-free fonts, PSMCT24 and PSMCT32
  are matched. PSMCT16, mipmapped textures and anything the game builds in memory at runtime are
  not.

## Sharing a pack

The textures are the game's art (upscaled), so a pack is a derivative of a copyrighted game - the
same position every HD pack in the community is in. Model licences matter too: 4x-UltraSharp is
CC BY-NC-SA 4.0, so a pack made with it is non-commercial. Places people share packs:

- a GitHub repository's Releases (files up to 2 GB each; the EmuCoreX-Textures catalogue the app
  reads works this way, so a pack there can be listed in it);
- the Internet Archive or a Hugging Face dataset for large packs;
- the PCSX2 HD texture threads on GBAtemp and the ARMSX2 / NetherSX2 Discords.

A disc pack only works in this fork, so say so wherever it is posted. Sharing the *recipe* instead
avoids the question: it holds no art, and anyone with the disc rebuilds the same pack
(`make_pack.py` checks the index checksum against the recipe's).
