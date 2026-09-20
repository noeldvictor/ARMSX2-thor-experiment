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

**Implemented and verified on device.** 26/26 filter self-tests pass and the neural path
runs to the expected checksum (`b2870000` for the identity model). Still unmeasured: how it
looks, and what it costs in a real scene. Full notes:
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
- **A model self-test runs at texture-cache creation**, independent of whether upscaling is
  enabled, and logs dimensions plus a checksum. For the `--identity` x2 model the checksum is
  deterministically `b2870000`. Use it rather than trying to force settings from a shell -
  settings live in SharedPreferences (`config.global` / `config.game.<serial>`), and
  `armsx2-settings.json` in the data folder is only a reinstall BACKUP, not the live store.
  Editing either from adb does not work; the UI is the only reliable way to change settings.
- Style presets rather than a curated per-game table.
- VRAM: warn once, then evict in a batch to a low-water mark, with a declined-hash
  set so a full budget cannot thrash.
- Mounted in **both** All Settings (renderer tab) and the in-game pause menu, from one
  definition in `ui/common`. In-game matters: filters are only comparable if you can
  switch them without leaving the game.
- Build order was scaffold → cheap scalers → neural last, as Vulkan compute. Do not
  jump to the neural path.

### RAISR-HD learned kernels

**Trainer, runtime and bundled kernels implemented; mipmapped textures still excluded (in
progress).** The "pseudo-HD without a pack" path: RAISR
(Romano/Isidoro/Milanfar 2016) - not a neural net. Each input pixel is hashed by gradient
angle/strength/coherence into one of 216 buckets; each bucket owns one small kernel per
output phase, fit by least squares on HD-pack pairs. Cost is ~550 MACs per input pixel, an
order of magnitude under the CPU worker's 4000 MACs/pixel ceiling, so it stays on the
existing worker thread with no Vulkan work.

- `tools/raisr_train.py`: `extract` (ZIP or B2 tar+zstd; PNG, BC1/BC3 DDS, or `.astc` via
  astcenc), `train`, `eval` (PSNR/SSIM vs nearest/bilinear/bicubic/Lanczos on held-out
  textures, plus side-by-side crops). Native size and PSM come from the PCSX2 replacement
  filename (`TEX0Hash[-CLUT][-rWxH]-bits`, bits = PSM:6 TW:4 TH:4).
- **Training pairs**: HD pack texture as the target. Until real dumps exist, the input is
  synthetic: box-downscale the pack back to native size, then quantise the way the PSM says
  the native was stored (5551 for CT16, 256 colours for PSMT8, 16 for PSMT4). Real dumps from
  `textures/<serial>/dumps/` drop into the same `lr/` folder by filename and the fit reruns.
- **One general kernel set per class per scale**, never per game: `world_x2/x4`, `ui_x2/x4`.
  UI is fit on the small-texture subset (the size heuristic in `ClassifyTexture`).
- **Decided**: bundle the four `.a2rk` files in the APK; picker entry named **RAISR-HD**
  (enum appended, picker lists in lockstep); fresh installs default to upscaling ON with
  RAISR-HD 2x on both classes (a stored preference still wins); a Reload-textures button in
  the pause menu plus a hotkey that flushes the hash cache, so a filter change is visible
  without a scene change. Same kernel is applied to R, G, B and A, chosen from luma, so
  alpha edges stay aligned with colour edges.
