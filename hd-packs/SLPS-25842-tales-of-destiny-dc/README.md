# Tales of Destiny: Director's Cut (SLPS-25842) - work in progress

The English fan-translated disc (v1.6). The extraction and the matching are proven exact on two
scenes; the 4x pack itself is being built and has not been judged on the Thor yet, so there are
no timings, sizes or screenshots here yet.

## What works (gsrunner replays on the Thor, 2026-09-23)

A full 1x pack (every disc image at native size, 101,328 of them) against an empty pack:

| Scene | Textures matched | Frames |
| --- | --- | --- |
| Ship's deck | 47 of 47 | bit-identical to an empty pack |
| Title screen | 16 of 17 | bit-identical to an empty pack |

The title's one miss is a 1024x1024 32-bit texture the game renders itself, not a disc image.
Replays use `hw_mipmap=false` for the comparison (see the skill: the deck floor is mipmapped).

- **Decoded:** `DAT.BIN`/`DAT.TBL`, Namco's Tales compression (types 1 and 3) including nested
  sub-files, TIM2 textures and `anp3` character sprites (59,546 unique frames, one HD image per
  palette). See `extractor.py`.
- **Font:** 2,013 glyphs out of the executable, palette-free with the runtime palette read from
  VRAM.
- **Title screen:** the art is two true-colour (PSMCT32) TIM2 pictures, drawn as such - not 8-bit
  data in disguise, as first thought; `gsdump.py --timeline` settled it. The menu text is a 4-bit
  picture with a 48-colour CLUT the game uploads 16 wide, so each palette is an 8x2 patch
  (entries 0-7 + 16-23, 8-15 + 24-31); `tim2.py` used to slice small CLUTs as consecutive 16s
  and missed all three text palettes. Lines wider than 512 texels read past the buffer width into
  the picture's last rows - a composite of one picture.
- **Composites:** the emulator splits a sprite batch or a text line into the disc images it is
  made of (143x31 from 4 frames, 87x87 from 3, the 95x175 party sprites from 7). The deck map
  differs from its disc image in 11 texels the game parks in its memory; it is a one-image
  composite with those texels left native.
- **TIM2s with no size:** the deck's 128x128 texture is a TIM2 with neither width nor height
  stored. `tim2.py` now takes the power-of-two square, which recovered 12,868 8-bit pictures on
  this disc.

## Not done yet

- The 4x pack: build, install, judge at 3x on the Thor, time it on the RTX 3060.
- Only two scenes are proven. Towns, battles and menus have not been dumped yet.
- The index is big: 1.1 GB (12 million blocks plus the texels of every disc image, which the
  exact check needs). The emulator memory-maps it (about 40 MB resident on the Thor), but it is
  still a gigabyte of download and disk.
- The first 4x pack (3.1 GB, PNG, 47 min on the RTX 3060) had wrongly coloured UI corners because
  the palette slicing changed between its extract and build steps; a clean rebuild fixes that.
