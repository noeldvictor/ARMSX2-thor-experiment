# AGENTS.md

## Project Shape
- ARMSX2 is a cross-platform PCSX2-derived monorepo. Android lives under `platforms/android`.
- The Android frontend is Kotlin/Compose under `platforms/android/app/src/main/java/com/armsx2`.
- Java/JNI compatibility code is under `platforms/android/app/src/main/java/kr/co/iefriends/pcsx2`; the Android native bridge is `platforms/android/app/src/main/cpp/native-lib.cpp`.
- Shared emulator code lives at the repository root, especially `pcsx2`, `common`, and `3rdparty`.

## Build And Verify
- Run the Android Gradle wrapper from `platforms/android`, not the repository root.
- Use `.\gradlew.bat :app:compileGithubDebugKotlin` for focused Compose/Kotlin checks.
- Use `.\gradlew.bat :app:assembleGithubDebug` for a sideloadable debug APK.
- JDK 17 and the Android SDK/NDK are required. A known local JDK is `C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot`.
- On a fresh checkout, run `python app\src\main\cpp\3rdparty\shaderc\utils\git-sync-deps` from `platforms/android` before the first native build.
- The native build also needs Cargo with the `aarch64-linux-android` Rust target. Keep the Windows `.cmd` NDK linker handling in librashader's CMake file.
- The `github` flavor includes the storage access used by the personal sideload build; the `play` flavor intentionally does not.
- Native changes trigger a much heavier CMake/NDK build. Always run `git diff --check`, even when a full native build is unavailable.
- Do not add signing keys, local SDK paths, generated `.cxx` content, APKs, or other ignored build output.

## Local Tooling
- Prefer `rg` for search.
- PowerShell may not accept Unix-style `&&`; run commands separately.
- Network access and SSH Git remotes are expected. `origin` is `git@github.com:noeldvictor/ARMSX2-thor-experiment.git` and canonical `upstream` is `https://github.com/ARMSX2/ARMSX2.git`.
- Use `adb devices` before deployment and install the newest `github/debug` APK with `adb install -r`.

## Git Workflow
- Use only the default/mainline branch. In this clone, user references to `main` mean `master`.
- Do not create or switch to feature branches unless explicitly requested.
- Commit and push completed work unless the user explicitly asks not to.
- Keep commit messages short and specific.
- Never run ultra review (`/code-review ultra` or the `/ultrareview` alias). Verify with the build steps above instead.

## Upstream Refresh
- "Get the latest ARMSX2 updates" means `git fetch upstream`, then merge `upstream/master` into `master` as a real merge commit. Do not rebase or squash; the history is a series of merge commits titled `Refresh Thor fork from upstream ARMSX2`.
- Three files conflict on nearly every refresh. Resolve them this way:
  - `AGENTS.md` - keep this fork's file. Upstream ships its own PCSX2 desktop-oriented `AGENTS.md`; discard that side.
  - `README.md` - keep the fork identity text. Upstream's "Current status" feature checklist does not belong here.
  - `platforms/android/app/src/main/java/com/armsx2/ui/patches/PatchManagerViewModel.kt` - take upstream's `refresh()` body (serial/CRC scoping, no `syncAllEnableLists`) and keep the fork's leading `CheatPresenceIndex.invalidate()` call.
  - `platforms/android/app/src/github/java/com/armsx2/update/UpdaterEntry.kt` - keep the fork's no-op stub. Upstream's updater follows ARMSX2/ARMSX2 releases and would replace the fork with an official build. Also keep `IN_APP_UPDATER = false` for the github flavor in `build.gradle.kts` and no `REQUEST_INSTALL_PACKAGES` / update FileProvider in `src/github/AndroidManifest.xml`. After a refresh, grep for `ARMSX2/ARMSX2/releases` - only `News.kt` (release notes, text only) may reference it.
- After resolving, run `.\gradlew.bat :app:compileGithubDebugKotlin` from `platforms/android` before pushing.
- Also run `.\gradlew.bat :app:testGithubDebugUnitTest --tests com.armsx2.SettingsSizeTest`. `Settings`' constructor must stay at 245 parameters or fewer or the APK crashes at launch (dex range-invoke limit; see "Settings constructor limit (dex)" under Current Thinking below). Never add fork fields to that constructor; use a side store like `TextureUpscaleSettings`.

