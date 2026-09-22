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

## Cheats

`assets/cheats/E0426FC6.pnach`: "No Enemy Encounters (Ghosts Pass Through)" -
`patch=1,EE,20137508,extended,10000077`, the ghost-contact `bc1f` forced to `b`. Found with
the snapshot-diff method (field controller state at +0x48, encounter record at `002D9BC0`,
battle flag `002D9ABC`, Ari's position `001FB980`), verified on desktop and loaded on the
Thor. Full method in `AGENTS.md`, "Cheat tooling".
