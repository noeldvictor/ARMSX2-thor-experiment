# Tales of Destiny: Director's Cut (SLPS-25842) - work in progress, not ready

The English fan-translated disc (v1.6). This recipe is being worked out and does **not** make a
usable pack yet. What is known, verified on the Thor with gsrunner replays of desktop GS dumps:

- **Decoded:** `DAT.BIN`/`DAT.TBL`, Namco's Tales compression (types 1 and 3) including nested
  sub-files, TIM2 textures (4,034 with palette variants) and `anp3` character sprites (59,546
  unique frames). See `extractor.py`.
- **Exact:** every match is exact - a 1x pack renders bit-identical frames to an empty pack on
  the ship's deck (with `hw_mipmap=false`, see the skill).
- **Matches today:** map textures (256x256 and 512x512), single sprite frames and the font -
  2,013 glyphs out of the executable, palette-free with the runtime palette read from VRAM.
  Ship's deck: 32 matches, bit-identical to an empty pack. Of the 470 textures desktop PCSX2
  dumped, 62% reproduce from disc data (`verify_dumps.py`) before the font; what is left is
  text lines and sprite sheets, both assembled in VRAM.

Not working yet:

- **Composites** now match: the emulator splits a sprite batch or a text line into the disc
  images it is made of (ship's deck: 143x31 from 4 frames, 87x87 from 3). Still bit-identical at 1x.
- A few textures on the deck still miss (the streaming 512x512 map slot, a 128x128 texture,
  small icons) - to be checked against the full pack.
- **Title art** is uploaded as 32-bit data and drawn as 8-bit (`gsmem.py` can reproduce that; the
  extractor does not do it yet).
- The first 4x pack (3.1 GB, 47 min on the RTX 3060) had wrongly coloured UI corners because the
  palette slicing changed between its extract and build steps; a clean rebuild fixes that.
