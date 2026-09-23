# Okage: Shadow King (SCUS-97129, CRC E0426FC6)

Per-game notes: what was measured, what shipped, what is still open. Numbers are from
desktop PCSX2 2.8.2, D3D12, native resolution, PCSX2's own OSD counters, on the Tenel
outdoor scene (save state 2 in the cheat lab) unless said otherwise. Tools:
`tools/pcsx2_mcp`, skills `ps2-cheat` and `find-ps2-cheats`.

## Why it is slow

The game is not visually heavy and it is not GPU-heavy on a desktop (GPU 28% at native).
It is **render-pass heavy**, and on a tiled mobile GPU that is the expensive thing.

| config | draws | barriers | render passes | GS thread | GPU |
| --- | --- | --- | --- | --- | --- |
| GameDB default: `autoFlush: 1` (sprites), blending Medium | 678 | 656 | **144** | 2.76 ms | 4.70 ms |
| autoFlush off, blending Medium | ~680 | 580 | 10 | 2.12 ms | 4.76 ms |
| autoFlush off, blending Minimum | ~680 | **257** | **8** | 1.90 ms | 3.31 ms |
| indoors (inn), GameDB default | 1121 | 622 | 5 | 2.36 ms | 4.39 ms |

Three separate costs stack up:

1. **Depth-of-field pass + auto-flush = 134 render-pass breaks per frame outdoors.** The game
   blurs by distance by reading its own frame buffer back through a *channel shuffle*: a
   sprite draw whose TEX0 points at the other frame buffer (block 0x1180, the buffer right
   after the 640x448x32 front buffer) with `PSMT4HH` (the high nibble of each 32-bit pixel,
   i.e. the alpha the scene wrote as a blur mask), TBW 4, a 1024x1024 declared size and a
   CLUT at 0x267A/0x267E (double-buffered per frame). PCSX2 upstream marks the game
   `autoFlush: 1` so the read-modify-write comes out right, and sprite auto-flush ends the
   render pass on every sprite of that pass. A desktop GPU shrugs at 144 passes; a tiled GPU
   (Adreno, Mali) stores and reloads the whole frame buffer's tiles at every pass boundary,
   at internal resolution. At 3x IR on the Thor that is the frame drop.
2. **Shader-emulated blending.** ~320 of the barriers at the default *Medium* blending
   accuracy are draws whose PS2 blend formula is done in the fragment shader with a
   frame-buffer read. *Minimum* halves the barriers (580 -> 257); the only visible change on
   the test scene was Ari's shadow blend (mean |diff| 1/255). The GameDB key
   `recommendedBlendingLevel` can only *raise* the level, so this stays a global user setting
   (the Thor already runs Basic).
3. **The DoF pass itself** still costs ~250 barriers per frame with everything else off, and
   the game's textures are sometimes stored in the upper nibbles of frame-buffer-sized VRAM
   (the 4-bit texture creator at `00194A20` picks PSMT4HL/HH layouts), which the texture
   cache treats as target reads.

The EE is 25-31% on desktop, so on the Thor's cores the CPU side is not the limit; the GS
thread and the GPU render-pass count are.

## What shipped

- **Fork GameDB drops `autoFlush: 1` for SCUS-97129** (`bin/resources/GameIndex.yaml`,
  commit `61e6573b11`): 144 -> 10 render passes per frame; visually identical at native res.
  It reaches the Thor with the next APK build. For an install that already exists, the same
  thing through the per-game ini works today:
  `gamesettings/SCUS-97129_E0426FC6.ini` -> `[EmuCore/GS] UserHacks = true`,
  `UserHacks_AutoFlushLevel = 0` (manual hacks disable the GameDB fix list, which for this
  game is only autoFlush).
- Recommended on the Thor in addition: blending accuracy **Minimum** (global setting).
- Not yet measured on the Thor: the device was reserved by another session while this was
  done. Do that first when it is free: boot, `hotkey` OSD, screenshot, compare FPS with and
  without the ini above.

## The depth-of-field patch (shipped)

