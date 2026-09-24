# River King: A Wonderful Journey (SLUS-21275) - disc HD texture pack recipe

**Status:** in progress. NTSC-U disc (Natsume / Marvelous, 2006). The extraction and the matching
are proven exact on five scenes; the 4x pack is being built.

A 4x HD pack for River King: A Wonderful Journey, built from your own disc. No gameplay dumping:
every texture is read off the disc, upscaled with 4x-UltraSharp and matched exactly by the
emulator. How that works: [docs/hd-texture-packs.md](../../docs/hd-texture-packs.md).

## Before / after

Not yet: screenshots come once the 4x pack has been judged at 3x on the Thor.

## Make it

Requirements: [hd-packs/README.md](../README.md#making-a-pack-from-a-recipe). From the repository
root:

```bash
python tools/disc_textures/make_pack.py hd-packs/SLUS-21275-river-king \
    --disc "River King - A Wonderful Journey (USA).chd" --model 4x-UltraSharp.safetensors
```

Install: unzip `work/SLUS-21275-disc-hd4x.zip` into `<DataRoot>/textures/`.

### Measured

Not yet. The extract takes about a minute and a half (8,034 images, 125 M texels); the 4x pack
will be about 2 GB (`make_pack.py`'s estimate).

## Coverage

`gsrunner` replays of desktop GS dumps on the Thor at 3x, 2026-09-23, a 1x pack (every disc image
at native size) against an empty pack:

| Scene | Textures matched | 1x vs empty pack |
| --- | --- | --- |
| Title screen | 44 of 44 | bit-identical |
| Character selection | 36 of 36 | bit-identical |
| Name entry | 37 of 37 | bit-identical |
| First dialogue | 36 of 36 | bit-identical |
| The house (3D, intro) | 30 of 30 | bit-identical |

The text is matched too: the game draws each line into a glyph cache, and the emulator builds
those caches out of the font's glyphs (the name-entry sheet is one texture of 86 glyphs).
Against the 259 textures desktop PCSX2 dumped in those scenes, the extractor reproduces 230 from
disc data; the other 29 are the glyph caches, matched on the device as composites.

## Not yet

- The 4x pack: not built, not judged, no timings.
- Only the opening is checked - no fishing, river, town or menu scenes yet.
- 831 textures have 16-bit palettes; the GS turns those into 32-bit ones with TEXA, which no
  checked scene showed, so the extractor guesses the usual values (see `extractor.py`). If those
  textures never match, that guess is why.

## How the disc stores its textures

Full details in [`extractor.py`](extractor.py)'s docstring. In short:

- Everything but the music is one CRI AFS archive, `DATA0000.AFS` (898 MB). Its entries are
  Nintendo U8 archives (models and their textures), texture lists (`.tex`, `.tpl`) and single
  textures - all uncompressed, except three `.arc.clz` archives.
- `.clz` is an LZSS whose references are distances back in the output, not window positions
  (`disc_codecs.clz_unpack`). A ring-buffer reading of the same bits decodes to the right size
  and garbage; stepping through the stream against the U8 header it has to produce settled it.
  The three hold the UI (`commonall`), the map window and sky (`preload`) and 125 character
  archives (`mainchapter0`).
- Every texture is a `P2IG` image: a 128-byte header (log2 size, PSM, CLUT format, offsets), the
  CLUT, then linear indices. 256-colour CLUTs are stored CSM1-swizzled, 16-colour ones not. A raw
  scan for `P2IG` finds every texture, wherever it sits.
- The dialogue font is not in the AFS: it is a 1-bit 24x24 table of 1,586 glyphs in the
  executable. The `.uf` fonts in the AFS (2-bit, antialiased) are another font the checked scenes
  do not use.
- A dead end worth knowing: `verify_dumps.py` first showed a third of the dumps as "palette only".
  They were full-size textures PCSX2 names by raw GS blocks, which a crop hash never reproduces;
  their colours were identical. The tool now falls back to comparing colours.

## Checksums

| | |
| --- | --- |
| Disc ISO SHA-1 (after `disc.py`) | `eb95fbc2654099fc66567fd0e57458c41681d374` |
| Upscale model SHA-256 (`4x-UltraSharp.safetensors`) | `36a340b5509b699d2c06cb445ddc1d3d39199ac734d889ed6d7915f60e05bcbc` |
| Index `disc-atlas.a2at` SHA-256 (ASTC pack) | not recorded yet |

## Playing with it

Not judged yet. The target: 3x internal resolution on the Thor, RAISR-HD left on, 2x fast-forward.
