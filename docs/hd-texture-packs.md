# HD Texture Packs

This fork can load HD textures two ways. They work together: a texture with its own file uses
it, and a disc pack fills in everything else.

| | Standard packs (PCSX2 / ARMSX2) | Disc packs (this fork) |
| --- | --- | --- |
| Where the textures come from | Dumped by the emulator while someone plays | Read straight off the game disc |
| What you have to do | Play through every area with dumping on | Run three tools; no playing |
| Coverage | Whatever the player reached | Every texture on the disc |
| Files in the pack | One PNG per texture, named by its hash | The whole upscaled disc images + one index file |
| Works in stock PCSX2 | Yes | No, this fork only |
| Per-game work | None | An extractor for the game's disc format |

Both go in the same folder: `<DataRoot>/textures/<SERIAL>/replacements/`.

## Using a disc pack

1. Copy the pack's `replacements` folder to `<DataRoot>/textures/<SERIAL>/`, so it holds
   `disc-atlas.a2at` and an `atlas/` folder.
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

## Making a disc pack (Okage: Shadow King)

Needs Python with `pycdlib numpy pillow xxhash`, and for the upscale a CUDA GPU with `torch
spandrel` (an RTX 3060 does Okage's 2,807 textures in about 6 minutes).

```bash
# 1. Disc image -> ISO (MAME's chdman for .chd; a CD image needs its 2352-byte sectors cut to 2048)
chdman extractcd -i "Okage Shadow King.chd" -o okage.cue -ob okage.bin
python -c "import os;f=open('okage.bin','rb');o=open('okage.iso','wb');[o.write(f.read(2352)[24:2072]) for _ in range(os.path.getsize('okage.bin')//2352)]"

# 2. Every texture off the disc -> PNG (+ manifest.json)
python tools/disc_textures/okage_xim.py okage.iso native/

# 3. Upscale on the GPU (default model 4x-UltraSharp; see the script for padding/alpha choices)
python tools/disc_textures/upscale.py native/ hd4x/ --model 4x-UltraSharp.safetensors

# 4. Pack: whole HD images + the index
python tools/disc_textures/build_disc_pack.py okage.iso hd4x/ pack/
#    -> copy pack/ to <DataRoot>/textures/SCUS-97129/replacements/
```

Try the pack before the upscale: build it from `native/` instead of `hd4x/`. That 1x pack must
render exactly like no pack; any difference is a bug.

## Making one for another game

Only step 2 is per game. Everything after it takes the same input: for each image on the disc,
its **palette indices** (one byte per texel, row-major, 4-bit formats expanded low nibble first)
and its **palette(s)** exactly as the GS receives them (RGBA, alpha on the PS2 scale where 0x80 is
opaque, in index order). Write a module in `tools/disc_textures/` with a `disc_images(iso)`
generator yielding those (the contract is in `okage_xim.disc_images`) and pass it with
`build_disc_pack.py --extractor <module>`.

Finding the format:

- Look for known containers first: `TIM2` headers are common. Okage uses its own (`.XPF`
  archives with a small LZ, `.XIM` images); the notes in `tools/disc_textures/okage_xim.py` show
  what "reverse the format" looked like in practice - a few hours, not weeks.
- Prove the idea before the tooling: in desktop PCSX2 dump the textures of one scene (Texture
  Replacement -> Dump Textures), then check that each dumped image is a crop of some disc image
  and that its name's two hashes come out of `xxh3_64(indices of the crop)` and
  `xxh3_64(palette)`. Names ending `-rWxH` are region textures and the easiest case.
- Palette textures (PSMT8/PSMT4 and their H variants) are supported today. True-colour images and
  palettes the game builds at runtime (recoloured fonts) are not yet.

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
- Menu text whose colours the game makes at runtime has no disc image to match.

## Sharing a pack

The textures are the game's art (upscaled), so a pack is a derivative of a copyrighted game - the
same position every HD pack in the community is in. Model licences matter too: 4x-UltraSharp is
CC BY-NC-SA 4.0, so a pack made with it is non-commercial. Places people share packs:

- a GitHub repository's Releases (files up to 2 GB each; the EmuCoreX-Textures catalogue the app
  reads works this way, so a pack there can be listed in it);
- the Internet Archive or a Hugging Face dataset for large packs;
- the PCSX2 HD texture threads on GBAtemp and the ARMSX2 / NetherSX2 Discords.

A disc pack only works in this fork, so say so wherever it is posted.