Found with the PCSX2 debugger: an 8-byte write breakpoint on the rebuilt packet's TEX0
qword, armed across a scene load (inn -> outside), stopped in the generic display-list qword
appender at `001900F0`; the return chain from the save state's `cpuRegs` (`sp` walk) was
`00100630 -> 0018FA48 -> 0018F97C (jalr) -> 00192E18 -> 00190DB8 -> 0018FF08`. So:

- `0018F928` is an effect dispatcher: entry `index` of a table at `[0x332934]` (32-byte
  records: enabled, a0, a1, a2) and a static function table at `002003B0`; it `jalr`s the
  function, then hands the returned packet handle to `001C96C0`, which **does nothing when the
  handle is 0**.
- slot 6 (`002003C8`) is the DoF: the wrapper `00192E08` -> builder `00190A20`, which per
  object assembles TEX0 (PSM from a VRAM descriptor via `0x1cbc58`, `dsll 0x14`), emits
  TEST/ALPHA/RGBAQ/TEX0/TEX1 through `0x1aedd8(reg, value)` and the sprites through
  `0018FE10` (30 call sites in the builder = 30 blur sprites max).
- **Patch**: `00192E08 = 03E00008` (`jr ra`), `00192E0C = 24020000` (`addiu v0,zero,0` in the
  delay slot). The frame loop asks for the DoF packet, gets NULL, links nothing.

Same scene, autoflush still on: 15,740 -> **3,083 primitives**, 678 -> 537 draws,
144 -> **5 render passes**, GS thread 16.5% -> 9.7%. The image is simply sharp. Fades and
the inn transition are unaffected (they are other slots; 7 and 8 are `00192E70`).

