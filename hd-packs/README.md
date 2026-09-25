# Disc HD texture pack recipes

One folder per game, each a **recipe** for building that game's HD texture pack from your own
copy of the disc. There are no textures in this repository and there never will be: the art
belongs to the game's publisher and the upscale model's licence is non-commercial. You build the
pack yourself, on your own PC, from your own disc.

What a disc pack is and how the emulator matches it: [docs/hd-texture-packs.md](../docs/hd-texture-packs.md).
Disc packs only work in this fork.

## Games

**[GAMES.md](GAMES.md)** lists every game with a recipe - status, before/after, pack size, build
time, coverage - plus the wishlist and the games checked and dropped. Each game's own README has
the screenshots, the measured build, and notes on how that game stores its textures. Today, five
completed: Okage: Shadow King, Tales of Destiny: Director's Cut, River King: A Wonderful Journey,
Tales of Legendia and Tales of Rebirth.

A new game needs someone to work out how that game stores its textures on the disc (see
[Adding a game](#adding-a-game) below). That work is the slow part, and it is not automatic.

## Making a pack from a recipe

You need:

- **Your own disc image** (`.chd`, `.cue`/`.bin` or `.iso`) of the exact release the recipe
  names. The recipe records the ISO's SHA-1, and `make_pack.py` warns you if yours is different.
- **A PC with an NVIDIA GPU** for the upscale. The times in these recipes were measured on an
  RTX 3060 12 GB. Without CUDA it falls back to the CPU and takes hours instead of minutes.
- **Python 3.10+** with `pip install pycdlib numpy pillow xxhash spandrel` and a CUDA build of
  PyTorch (`pip install torch --index-url https://download.pytorch.org/whl/cu130`, or the one
  <https://pytorch.org> gives for your driver).
- **MAME's `chdman`** on PATH if your image is a `.chd` (it comes with MAME).
- **Arm's `astcenc`** ([astc-encoder releases](https://github.com/ARM-software/astc-encoder/releases))
  on PATH, in `ASTCENC`, or passed with `--astcenc`: packs are stored as ASTC, the format the
  Thor's GPU reads directly.
- **The upscale model** the recipe names, downloaded yourself. It is not in the repo because of
  its licence. For Okage that is [4x-UltraSharp](https://huggingface.co/Kim2091/UltraSharp)
  (`4x-UltraSharp.safetensors`, CC BY-NC-SA 4.0).
- **Free disk** for the work folder: about 2.5 GB for Okage, tens of GB for a big game. Each
  recipe's README gives its figures, and `make_pack.py` prints the pack's size estimate after the
  extract step, before the long upscale.

Then, from the repository root:

```bash
python tools/disc_textures/make_pack.py hd-packs/SCUS-97129-okage \
    --disc "Okage Shadow King.chd" --model 4x-UltraSharp.safetensors
```

It prints each step as the command it amounts to, so a failed step can be rerun on its own:

1. **disc** - disc image to ISO (`disc.py`)
2. **extract** - every texture on the disc to a native-size PNG (`extract_native.py` + the game's `extractor.py`)
3. **upscale** - PNGs through the model on the GPU (`upscale.py`). This is most of the time. Stop it whenever you like; the next run resumes.
4. **build** - the pack: the whole upscaled images plus `disc-atlas.a2at`, the index the emulator matches against (`build_disc_pack.py`)
5. **zip** - `<SERIAL>-disc-hd4x.zip` in the work folder

At the end it checks the index against the recipe's reference SHA-256. The index depends only on
the disc and the extractor, so a match means your extraction is identical to the reference. The
HD images can differ by a bit here and there between GPUs, which is expected.

**Install:** unzip into `<DataRoot>/textures/` on the device, so you get
`textures/<SERIAL>/replacements/disc-atlas.a2at` plus an `atlas/` folder. Texture packs are on
by default (Settings > Graphics > Load texture replacements). The log line
`Disc atlas: N disc images` at boot means the pack loaded.

## Adding a game

Be realistic about this part. Every game stores its textures its own way, in its own container
and often with its own compression. To add a game, someone has to open the disc, find the
texture files, work out their format, and write an `extractor.py` that turns them into palette
indices and palettes exactly as the game hands them to the GS. Nobody has a generic tool for this.

We did Okage with an AI coding agent, [Claude Code](https://claude.com/claude-code), in this
repository. You probably will too. The repository has a skill for it,
[`.claude/skills/hd-texture-pack`](../.claude/skills/hd-texture-pack/SKILL.md): open Claude Code
at the repo root and ask, for example, *"make an HD pack recipe for <game>, my disc is at
<path>"*. The skill takes the agent through the same steps we used:

1. **Check the game is a candidate (about an hour).** In desktop PCSX2, dump one scene's
   textures (Texture Replacement > Dump Textures). Then find the disc files those textures come
   from, and check that each dumped texture is an exact crop of a disc image and that its hashes
   can be computed from the disc data. If they can't (textures built at runtime, formats nobody
   can decode), stop here. A disc pack will not work for that game.
   The tools do much of the detective work: `gsdump.py` lists what the game uploads in a GS
   dump and finds those bytes on the disc (`--timeline` shows what each upload is drawn as),
   `gsmem.py` reproduces the GS's memory layout, and `verify_dumps.py` scores an extractor
   against PCSX2's texture dumps (see the skill).
2. **Write `hd-packs/<SERIAL>-<name>/extractor.py`.** The contract is in
   [`tools/disc_textures/extract_native.py`](../tools/disc_textures/extract_native.py): yield each
   image's indices and palettes. Okage's
   [extractor](SCUS-97129-okage/extractor.py) is the worked example: an archive format, an LZ
   variant, an image format and a font format. Working those out took a few hours of
   agent-assisted work.
3. **Prove it exact.** Build a pack from the native PNGs (no upscale) and replay a GS dump of
   the game with it and with an *empty* pack (an index with no images). The frames must be
   bit-identical. Then upscale.
4. **Add `game.json` and a README from [TEMPLATE.md](TEMPLATE.md)** - before/after shots,
   measured times, coverage and gaps, format notes - fill in the reference checksums from your
   run, and add a row to [GAMES.md](GAMES.md).

Supported: palette textures (8 and 4 bit), fonts the game colours at runtime, 24-bit and
32-bit true-colour textures, and textures the game assembles in VRAM out of disc images (sprite
batches, lines of text). Not yet: 16-bit textures, mipmapped textures, and anything the game
renders at runtime rather than loading from the disc. Some games will need emulator work before
a recipe can cover them.

## A recipe folder

| File | What it is |
| --- | --- |
| `game.json` | Serial, title, extractor file, upscale model + its SHA-256 and licence, scale, the disc ISO's SHA-1, reference index checksum |
| `extractor.py` | Everything game-specific: where the textures are on the disc and how they are stored |
| `README.md` | From [TEMPLATE.md](TEMPLATE.md): before/after shots, the command and measured timings, coverage and gaps, how the disc stores its textures, checksums |
| `media/` | Before/after screenshots from the device |
| `work/` | Created by `make_pack.py` and ignored by git: the ISO, PNGs and the pack |