## Fork Identity
- Treat this as the personal AYN Thor experiment fork, not official ARMSX2.
- Keep the README explicit: vibe-coded with AI, personal use, unsupported, no stability guarantee, no issue/request queue, and fork-it-yourself friendly.
- Do not add an APK download/release section to `README.md` unless the user reverses that preference.
- Keep app-facing repository links pointed at `noeldvictor/ARMSX2-thor-experiment`; retain upstream and PCSX2 links only where attribution is clear.

## Android UI And Cheats
- Follow the existing Compose components and controller-focus patterns.
- Cover art defaults to xlenore's PS2/PS1 cover repositories. Preserve that hardcoded default.
- Cover `CHEATS` badges must come only from real `.pnach` files in `<DataRoot>/cheats`; never infer them from widescreen, 60 FPS, compatibility, or patch folders.
- `CheatPresenceIndex` owns cover-badge indexing. Invalidate it whenever PNACH files are imported, installed, or deleted.
- `platforms/android/app/src/main/assets/cheats/index.tsv` maps bundled CRC filenames to serials and titles so cover badges work before a game is booted. Keep it synchronized with the bundled PNACH set.
- Individual switches are named PNACH sections handled by `PatchManagerScreen`, `PatchManagerViewModel`, `PatchRepo`, and `NativeApp.setEnabledPatches`.
- Bundled exact cheats live at `platforms/android/app/src/main/assets/cheats`. Keep their `patch=` lines commented so every cheat starts off and is enabled deliberately with its own switch.
- Bundled assets copy into `<DataRoot>/cheats` only when missing. Never overwrite a user's edited PNACH.
- This fork's cheat work is about gameplay cheats. Do not use widescreen or 60 FPS patch metadata as cheat state.

## Native Core
- Treat shared `pcsx2`, `common`, `3rdparty`, and `platforms/android/app/src/main/cpp` changes as high blast radius.
- Prefer existing bridges in `NativeApp.java` and `native-lib.cpp` before adding JNI surface area.
- Preserve upstream behavior when the refreshed Compose patch manager already covers a fork feature.

## Rendering And Upscaling
- Present-time enhancement already exists: `GSUpscaler` (FSR1), the librashader `.slangp` chain, and LSFG frame generation. Check `pcsx2/Config.h` before adding a new present-time path; the odds are it is already there.
- Texture upscaling in this fork means upscaling each texture as it is uploaded, not upscaling the finished frame. Do not conflate the two; they have opposite cost models.
- Texture-time work belongs in the hash cache. The injection point is `GSTextureCache::InjectHashCacheTexture`, and `GSTextureReplacements::QueueWorkerThreadItem` is the existing async worker queue.
- World/3D and UI/2D textures are separate user-facing classes, each with its own on/off and its own algorithm. Keep them independent everywhere, including config keys.
- Anything that upscales textures needs a VRAM budget with batch eviction to a low-water mark, a "do not retry" mark on evicted hashes, and a hash-stability heuristic. Animated textures re-hash every frame and will otherwise generate unbounded work.
- Gate any new GPU feature on the existing `MobileGpuArchitecture` detection in `pcsx2/GS/Renderers/Common/GSGPUProfile.h`. Thor ships both an 8 Gen 2 (Adreno 740) and an 865 (Adreno 650) variant, so never assume 8 Gen 2.
- Prefer Vulkan compute over the Hexagon NPU for texture work: the data is already in GPU memory, QNN/SNPE is a per-SoC packaging burden, and NNAPI is deprecated as of Android 15.
- Design notes live in `docs/texture-upscaling-research.md`. Update that file rather than restating its conclusions in code comments.

## Shared Test Device
- The AYN Thor is SHARED. Several Claude sessions do emulator work against it at once, so another session's app stealing foreground focus is normal, not a fault to debug.
- Never fight for the device. If `adb` taps land in another app, focus jumps, or a different emulator is in the foreground, stop driving it and do code work instead - the device being busy is never a reason to stop working or to end a turn.
- Do not force-stop other apps to take focus. That is someone else's session in the middle of something.
- Always `adb shell am force-stop com.armsx2` when finished with a device run, so the next session gets a clean device.
- Device verification is therefore best-effort and opportunistic. Build, compile checks and code review do not need the device; schedule those first and take the device only for the step that genuinely requires it.

