# HD Texture Packs

This fork can load HD textures two ways. They work together: a texture with its own file uses
it, and a disc pack fills in everything else.

| | Standard packs (PCSX2 / ARMSX2) | Disc packs (this fork) |
| --- | --- | --- |
| Where the textures come from | Dumped by the emulator while someone plays | Read straight off the game disc |
| Per-game work | Play through every area with dumping on | Work out the game's disc format and write an extractor (reverse engineering, usually with an AI agent) - once per game |
| After that | Upscale and clean up the dumps | One command per pack: [a recipe](../hd-packs/README.md) |
| Coverage | Whatever the player reached | Every palette texture on the disc |
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
holding that block under the same palette, and takes the one whose crop hashes to the texture's
own name. **A match is exact, never a guess.** The HD texture is that crop of the upscaled image.
For menu sprites the emulator narrows the texture to the sprite's own UV rectangle first, so it
can be found too. Code: `pcsx2/GS/Renderers/HW/GSDiscAtlas.*`.

Verified on the Thor: a pack built from the *original* disc images renders frames bit-identical to
no pack at all, so every crop is exact; and all 35 textures desktop PCSX2 dumped in an Okage scene
have their names reproduced from disc data.

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
- Only palette textures (PSMT8/PSMT4 and their H variants) are matched. True-colour images,
  mipmapped textures and anything the game builds in memory at runtime are not.

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
