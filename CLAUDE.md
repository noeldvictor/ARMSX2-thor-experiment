# CLAUDE.md

## Where The Rules Live

Working conventions for this repository live in a single file: [AGENTS.md](AGENTS.md).

Read `AGENTS.md` before making changes. It covers project shape, build and verify
commands, local tooling, the git and upstream-refresh workflow, fork identity rules,
Android UI and cheat conventions, rendering and upscaling conventions, native-core
caution, and style.

Do not duplicate those rules here — update `AGENTS.md` instead.

## Current Thinking

Active work and design directions, so a new session does not re-derive them or
contradict decisions already made. Implementation status is stated per item.

### Texture upscaling — in the emulator, not on the screen

**Implemented and running.** Full notes:
[docs/texture-upscaling-research.md](docs/texture-upscaling-research.md).

The core distinction, and the easiest thing to get wrong: this upscales **each
texture as the emulator uploads it**, so the game renders from better source art. It
does **not** upscale the finished frame. Present-time upscaling already exists here
(FSR1, the librashader slang chain, LSFG) and is not what this is.

- On-device and real time, no offline bake, works without HD texture packs.
- Two independent classes, world/3D and UI/2D, each with its own enable, algorithm
  and scale. A filter that flatters a painted wall will mangle a HUD font.
- Explicit Off / 2x / 4x. Thor's panel is 1080x1920, so 4x is the useful ceiling.
- Per-game with a global default.
- **Seventeen algorithms are selectable**: Nearest, Bilinear, Sharp Bilinear, Bicubic,
  Mitchell, Lanczos, Lanczos+CAS, Scale2x, Eagle, SuperEagle, 2xSaI, Super2xSaI, xBR, and the
  four neural entries. HQx, xBRZ, SuperxBR, ScaleFX and OmniScale still decline and leave the
  texture native - deliberately not reconstructed from memory, since a subtly wrong filter
  under a famous name gets the filter blamed rather than the bug.
- The enum is **append only** and the picker's ordinal/label/description lists must stay the
  same length - a mismatch silently selects the wrong filter.
- **The upscale runs on a worker thread**, not the GS thread. The native texture is created
  immediately and the upscaled one swaps in a frame or two later via
  `ProcessUpscaledTextures`. Do not move it back inline - that was the original mistake.
- **Neural needs a user-supplied model.** Architecture only; no weights ship. See
  [docs/neural-models.md](docs/neural-models.md). `tools/make_a2nn.py --identity` proves the
  path end to end without weights. There is a 4000 MACs/pixel ceiling because inference is
  on the CPU; raising it is the wrong fix, moving to Vulkan compute is the right one.
- Style presets rather than a curated per-game table.
- VRAM: warn once, then evict in a batch to a low-water mark, with a declined-hash
  set so a full budget cannot thrash.
- Mounted in **both** All Settings (renderer tab) and the in-game pause menu, from one
  definition in `ui/common`. In-game matters: filters are only comparable if you can
  switch them without leaving the game.
- Build order was scaffold → cheap scalers → neural last, as Vulkan compute. Do not
  jump to the neural path.

### On-device MCP server

**Not implemented.** Full notes: [docs/mcp-server.md](docs/mcp-server.md).

Exists mainly to make the upscaling work measurable — comparing twenty algorithms by
hand across a library is not realistic.

- Drives screenshots/framebuffer capture, settings read/write, emulator control, and
  texture dump/replace control.
- Localhost only over `adb forward`. Off by default, visible indicator when running.
- `github` flavor only, compiled out of `play`, never a hard dependency.

### ARM64 optimization

**One finding applied.** Full notes:
[docs/arm64-optimization-review.md](docs/arm64-optimization-review.md).
Reference manuals for the Thor's exact cores in
[docs/reference/arm/](docs/reference/arm/README.md).

- The Thor is a heterogeneous 1+4+3 complex (Cortex-X3 / A715 / A710 / A510) and the
  Lite is a Snapdragon 865 matching none of them. Any perf claim must name the core.
- **Applied:** `armsx2.march=armv8.2-a+fp16+dotprod` now defaults in
  `platforms/android/gradle.properties`. Before this, local debug builds fell back to
  `armv8.1-a` and tested different codegen than any released APK.
- Verified on device: the v8.2 build installs and runs with no SIGILL. Confirmed in the
  CMake cache as `-O3 -g -march=armv8.2-a+fp16+dotprod`.
- Not benchmarked. No profiling has been run, so nothing here identifies a
  *measured* hot path — do that before vectorizing anything.

### Cheat tooling

**Analysis tool implemented, rest not.** Full notes:
[docs/cheat-tooling.md](docs/cheat-tooling.md).

- `tools/cheat_coverage.py` measures the gap. On the test library: 590 bundled files
  covering 534 serials, and **46% of resolvable games have no cheats**.
- Unresolved filenames are reported separately from missing on purpose — unknown is
  not the same as absent, and merging them overstates the gap.
- Two separate problems: importing cheats that exist (use the tool's missing list),
  and authoring ones that do not (memory search — likely better driven over MCP than
  through an on-device UI).
- Bundled files are keyed by **CRC**, not serial. That is the real friction when
  importing for a game you do not own.

### Texture pack getter

**Not implemented.** Full notes:
[docs/texture-pack-getter.md](docs/texture-pack-getter.md).

- Only offer packs for games in the library — that scoping is the point of the
  feature, not a filter on top of it.
- Catalogue must be keyed by **serial and CRC**, never title. Title matching leaves
  ~16% unresolved on a real library, and a catalogue can just carry the right key.
- Installs to `<DataRoot>/textures/<SERIAL>/`, the folder the existing replacement
  loader already scans.
- Runtime resolution order is pack → upscaler → native, which is what makes partial
  pack coverage acceptable.

### Device defaults

- **On-screen touch controls default to off** (`TouchControls.visibilityMode` 0 rather than
  upstream's 11). The Thor has physical sticks and buttons; the overlay was covering the
  game to duplicate them. Mode 0 is what the code already documented as the
  physical-controls-device setting.
- A stored preference always wins over a default, so changing a default does nothing for
  an existing install. Worth remembering before concluding a default change "did not work".

### Working with the shared Thor

- **The device is shared with other Claude sessions.** Foreground focus being stolen
  mid-run is other agents working, not a bug on the device. Full rules in `AGENTS.md`.
- **Never stop because the Thor is busy.** There is always code work available - filters,
  tooling, docs, review. Device time is opportunistic; take it for the one step that needs
  it and give it back.
- **Close the emulator when done**: `adb shell am force-stop com.armsx2`.
- Do not force-stop other apps to grab focus.