## Dev Automation
- The planned on-device MCP server is off by default, binds localhost only, and is reached over `adb forward`. Do not add a LAN bind or an auth scheme without being asked.
- Keep it `github`-flavor only and compiled out of `play`, matching how storage access and LSFG are already handled. The emulator must build and run identically with it compiled out.
- Design notes live in `docs/mcp-server.md`.

## Android Gotchas
- Android compiles regexes with ICU, which is stricter than desktop Java. Patterns that build and pass a Kotlin compile can still throw `PatternSyntaxException` at runtime on device. Escape `]` and `}` inside patterns (`[^\]]`, `\}`), and treat a green Gradle build as no evidence a regex is valid.
- Compiling is not running. After a change that touches startup, the game list, or cover rendering, launch the app on device and check `adb logcat -b crash` before calling it done.

## Style
- Match nearby Kotlin/Compose or C++ style.
- Use Android resources for app-visible text when practical.
- Keep changes scoped to the requested behavior; avoid unrelated monorepo refactors.

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

**Trainer, runtime, bundled kernels and mipmap support implemented; verified on device.** The "pseudo-HD without a pack" path: RAISR
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
  workspace is outside the repo at `F:\Projectsrmsx2-thor
aisr-data\`.
- Wizardry: Tale of the Forsaken Land (SLUS-20259, no pack exists) is the user's live test.
- **Runtime**: `GSTextureUpscalerRaisr.{h,cpp}`, enum entry `RaisrHD` (ordinal 24), texture
  class carried through the worker job, self-test at texture-cache creation (checksum
  `6d1bd0ec` with the first general kernels). Verified bit-exact against the numpy reference
  with a host harness (`raisr-data/hosttest/`, clang against thin stubs).
- **Shipped kernels**: one general 2x set fit on nine packs (Xenosaga III, Ape Escape 2,
  TimeSplitters 2, Suikoden V, Dual Hearts, Tales of Rebirth, Wild Arms 5, Cold Winter,
  Mega Man X7; 17,320 textures), copied to both `world_x2` and `ui_x2` in
  `assets/resources/upscale/` (`.a2rk` files are force-refreshed on app update). Held-out
  2x PSNR vs Lanczos, per pack: +3.5, +0.1, +1.0, +2.1, +0.6, +0.7, +1.0, +0.7, +0.7 - it
  wins on every pack. **The small/large split is retired**: specialist sets fit on the
  <=128 and >128 bands gained <=0.06 dB over the general set on their own bands
  (`raisr-data/fit_round2.log`), so one set serves both classes. The 4x set is the same
  nine-pack general fit (small band +0.6 dB over Lanczos; specialists again within
  0.06 dB).
- God Hand's pack (1.8 GB) failed mid-download on an HTTP/2 stream error; refetch it for a
  later round. Mega Man X7's pack is mostly mip files (184 usable textures).
- **Found and fixed on device**: with upstream's `hwMipmap = true` default, the upscaler's
  guard used to skip every mipmapped texture, which in Wizardry is all of them -
  `skippedGuard` climbed, `upscaled` stayed 0. `lod` is no longer a guard: level 0 is
  scaled and the injected texture is a **single level**, exactly like a pack texture
  without mip files (manual-LOD sampling clamps to level 0). A generated chain was tried
  first and produced rainbow speckle on Wizardry's portraits - levels 1..N sampled before
  they held data in manual-LOD mode - so do not reintroduce it without generating the
  chain on the worker and uploading every level explicitly. `gpuPaletteConversion` is off by default, so palettes are
  not a blocker. First A/B on the Wizardry opening dialogue (same frame, IR 3x, reload
  between): RAISR-HD is visibly crisper than native on hair and line art; mean |diff|
  2.5/255. Screenshots in `raisr-data/shots/`.
- **Follow-ups seen in that A/B**: the dialogue font never reaches the upscaler
  (region-texture or target path - one of the remaining guards); and on the opening
  dialogue `upscaled`/`evicted` climb ~4/s with `held` flat, i.e. a few textures churn
  through the hash cache continuously (snow particles or a fade) and get re-upscaled each
  time - cheap here, but the stability heuristic in the design is not yet applied. The
  earlier "Lanczos+CAS speckle" was the same mip-chain bug, not a filter fault.
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

**Coverage tool and an authoring workflow implemented.** Full notes:
[docs/cheat-tooling.md](docs/cheat-tooling.md). The workflow is the `ps2-cheat`
skill (`.claude/skills/ps2-cheat/SKILL.md`) driving the `pcsx2` MCP server
(`tools/pcsx2_mcp/`, registered in `.mcp.json`).

- `tools/cheat_coverage.py` measures the gap. On the test library: 590 bundled files
  covering 534 serials, and **46% of resolvable games have no cheats**.
- Unresolved filenames are reported separately from missing on purpose - unknown is
  not the same as absent, and merging them overstates the gap.
- Bundled files are keyed by **CRC**, not serial. That is the real friction when
  importing for a game you do not own.
- **Authoring happens on desktop PCSX2 on the PC, not on the Thor.** The Thor build has
  no debugger; desktop PCSX2 (`F:\Projects\pcsx2-desktop\pcsx2`, portable, v2.8.2) has
  PINE, save states and the same PNACH loader, so an address found there is the address
  on the Thor (same ELF, same CRC). `tools/pcsx2_mcp/setup.py` reproduces the install.
- The MCP server does: PINE memory read/write, save/load state, button presses via
  SendInput, screenshots (F8 hotkey), 32 MB RAM snapshots from uncompressed save states,
  a snapshot-diff search, capstone disassembly, lui/lo cross-refs and PNACH output for
  desktop, the repo bundle and the Thor. `server.py cli <tool>` is the same thing from a
  shell; each CLI call is a fresh process, so snapshots do not persist between calls.
- **PCSX2 2.x treats a named `[Cheats/Name]` section as a switch that is off by default.**
  It only applies once `gamesettings/<SERIAL>_<CRC>.ini` lists it under
  `[Cheats] Enable = Cheats/Name` - the full header, prefix included. `pnach_write`
  handles that for desktop. On the Thor the switch is the same ini entry **plus** the
  `patch=` line uncommented in `<DataRoot>/cheats/<CRC>.pnach` (the bundle ships the line
  commented, the in-app cheat manager uncomments it) **plus** `enableCheats` on globally.
  A file whose lines are all commented is "0 cheats found", not "found, disabled".
- **The technique that worked** (Okage, no encounters): a burst of snapshots while walking
  into a ghost, then diff "constant across the field snapshots, changed at the swirl
  frame", restricted to the ELF `.data/.bss` band (`0x1F0000-0x320000`) because the battle
  overlay load swamps everything above `0x320000`. That gave the encounter record and the
  field controller's state field; every `sw x, 0x48(base)` site listed the one store of
  the encounter state, and that function is the ghost update with the distance compare.
  Forcing the compare's `bc1f` into `b` (same offset, delay slot untouched) is the cheat.
  Prefer that over a data freeze: one line, no per-frame write.
- Okage: Shadow King (SCUS-97129, CRC `E0426FC6`): field ghost update at `00137218`,
  contact branch at `00137508`, encounter record at `002D9BC0`, battle flag
  `002D9ABC` (0 field, 1 battle), field controller state at `+0x48` (8/11 = encounter).
  Ari's field position is a vec3 at `001FB980`. Shipped as
  `assets/cheats/E0426FC6.pnach`, verified on desktop and loaded on the Thor.
- Saves for a game with no progress come from GameFAQs; its pages sit behind
  Cloudflare, so fetch with Playwright in `F:\Projects\pcsx2-desktop\.venv` (headed
  Chromium passes the challenge) or ask the user to download. Okage keeps every slot in
  one save directory, so a second save needs a second memory card. `mymcplus` imports
  `.max`/`.cbs`/`.psu` into a fresh `.ps2` image.
- PCSX2 v2.8 reads the **contents** of `portable.txt` as the data root; the file must be
  empty, or the ini lands in `<exe dir>/<that text>/inis` and the setup wizard reappears.

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
