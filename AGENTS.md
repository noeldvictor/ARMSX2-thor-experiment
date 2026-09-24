# AGENTS.md

The single instruction file for this repository, for every agent and tool (there is no
CLAUDE.md any more). Rules first, then "Current Thinking": active work, decisions already
made, and implementation status per item. Update this file rather than adding another.

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
- In Git Bash, `export MSYS_NO_PATHCONV=1` before `adb push`/`adb shell` with device paths: MSYS
  rewrites `/data/local/tmp/...` into `C:/Program Files/Git/data/...` and the push fails or lands
  nowhere while a stale file on the device gets used.

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
- The fork installs as **`com.armsx2.thor`** (`armsx2.applicationId` in `platforms/android/gradle.properties`), next to official ARMSX2 (`com.armsx2`), with its own launcher icon (github flavor res). Only the applicationId differs: the Kotlin namespace is still `com.armsx2`, so adb component names are `com.armsx2.thor/com.armsx2.Main`, not `com.armsx2.thor/.Main`. `com.armsx2` on a device is official ARMSX2 (or an old fork build) - never force-stop, uninstall or push into it for fork work. Each app keeps its own data folder.

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
- On the Thor's Qualcomm driver (texture barriers off) every draw that reads its own target is an end-pass + copy + restart. Before accepting one, look for a no-read form of the same result: dual-source blend (`GSFastStencilShadow.h`), logic ops and a stencil copy kept current inside the pass (`GSAlphaBitLogicOp.h`). Gate such paths on the device features they need (`!texture_barrier`, `logicOp`, `stencil_buffer`), never on `!texture_barrier` alone, and prove them with a bit-exact frame compare from a gsrunner replay on the device.
- Prefer Vulkan compute over the Hexagon NPU for texture work: the data is already in GPU memory, QNN/SNPE is a per-SoC packaging burden, and NNAPI is deprecated as of Android 15.
- Design notes live in `docs/texture-upscaling-research.md`. Update that file rather than restating its conclusions in code comments.

## HD Texture Packs (How-To For AI Agents)
When asked for HD textures for a game, this is the job, in this order. The `hd-texture-pack`
skill (`.claude/skills/hd-texture-pack/SKILL.md`) has the detail; `hd-packs/README.md` is the
user-facing version.
- **Know the three options before starting.** A standard PCSX2 pack (the online catalogue in the
  Texture Packs screen) wins if one exists. RAISR-HD already sharpens every game on the device with
  no pack. A **disc pack** is this fork's own: every texture read off the disc, upscaled on the
  desktop GPU, matched exactly by hash. Build a disc pack only when there is no pack worth using.