- Training data comes from sashkinbro's GitHub Releases ZIPs (lossless PNG). ARMSX2's B2
  bucket has a daily cap and returned 403 `download_cap_exceeded` on 2026-09-20. The data
  workspace is outside the repo at `F:\Projectsrmsx2-thoraisr-data\`.
- Wizardry: Tale of the Forsaken Land (SLUS-20259, no pack exists) is the user's live test.
- **Runtime**: `GSTextureUpscalerRaisr.{h,cpp}`, enum entry `RaisrHD` (ordinal 24), texture
  class carried through the worker job, self-test at texture-cache creation (checksum
  `6d1bd0ec` with the first general kernels). Verified bit-exact against the numpy reference
  with a host harness (`raisr-data/hosttest/`, clang against thin stubs).
- **Shipped kernels**: one general set fit on Xenosaga III + Ape Escape 2 + TimeSplitters 2
  (9,126 textures), copied to both `world_*` and `ui_*` in `assets/resources/upscale/`
  (`.a2rk` files are force-refreshed on app update). Held-out 2x PSNR vs Lanczos: UI-type
  pack +3.6 dB, TimeSplitters +0.9, Ape Escape +0.1; 4x on TimeSplitters +0.5. A
  pack-specific fit is worth ~1 dB on UI art, so the planned refit is a small-texture vs
  large-texture split, which is what the World/UI size classes already are.
- **Found on device**: with upstream's `hwMipmap = true` default, the upscaler's guard
  (`paltex || lod || region`) skips every mipmapped texture, which in Wizardry is all of
  them - `skippedGuard` climbs, `upscaled` stays 0. Fix in progress: drop `lod` from the
  guard and create the injected texture with a full mip chain (the replacement path already
  does this; `GenerateMipmapsIfNeeded` fills it). `gpuPaletteConversion` is off by default,
  so palettes are not the blocker.
- Wizardry also ran at `upscaleFloat = 1` (native internal resolution) on the test device;
  texture sharpness is invisible until IR is 2-3x. Set it before judging the look.
- RAISR is an interpolator: it sharpens along edges and cannot invent detail. Expect "a much
  better Lanczos", not an ESRGAN pack. Pixel-art sprites still want xBR; both stay in the
  picker.
### On-device MCP server

**Implemented** (`platforms/android/app/src/github/java/com/armsx2/devtools/`). Full notes:
[docs/mcp-server.md](docs/mcp-server.md). It exists to make the upscaling work measurable,
and it paid for itself on day one: it is how the mipmap guard finding above was made.

- Start: `adb forward tcp:27183 tcp:27183` then
  `adb shell am start -n com.armsx2/.BootSplashActivity --ez devserver true`, or the
  App-settings toggle "Dev server (MCP)". An `MCP :27183` chip shows on the library bar and
  the pause menu while it runs. 127.0.0.1 only. `play` gets a no-op stub (`DEV_SERVER`
  build flag is false there).
- Transport: MCP Streamable HTTP on `POST /mcp` (JSON-RPC, JSON responses, no SSE) plus
  curl-friendly `POST /tool/<name>` with the arguments as the body, and `GET /screenshot`
  for the PNG bytes.
- Tools: `status`, `library`, `boot`, `close`, `pause`, `resume`, `save_state`,
  `load_state`, `screenshot`, `settings_get`, `settings_set` (patch of Settings fields;
  texture upscaling under a `textureUpscale` object), `hotkey`, `texture_stats`,
  `texture_dump`, `log`, `logcat`. `texture_stats` comes from a new JNI
  `getTextureUpscaleStats()`.
- `boot` must hand the core a plain path for `file:` URIs (`HomeViewModel.launch` does the
  same); the raw `file:///...%20...` string fails VM init. A `file:` VIEW intent from adb
  hits Android's app chooser because three activities accept it; use the server instead.
- Save states are the checkpoint primitive: load slot, change a setting, screenshot,
  compare. Slot 1 of Wizardry is the current checkpoint.

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

**Upstream ships it; the fork adds cover badges.** The design notes in
[docs/texture-pack-getter.md](docs/texture-pack-getter.md) predate upstream's
implementation and are kept for the reasoning, not as a to-do.

- Upstream: `TextureCatalog` (serial-keyed, ARMSX2's B2 bucket first, sashkinbro's
  GitHub catalogue as fallback), `TextureOnlineSection` in the Texture Packs screen,
  `TexturePackInstaller` (tar+zstd, ASTC textures uploaded compressed on Vulkan/GLES).
  Library games sort first; the catalogue is cached on disk for 6 hours.
- Fork: `TexturePackPresenceIndex` drives an `HD` pill on library covers - solid when
  `<DataRoot>/textures/<SERIAL>/replacements` exists, hollow when the catalogue has a
  pack. Installed is decided by the folder, not the install record, because the folder
  is what the loader scans. Serial only, no title matching. Tap, or the long-press
  menu row, opens Texture Packs with that game as `contextGame`.
- The catalogue refreshes in the background at app start (`warm`) so badges are right
  in the first session; the Texture Packs screen's own fetch publishes to the index too.
- Runtime resolution order is pack → upscaler → native. A pack texture still loading
  now suppresses the upscale for that key (see `LookupHashCache`), otherwise the two
  async paths raced for `InjectHashCacheTexture`.

### Settings constructor limit (dex)

- `Settings` is a data class; its `copy$default` is a static method taking the instance,
  every constructor parameter, one Int mask per 32 parameters and a marker. A dex
  range-invoke encodes its register count in **8 bits**. Past 255, D8 silently wraps the
  count, the build succeeds, and ART rejects every class that calls `copy()` at load -
  `MainActivityRuntime` on 2026-09-20, so the app crashed on launch after the refresh.
- Upstream's constructor is at 246 fields = 256 registers, i.e. at or past the limit
  itself. **Fork fields never go in that constructor.** The fork's texture-upscaling fields
  live in `TextureUpscaleSettings` / `TextureUpscaleStore` and PINE in `PineSettings` /
  `PineStore` (both `config/`), with the same global + per-game scope rules and a one-shot
  migration from the old JSON keys. That leaves 244 fields = 254 registers.
- `SettingsSizeTest` fails the unit tests when the count would overflow. Run
  `:app:testGithubDebugUnitTest --tests com.armsx2.SettingsSizeTest` after every refresh.
  `dexdump -d classes*.dex | grep "copy\$default"` shows the encoded register list if in
  doubt.

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
