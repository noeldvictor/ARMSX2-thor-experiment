# Tales of Destiny: Director's Cut (SLPS-25842) - work in progress, not ready

The English fan-translated disc (v1.6). This recipe is being worked out and does **not** make a
usable pack yet. What is known, verified on the Thor with gsrunner replays of desktop GS dumps:

- **Decoded:** `DAT.BIN`/`DAT.TBL`, Namco's Tales compression (types 1 and 3) including nested
  sub-files, TIM2 textures (4,034 with palette variants) and `anp3` character sprites (59,546
  unique frames). See `extractor.py`.
- **Exact:** every match is exact - a 1x pack renders bit-identical frames to an empty pack on
  the ship's deck (with `hw_mipmap=false`, see the skill).
- **Matches today:** 256x256 map textures and single sprite frames.

Not working yet:

- **Composite textures.** The game builds its 512x512 map sheets and multi-part sprites in VRAM
  out of several uploads, and draws regions that are no single disc image. In the first scene
  that is most of the misses. The disc pack cannot match these; it needs upload-time replacement
  in the emulator.
- **Fonts and menu text** use palettes made at runtime; the font file is not decoded.
- **Title art** is uploaded as 32-bit data and drawn as 8-bit (`gsmem.py` can reproduce that; the
  extractor does not do it yet).
- The first 4x pack (3.1 GB, 47 min on the RTX 3060) had wrongly coloured UI corners because the
  palette slicing changed between its extract and build steps; a clean rebuild fixes that.