- **A game with a recipe** (`hd-packs/<SERIAL>-<name>/`): run
  `python tools/disc_textures/make_pack.py hd-packs/<folder> --disc <chd|iso> --model <model>`
  (Python venv `F:\Projects\pcsx2-desktop\.venv`, models in `F:\Projects\pcsx2-desktop\models\`,
  `chdman` in `F:\Tools\MAME`). It resumes; `--from STEP` redoes a step. Check the index SHA-256
  against `game.json`.
- **A new game is reverse engineering, not a button.** Steps, and do not skip the first:
  1. Prove it is a candidate: dump one scene's textures in desktop PCSX2 and check each dumped
     texture is an exact crop of a disc file and its name's hashes come from disc data
     (`xxh3_64` of the crop's indices, row by row; `xxh3_64` of the palette). If not, stop and say so.
  2. Write `hd-packs/<SERIAL>-<name>/extractor.py`: disc-format code only, the
     `disc_images()` contract in `tools/disc_textures/extract_native.py`. Copy Okage's folder.
  3. A 1x pack (built from the native PNGs) must replay **bit-identical** to an *empty* pack (an
     index with no images) in gsrunner on the Thor. That is the proof the matching is exact; only
     then upscale. Not against no pack: with any disc pack loaded, menu sprites are narrowed to
     their UV rect, which changes bilinear sprite edges by a pixel.
  4. Images the game colours at runtime (fonts) are palette-free: upscale them through the
     game's real palette (`palette_free_palette()`, read from the `Disc atlas: palette` log
     line). A guessed grey ramp upscales into the wrong indices.
  5. Finish the recipe: one clean `make_pack.py` run into a fresh `--work`, then per-step times
     with the PC's CPU/GPU, output sizes, ISO SHA-1, model SHA-256 and index SHA-256 in the
     README (from `hd-packs/TEMPLATE.md`: before/after, build estimate, coverage and gaps, format
     notes) and `game.json`, 3x before/after shots in `media/`, and what it does *not* cover.
     Add a row to `hd-packs/GAMES.md` - the games list the main README links to.
- **Verify on the device, not on the desktop.** gsrunner replays of a GS dump
  (`/data/local/tmp/gsr`, pack under `cfg/ARMSX2/textures/<SERIAL>/replacements`) for exact
  comparisons, then the app via the dev server: `hd_test` for A/B, `texture_stats` for
  `discAtlasMatches`/`Misses`. Judge at 3x internal resolution, the resolution the user plays at.
- **Never commit textures, packs, zips or models.** The art is the publisher's and 4x-UltraSharp is
  CC BY-NC-SA. Recipes (code + checksums + small screenshots) are what goes in the repo.
- **Describe it honestly.** Per game, needs format work (usually AI-assisted), only palette
  textures today. Never write it up as a generic or one-click tool.
- Emulator side: `pcsx2/GS/Renderers/HW/GSDiscAtlas.*`, the probe in `GSTextureCache.cpp`, the
  tight sprite region in `GSRendererHW.cpp`. Keep the 1x bit-identical proof passing after any
  change there.

## Shared Test Device
- The AYN Thor is SHARED. Several Claude sessions do emulator work against it at once, so another session's app stealing foreground focus is normal, not a fault to debug.
- Never fight for the device. If `adb` taps land in another app, focus jumps, or a different emulator is in the foreground, stop driving it and do code work instead - the device being busy is never a reason to stop working or to end a turn.
- Do not force-stop other apps to take focus. That is someone else's session in the middle of something.
- Always `adb shell am force-stop com.armsx2.thor` when finished with a device run, so the next session gets a clean device.
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

Registered in `.mcp.json` as `armsx2-thor` (HTTP, `127.0.0.1:27183/mcp`), next to the desktop
`pcsx2` server. It needs `adb forward tcp:27183 tcp:27183` (wireless adb works:
`adb connect <thor-ip>:<port>`) and the dev server running on the device (App settings
toggle, or `am start -n com.armsx2.thor/com.armsx2.Main --ez devserver true` against a running game).

**Implemented** (`platforms/android/app/src/github/java/com/armsx2/devtools/`). Full notes:
[docs/mcp-server.md](docs/mcp-server.md). It exists to make the upscaling work measurable,
and it paid for itself on day one: it is how the mipmap guard finding above was made.

- Start: `adb forward tcp:27183 tcp:27183` then
  `adb shell am start -n com.armsx2.thor/com.armsx2.BootSplashActivity --ez devserver true`, or the
  App-settings toggle "Dev server (MCP)". An `MCP :27183` chip shows on the library bar and
  the pause menu while it runs. 127.0.0.1 only. `play` gets a no-op stub (`DEV_SERVER`
  build flag is false there).
- Transport: MCP Streamable HTTP on `POST /mcp` (JSON-RPC, JSON responses, no SSE) plus
  curl-friendly `POST /tool/<name>` with the arguments as the body, and `GET /screenshot`
  for the PNG bytes.
- Tools: `status`, `library`, `boot`, `close`, `pause`, `resume`, `save_state`,
  `load_state`, `screenshot`, `settings_get`, `settings_set` (patch of Settings fields;
  texture upscaling under a `textureUpscale` object), `hotkey`, `texture_stats`,
  `texture_dump`, `gs_dump`, `hd_test`, `log`, `logcat`. `texture_stats` comes from the JNI
  `getTextureUpscaleStats()` and includes the disc-pack counters. Table in `docs/mcp-server.md`.
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
- **Finding cheats that already exist** is the `find-ps2-cheats` skill with
  `tools/cheat_finder/`. Sources, in order: the bundle; XiGuanChi/PCSX2-CheatsDB on GitHub
  (23k `SERIAL_CRC.pnach` files, the only serial-keyed public DB - the CRC-named
  collections are the NetherSX2 set we already ship); then gamehacking.org, which exports
  PCSX2 `.pnach` per game but sits behind Cloudflare - stealth Playwright in a headed
  Chromium typing into its search form gets through, `?q=` URLs and plain fetches do not.
  **The user asked that gamehacking not be hammered**: ~10 s between requests, three
  requests per game, one pass, no retries. Its exports still contain un-decrypted GameShark
  v1 lines (`91F68566`-style addresses); `gamehacking_export.py` drops those sections.
  Result of the 2026-09-22 sweep is `docs/games-without-cheats.txt`
  (`tools/cheat_finder/write_report.py` merges the two checks); the exported files sit in
  `F:\Projects\pcsx2-desktop\gamehacking\` until the user installs them on the Thor.
- **Use real serials, not filename guesses.** `cheat_coverage.py --library` takes the dev
  server's `library` JSON (the app reads SYSTEM.CNF); the title matcher had "Xenosaga Episode I
  (USA)" as the Asian SCAJ disc and six US discs as PAL, so cheats were fetched for discs the
  Thor does not have. By real serial the library is 83/123 covered (2026-09-22). Translation
  patches keep the original serial (`GameIndex.yaml` `name-en:`; never guess - SLUS-20952 is
  Tak 2, not Shadow Hearts).
- Frame-rate / no-interlace / widescreen entries found under a "cheats" DB are not cheats
  here (AGENTS rule above); drop those sections before bundling.

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

### Per-game notes

`docs/games/<game>.md` holds what was measured, shipped and left open for one game
(`docs/games/okage.md` is the first). Put game-specific findings there, not here; this file
keeps the cross-cutting rules and the one-paragraph status below.

### Desktop measurements do not transfer to the Thor as-is

Learned the hard way on Okage (2026-09-22): desktop PCSX2 on D3D12 said the house scene was
cheap (GS 2.4 ms), and the Thor ran it at 45 fps with fast-forward on. The desktop OSD was
showing the answer and it was read as harmless: **1,389 barriers per frame**. A barrier on
desktop is a cheap in-pass texture barrier; on the Thor's Qualcomm proprietary Vulkan
driver the log says `unreliable in-pass render-target self-read - forcing the RT-copy blend
path` and `ROAA=NO fbfetch=NO(barrier-fallback)`, so every barrier is *end render pass, copy
the render target, restart the pass* - GS thread 99% (21 ms) and GPU 81% on the device.

- Count **BAR** as well as RP on desktop, and treat each BAR as a render-pass break plus a
  copy on the Thor. Render passes alone understated the cost by two orders of magnitude.
- Measure on the device, not by analogy: the emulator's `PerfLog` line in `emulog.txt`
  (30 s averages of fps, EE, GS, VU, GPU) over wireless adb, settings flipped live through
  the dev server (`am start -n com.armsx2.thor/com.armsx2.Main --ez devserver true` reaches a running game
  through `onNewIntent` without restarting it).
- Get the frame itself: pause menu > Capture GS Dump writes `snaps/*.gs.zst`; desktop PCSX2
  replays it with the OSD counters (`pcsx2-qt.exe -- dump.gs.zst`), and
  `pcsx2-gsrunner` (NDK executable: `<Sdk>/cmake/3.22.1/bin/ninja.exe pcsx2-gsrunner` run in
  `platforms/android/app/.cxx/Debug/<hash>/arm64-v8a`) replays it on the Thor's own GPU with
  `-perf -stats-json`. Use that ninja - the one Gradle runs - and no other: the winget ninja
  1.13 on PATH and CMake 3.31's 1.12 write `.ninja_log` in their own format, and the next build
  by a different ninja throws the log away and recompiles the whole native tree.
- For correctness, compare with the software renderer, not with the previous build: Okage's
  shadow was wrong on the Thor in every build, so "bit-identical to before" proved nothing
  about it. Desktop PCSX2 makes the dump from any save state (`tools/pcsx2_mcp` hotkey
  `gs_dump`, F9); replay it on the Thor with `-renderer vulkan` and `-renderer sw` and diff.
  Per-draw targets and HW config for a draw range: `-set EmuCore/GS/DumpGSData=true`,
  `HWDumpDirectory`/`SWDumpDirectory`, `SaveRT`, `SaveAlpha`, `SaveHWConfig`,
  `SaveDrawStart`/`SaveDrawCount`, `SaveFrameStart`/`SaveFrameCount`.
- Desktop save states load on the Thor when the save-state version matches
  (`g_SaveVersion` in `pcsx2/SaveState.h`, `0x9A59` for both on 2026-09-22); copy them to
  `<DataRoot>/sstates/` and use the dev server's `load_state`.
- Blending accuracy is not a free knob here: Minimum moved Okage's load onto the GPU
  (40 fps, GPU 98%) instead of removing it. Texture upscaling cost nothing measurable.

### Okage frame drops

**Full notes: [docs/games/okage.md](docs/games/okage.md).** Diagnosed on desktop PCSX2 2.8.2 (D3D12, native res, Tenel outdoor scene, PCSX2's own OSD
counters).** The game is not GPU-heavy; it is *render-pass* heavy under the GameDB defaults:

| config | barriers | render passes | GS thread | GPU |
| --- | --- | --- | --- | --- |
| GameDB default (autoFlush sprites, blending Medium) | 656 | **144** | 2.76 ms | 4.70 ms |
| autoFlush off, blending Medium | 580 | 10 | 2.12 ms | 4.76 ms |
| autoFlush off, blending Minimum | 257 | 8 | 1.90 ms | 3.31 ms |
| indoors, GameDB default | 621 | 5 | - | - |

- The 134 extra render passes are the depth-of-field pass: the game reads its frame buffer
  back through a PSMT4HH channel shuffle (TEX0 at block 0x1180, the other frame buffer, TBW 4,
  CLUT 0x267A/0x267E double-buffered) and `autoFlush: 1` flushes every sprite of it. On a
  tiled GPU a render-pass break is a full tile store/load of the frame buffer; at 3x IR that
  is the frame drop. **Fork GameDB now omits autoFlush for SCUS-97129** (visually identical at
  native res, mean |diff| 1/255).
- ~320 of the barriers are shader-emulated blends at Medium accuracy; `recommendedBlendingLevel`
  in the GameDB can only raise the level, so lowering it is a global user setting (the Thor
  already runs Basic = 577; Minimum = 257, only Ari's shadow blend changes).
- `skipdraw` cannot remove the DoF: skipping 1 draw leaves the blur, 2+ kills the field merge
  (dark scanlines) - the DoF draws come later than the first frame-buffer-sampling draw.
- The DoF display list is **built once per scene** (a PINE poke to the TEX0 qword survives),
  so a write breakpoint fires only on a scene load, and `sq` stores are covered by PCSX2's
  memchecks. The builder was not found yet: no TEX0 template in .data, PSM 0x2C is passed as
  an argument (the generic 4-bit texture creator at `00194A20` also uses 4HL/4HH layouts), so
  the next step is a memory write breakpoint on the packet with the debugger open *across* a
  scene load (do not relaunch PCSX2 in between - the debugger and its breakpoints die with the
  process). `tools/pcsx2_mcp` has `click`/`type_text` and multi-monitor window capture for
  driving the Qt debugger; `find_window` skips the debugger window.
- **DoF removed by code patch** (GameDB `patches: E0426FC6`, also a bundle cheat switch): the
  effect-slot wrapper `00192E08` returns 0 (`jr ra` / `addiu v0,zero,0`), so the frame loop
  links no DoF packet. 15,740 -> 3,083 primitives and 144 -> 5 render passes a frame. Found
  with a debugger write breakpoint across a scene load and the save state's `cpuRegs` for the
  return chain - the pattern for any "who builds this packet" question.
- **On the Thor the remaining cost is the shadows**, not the DoF: 350 shadow strips a frame,
  each drawn three times with a 1-bit destination-alpha stencil (`FBMSK 7FFFFFFF` mark, DATE
  draw, clear) = 1,389 barriers in a house scene, each a render-target copy on the Adreno
  driver (GS 99%, 45 fps with fast-forward on). Removing the stencil trick breaks the
  outdoor silhouette; removing the shadows (switch "No Character Shadows (Much Faster)") takes
  it to 1 barrier. **Fixed in the renderer with the shadows kept** (Vulkan, no-texture-barrier
  devices only): mark/clear via logic ops (`GSAlphaBitLogicOp.h`), the DATE draw from a stencil
  copy of the test that the mark/clear pipelines keep current inside the render pass. Thor
  replay: GS 18.8 -> 5.9 ms a frame, 708 -> 161 render passes, frames bit-identical. The
  shadow's *shape* was also wrong on the Thor in every build (half-strength Ad blend, fixed by
  an alpha-doubling pass under the stencil, which also let every strip keep the stencil copy:
  9-12 render passes a frame). In game: 2x fast-forward holds 119.9 fps in Tenel and the World
  Library. Full notes in `docs/games/okage.md`.
- **Disc HD packs are the fork's HD texture system** - see "Disc HD texture packs" below.
- GS dumps parse fine in Python (zstd; header, state, 0x2000-byte priv regs, then packets
  0 = transfer / 1 = vsync / 2 = readfifo / 3 = regs); rewriting A+D register values in a dump
  and replaying it on desktop is the fastest way to test "what if the game did X" before
  looking for the code.

### Disc HD texture packs

**Implemented and verified on the Thor (2026-09-22).** HD packs built from the game disc with no
gameplay. One recipe folder per game in `hd-packs/<SERIAL>-<name>/` (`game.json`, `extractor.py`,
README with measured RTX 3060 timings, gaps, before/after shots, reference checksums);
`hd-packs/README.md` is the user guide, `docs/hd-texture-packs.md` how it works, the
`hd-texture-pack` skill the procedure for adding a game.

- What makes it possible: a drawn texture is a rectangle of a disc image, and PCSX2 keys a region
  texture by XXH3 over the rectangle's palette indices plus XXH3 of the palette - computable
  offline. Okage: 35/35 dumped keys reproduced from disc data.
- Pack = whole upscaled disc images (`atlas/`) + `disc-atlas.a2at` (v4: palette hash, size and
  indices per image, hash of every 16x16 block every 8 px, plus a table for palette-free and
  true-colour blocks). `GSDiscAtlas` matches on a stock-name
  miss: probe block -> candidates -> the crop whose hash equals the texture's *content hash* (the
  atlas's own XXH3 of the texels as the GS reads them, computed lazily on the first candidate), so a
  match is exact. It registers the crop under the stock name; the normal async loader does the rest.
- 4x packs store HD images as ASTC 4x4 (index v5, `astc.py` batches astcenc): crops and composites
  are block copies, uncovered texels constant blocks; palette-free index maps stay PNG; `--format
  png` for the 1x exactness test and 2x packs. Okage ASTC vs PNG: same matches, 47-55 dB frames.
- Composites: a texture no crop matches is split into disc images by block votes
  (`GSDiscAtlas::MatchComposite`); a placement counts where its texels equal the texture - up to
  1/256 may differ and stay native (bytes a game parks inside a texture: Tales of Destiny's deck
  map, 11 texels) - and the rest keeps its native colour. That covers sprite sheets and text built
  from glyphs in any game, with no hook in the upload path. Palette textures only.
- The index is memory-mapped (Tales of Destiny's is 1.1 GB: ~40 MB resident on the Thor). The
  builder leaves out exact duplicates and blank images. A 4x pack is 16 bytes per native texel:
  Okage 467 MB, Tales of Destiny 14.8 GB (8.5 GB zipped; real map art, audited); `make_pack.py` prints the
  estimate before the upscale.
- Tales of Destiny DC (`hd-packs/SLPS-25842-tales-of-destiny-dc/`) is the second recipe, in
  progress: ship 47/47 and title 16/16 disc textures matched, bit-identical at 1x; the 4x pack is on the
  Thor (`/sdcard/armsxdata/textures/SLPS-25842/`) and runs in the app at 59.9 fps at 3x. Only those
  two scenes are checked.
- **Never change PCSX2's texture hashing (`HashCacheKey`).** It is the name every standard pack
  and dump uses. Where the stock key cannot be reproduced from disc data (raw-block keys of
  full-size textures), the atlas's own content hash does the checking instead.
- Menus: a UV-selected sprite of a big palette sheet is narrowed to its own texels
  (`GSRendererHW`, only while a disc atlas is loaded; last texel `ceil(max - 0.5) - 1`).
- Proof standard: a 1x pack built from the native disc images renders bit-identical frames to an
  empty pack (gsrunner on the Thor; Okage: Library, Tenel, house and menu dumps, 2026-09-23). Keep
  it that way when changing any of this.
- True colour (PSMCT24): the key hashes RGB + TEXA alpha (TA0, or 0 for black under AEM), so it
  is computable; blocks are indexed by RGB, the crop check and the loader use the key's TEXA.
  Okage uses AEM with TA0 0x80 (`...-80c02a81` names). PS2 alpha above 0x80 (16 Okage palettes)
  is carried raw through the upscale, never doubled and clipped (`raw_alpha()`).
- Palette-free images (fonts coloured at runtime): matched by TEX0 alone, shipped as an HD index
  map painted with the game's palette at load. Upscale them through the game's *real* palette
  (`palette_free_palette()` in the extractor; the emulator logs it as `Disc atlas: palette`) - a
  grey ramp made Okage's index-1 ink hollow.
- PSMCT32 images keep RGBA (a third flag, four bytes a texel), blocks indexed by RGB.
- Not covered yet: PSMCT16 disc images, mipmapped keys with more than one level. Upscale edge
  bleed between atlas neighbours. Okage's IQ24 font and its one PSMCT32 texture are in the pack
  but not yet seen on screen.
- Tools: `tools/disc_textures/` - `make_pack.py` (one command: disc -> extract -> upscale ->
  build -> zip, timings, checksum), `disc.py`, `extract_native.py` (the extractor contract),
  `upscale.py`, `build_disc_pack.py`; for discovering a new game's formats `disc_codecs.py`
  (decompressors, numba), `tim2.py` (lenient TIM2), `gsdump.py` (uploads in a GS dump, `--locate`
  them on disc), `gsmem.py` (GS swizzle, VRAM from a dump) and `verify_dumps.py` (extractor vs
  PCSX2 texture dumps). A new game needs only `hd-packs/<game>/extractor.py`.
- README honesty rule: disc packs are per game and need reverse engineering (usually AI-assisted)
  for each new game. Never describe them as a generic or one-click tool. No packs in the repo.
- Found on the way: a texture reload while the upscaler worker ran used freed RAISR kernels
  (crash); sets/models are shared_ptr now.

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
- **HD defaults for the Thor's 1080p panel** (2026-09-22): internal resolution defaults to
  `DeviceTier.hdUpscaleDefault()` - 3x on Snapdragon 8 Gen 2 and newer (SM8550+), 2x otherwise
  (the 865 Thor) - instead of 1x, and `loadTextureReplacements` defaults on. Existing saves on the
  exact old values are moved once (`config.migrated.thorHdDefaults`).

### Working with the shared Thor

- **The device is shared with other Claude sessions.** Foreground focus being stolen
  mid-run is other agents working, not a bug on the device. Full rules in `AGENTS.md`.
- **Never stop because the Thor is busy.** There is always code work available - filters,
  tooling, docs, review. Device time is opportunistic; take it for the one step that needs
  it and give it back.
- **Close the emulator when done**: `adb shell am force-stop com.armsx2.thor`.
- Do not force-stop other apps to grab focus.