Shipped as an automatic GameDB patch (`patches: E0426FC6` in the fork's GameIndex.yaml) and
as a bundle cheat switch "No Depth of Field Blur (Faster)" for builds that predate it. On
the Thor right now: the active cheat file and the game ini enable it (plus autoflush off);
the next APK build makes all of that the default. Target on device: 2x fast-forward.

## On the Thor: it is the shadows (2026-09-22)

The DoF patch and dropped auto-flush did not fix the Thor. A house scene (dialogue, fast
forward on) measured on the device over wireless adb, 60-second `PerfLog` averages:

| config | fps | GS thread | GPU |
| --- | --- | --- | --- |
| blending Basic (the user's setting) | 45 | 99% | 81% |
| blending Minimum | 40 | 67% | 98% |
| Minimum, texture upscaling off | 40 | 70% | 98% |
| Basic, texture upscaling off | 45 | 99% | 81% |

The same frame, captured on the Thor (pause menu > Capture GS Dump) and replayed in desktop
PCSX2, has **1,256 draws and 1,389 barriers**. Desktop shrugs; the Thor's Qualcomm driver
has no usable in-pass self-read (`ROAA=NO fbfetch=NO`, "forcing the RT-copy blend path"), so
each barrier is end-pass + copy the render target + restart - that is the 21 ms GS thread.

Where the barriers come from, from a parse of the dump's GIF stream: **350 shadow strips
per frame, each drawn three times** (12-vertex untextured triangle strips, colour 0, alpha 0):

1. `FRAME.FBMSK = 7FFFFFFF` (only alpha bit 31 writable), `FBA = 1`, no blending - set the bit
2. `TEST = 0005400C` (DATE, DATM 0), `ALPHA = 0x54` `(Cs-Cd)*Ad+Cd` - the shadow
3. `FBMSK = 7FFFFFFF`, `FBA = 0` - clear the bit

A 1-bit alpha mask is a partial channel mask, emulated in the shader by reading the frame
buffer (`ps.fbmask` -> one barrier), and the DATE pass needs one too. Replaying the dump with
the shadow draws removed: **0 barriers, 206 draw calls** (from 1,594). All 1,389 barriers
are shadows.

The shadow pass is built from a 9-qword GIF template at `00202DE0` that `001B0F90` refreshes
every frame (it stores the FRAME values into it - the mask comes from
`addiu a1,zero,-1; dsll32 a1,a1,1; dsrl a1,a1,1` at `001B0FB4`) and uploads to VU1, whose
microprogram replays it per strip; a setup packet in the heap (`ALPHA 0x54`, `FRAME
7FFFFFFF`, `TEST 5000C`, `FBA 0`) opens each batch.

What was tried on the live Tenel scene (desktop):

- Only DATE off: the shadow becomes a solid black band - the shape depends on the trick.
- Mark/clear write nothing + DATE off ("no trick", every `7FFFFFFF` including the setup
  packet's): indoors it is visually identical to the original (mean |diff| 0.03/255), 0
  barriers; **outdoors the crisp silhouette turns into faint streaks**, so not shippable.
- Pass 2 with a fixed blend factor (`ALPHA` FIX 0x80 in place of the `FBA` slot): solid black
  band again.
- **Shadows off** (mark/clear write nothing, shadow draw `ZTST = NEVER`): **576 -> 1 barrier,
  640 -> 65 draw calls** outdoors, clean image, no shadows. Shipped as the switch "No
  Character Shadows (Much Faster)" in `E0426FC6.pnach` (off by default):
  `201B0FB8 = 0005283C`, `201B0FBC = 00000000`, `20202E30 = 0001000C`.

### Shadows kept, reads removed (renderer, 2026-09-22)

Two renderer changes, both Vulkan-only and both only on devices where a frame read is a
pass break plus a copy (no texture barriers - the Thor's Qualcomm driver). Measured by
replaying the Thor's own house-scene dump on the Thor with `pcsx2-gsrunner -perf`, 30 loops:

| build | GS thread / frame | render passes / frame | image |
| --- | --- | --- | --- |
| before | 18.6-19.0 ms | - | reference |
| + logic-op mark/clear (`45b27da3f5`) | 14.0 ms | 708 | bit-identical |
| + stencil copy of the DATE result (`ce83c8f019`) | **5.9 ms** | **161** | bit-identical |
| shadows removed entirely (dump edit, for scale) | 5.7 ms | - | no shadows |

So the shadows now cost ~0.2 ms a frame instead of ~13.

1. **Mark and clear through a logic op** (`GSAlphaBitLogicOp.h`). A draw with
   `FBMSK 7FFFFFFF`, black, no blending only sets or clears alpha bit 7. With the shader's
   alpha forced to 0x80, `VK_LOGIC_OP_OR` sets it and `AND_INVERTED` clears it, keeping
   bits 0-6 (the next draw's blend factor) without reading the target.
2. **The DATE draw from a stencil copy that the flag draws keep current.** The shadow draw
   writes alpha 0 under DATM 0, so it never flips its own test: a stencil snapshot of the
   test is exact (plain stencil DATE instead of primitive-ID tracking). The first such draw
   after a flag draw in a render pass takes the snapshot over the whole target (one setup
   pass per frame instead of one per strip); the mark and clear pipelines then also
   `REPLACE` the stencil with the result they leave (where the depth test passes - exactly
   where the logic op writes), and the following DATE draws test it with no setup at all.
   Any other alpha write, a StencilOne DATE draw, an in-pass colour clear or the end of the
   pass drops the copy; the pass variant carrying it stores stencil so a command-buffer
   restart keeps it.

Nothing game-specific: any game that uses alpha bit 7 as a one-bit stencil this way gets
the same path. The switch "No Character Shadows (Much Faster)" stays in the cheat file for
anyone who wants the last 0.2 ms, off by default.

**In game on the Thor** (APK with both changes, native resolution, blending Basic, DoF patch
on, desktop save states copied to the Thor - same save-state version `0x9A59`):

| scene | normal speed | 2x fast-forward |
| --- | --- | --- |
| Tenel, outside the inn (state 2) | 59.9 fps, EE 46% GS 34% GPU 26% | **119.9 fps**, EE 81% GS 69% GPU 53% |
| World Library, three characters (state 1) | - | 113-116 fps, EE 88-91% GS 99% GPU 90-92% |

Before: 45-49 fps with fast-forward on, GS 99%.

After the shadow-shape fix and `c7a60a6972` (the strips without self-overlap no longer take
the multi-pass blend, so they stop rebuilding the stencil copy): **2x holds 119.9 fps in both
scenes** - Tenel EE 88% GS 47% GPU 25%; World Library EE 96-99% GS 99-100% GPU 85% (at the
edge there, but at speed). Replays: render passes a frame house 161 -> 9, Tenel 180 -> 9,
World Library 324 -> 12; GS 5.1 / 2.0 / 7.5 ms.

### The shadow's shape was wrong on the Thor all along (fixed 2026-09-22)

Seen in game after the speed fix: Ari's shadow was a pale smear with jagged dark pieces, where
desktop PCSX2 draws a solid Shadow King silhouette. Not caused by the speed work: a desktop GS
dump of the same frame (state 2, `F9` single-frame dump) replayed on the Thor gave identical
pixels with the old, logic-op and stencil-copy builds, and `-renderer sw` on the same dump drew
the right silhouette. Per-draw target dumps (`-set EmuCore/GS/DumpGSData=true ... SaveRT/SaveAlpha/
SaveHWConfig`, Vulkan vs SW) located it at the first strip:

- the shadow draw is `Cd * (1 - Ad)`; the mark draw before it unscales the target's alpha (its
  1-bit mask needs the raw bits), so the hardware blend reads Ad as `a/255` - for the ground's
  alpha 0x40 that darkens by 25% instead of 50%, and the error compounds over 350 strips;
- upstream's correct road is a software blend (a frame read); desktop takes it through texture
  barriers, the Thor cannot, so it fell back to the half-strength hardware blend at every
  blending-accuracy level (High gave the same picture).

Fix (`820fd43af3`, Vulkan, stencil DATE only): run the draw twice under the stencil copy - an
alpha-only pass `Ad * 1 + Ad` on the passing pixels (stencil 1 -> 2, so overlapping triangles do
not double twice), then the draw over stencil 2 (-> 1), which now reads Ad double-scaled like
RTA correction does. No reads, same pass, GS time unchanged. Replay vs SW: shadow area |diff|
3.29 -> 0.68, whole frame 1.07 -> 0.68.

## What did not work

- `skipdraw` cannot isolate the DoF: skipping 1 draw after the first frame-buffer-sampling
  draw leaves the blur; skipping 2 or more removes the field merge (every other line dark,
  Ari drawn black). The DoF draws come later in the frame than the first target read (the
  overhead map / field blend), and skipdraw only counts from the first.
- (Now solved above.) Facts that led there: the DoF display list is built **once per scene load** and re-sent by
  DMA every frame (a PINE poke to the TEX0 qword at `01BE8570` survives indefinitely; the
  list lives in the heap and moves per scene: `01BE8570` and `01CE8570` in Tenel, elsewhere
  in the World Library). There is no TEX0 template in `.data`; PSM 0x2C is passed to a
  generic texture helper as an argument; the TBP0/CBP come from the VRAM allocator. So the
  builder has to be caught dynamically: open the PCSX2 debugger, set a **memory write
  breakpoint, 8 bytes, on the packet's TEX0 qword**, then trigger a scene load (walk into
  the inn). `sq` stores are covered by PCSX2's memchecks (`recMemcheck(op, 128, store)`),
  so it will fire. Do not relaunch PCSX2 between setting the breakpoint and the scene load;
  the debugger and its breakpoints die with the process (that is what went wrong the first
  time). `tools/pcsx2_mcp` has `click`/`type_text` for the Qt debugger, captures across two
  monitors, and `find_window` skips the debugger window.

## Ideas worth fleshing out

### "Move the render-pass-heavy stuff to the GPU"

It already is GPU work; the cost is the *pass boundaries*, not the shading. The way to keep
the pass open while a draw reads the pixel it writes is a framebuffer-fetch style feedback
loop, and the fork's Vulkan backend already knows the two extensions that give it:
`VK_EXT_attachment_feedback_loop_layout` (in-tile feedback loop; used for the "one barrier"
case) and `VK_EXT/ARM_rasterization_order_attachment_access` (`GSDeviceVK.cpp` around line
490-830 decides which to keep). What to find out on the Thor: which of the two the driver
in use (Qualcomm proprietary vs Turnip) actually exposes, and whether the auto-flush sprite
path and the channel-shuffle path take the feedback-loop road or fall back to a pass break.
If the driver has the extension but the shuffle path breaks the pass anyway, that is a fork
optimisation with a measurable target: 144 -> ~10 passes with the blur intact.

### "Dump the textures without playing, make the HD pack in advance"

How the hash works, so the question has a precise answer: PCSX2 hashes the texture **after
the game uploads it to GS memory**, reading the texel rows back out of local memory
(`HashTextureLevel` in `GSTextureCache.cpp`): for paletted formats the 4/8-bit palette
indices, for others the 32-bit texels, over the TEX0 TW/TH rectangle (or the used region,
hence the `-rWxH` suffix) aligned to the PSM's block size; the CLUT is hashed separately
(the `-CLUT` part of the replacement name); the trailing bits encode PSM/TW/TH. So the hash
is format-agnostic on the emulator side, but an offline tool has to reproduce the exact
bytes the game would upload: same pixel data, same PSM, same declared size. That means
decoding the game's own texture container. PS2 has only nine GS pixel formats
(CT32/CT24/CT16/CT16S, T8/T4, T8H/T4HL/T4HH) and fixed swizzles, but the *on-disc* container
is per game (TIM2 is common; many games are custom and compressed), so "in advance" means
"reverse the archive format first". Doable per game, not generic.

The generic route that needs no format work is a **crawler**: run the game on the PC with
texture dumping on and force it through every map. The cheat tools can do the forcing: find
the map/area id variable with the snapshot-diff search (it changes on every scene load),
poke each id with `mem_write` at a load trigger, let each map draw for a few seconds at
turbo, and let PCSX2's dumper write every upload. Then upscale the dumps offline (a real
neural model on the PC, not the Thor's 4000 MACs/pixel ceiling) and ship the folder as a
pack: pack textures win over the on-device upscaler by design. That is the "HD pack in
advance" the question is after, with the PC doing the heavy lifting once per game.

## HD textures

No released pack (checked 2026-09-22): not in ARMSX2's B2 catalogue (517 packs) or sashkinbro's
(621), and Panda_Venom's GBAtemp list only has 2023 work-in-progress screenshots of Okage, not
a release. On the Thor the on-device upscaler is already on for it (RAISR-HD 2x, world and UI);
at internal resolution 1x the gain is hard to see - raise the internal resolution to judge it.

### Every texture straight off the disc (2026-09-22)

The plan: make an HD pack without playing - pull the textures from the disc, upscale them on a
desktop GPU, and link them to what the emulator sees. The disc side is done:
`tools/disc_textures/okage_xim.py` writes all **2,807 unique textures** (9,634 references,
40 MB of PNG) with **no failures**, in about 15 seconds.

- The disc is a CD (`MODE2_RAW`); `chdman extractcd` then strip each 2352-byte sector to its
  2048 user bytes (offset 24) for an ISO that `pycdlib` reads.
- 741 `.XPF` archives (maps, battles, characters) plus 50 standalone `.XIM` images. XPF entries
  are compressed with a small bit-flag LZ (format documented by simontime/xpftool; our decoder is
  an independent implementation). XIM: a GS `TEX0` word giving the PSM (2,530 PSMT8, 103 PSMT4,
  173 PSMCT24, 1 PSMCT32), a 256-entry RGBA palette in index order (not CSM1-swizzled) with PS2
  alpha, then height, width and linear pixels. Full layout in the tool's docstring.
- Most textures are small (64x32, 64x64, 32x32 lead); the whole set is 32 MB native, so a desktop
  GPU upscales it in minutes.

The link to the emulator is the open part. The stock replacement key hashes GS memory the way
the draw reads it (raw swizzled blocks for most PSMT8 textures, TW x TH rounded up to a power of
two, so junk past the image edge is in it) plus the palette, size and region. A fork-only key
computed from what the disc already gives - the decoded RGBA at the uploaded image size - would
let the offline tool name each PNG exactly; the emulator side needs to know the uploaded image
size, which it can record from the transfer (BITBLTBUF/TRXREG) that filled the texture's memory.

## Cheats

`assets/cheats/E0426FC6.pnach`: "No Enemy Encounters (Ghosts Pass Through)" -
`patch=1,EE,20137508,extended,10000077`, the ghost-contact `bc1f` forced to `b`. Found with
the snapshot-diff method (field controller state at +0x48, encounter record at `002D9BC0`,
battle flag `002D9ABC`, Ari's position `001FB980`), verified on desktop and loaded on the
Thor. Full method in `AGENTS.md`, "Cheat tooling".
