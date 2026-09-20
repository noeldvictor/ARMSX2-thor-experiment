# Texture Upscaling On AYN Thor

Design direction plus the research behind it.

**Verified on device (AYN Thor, 2026-08-21):** all thirteen filter kernels pass their
self-test at both scales with no out-of-bounds writes (26/26), and the neural path loads a
model, runs inference and pixel-shuffles to the expected checksum. What is *not* yet
measured is how it looks or what it costs in a real scene - that needs the OSD counters
read during actual play.

Last researched: 2026-08-21.

## What This Is

**Upscale textures inside the emulator, as you play. Not the screen.**

The distinction matters and is easy to lose. Present-time upscaling enlarges the
finished frame. This enlarges each *texture* at the point the emulator uploads
it, so the game renders with better source art. No HD texture pack required, no
playthrough needed to prepare anything, and it works on any game.

HD packs stay optional and complementary — a pack wins where it exists, this
covers everything else.

### Why texture-time and not present-time

1. **Present-time is already covered here.** `GSUpscaler` (`pcsx2/Config.h:478`)
   ships FSR1 as EASU + RCAS Vulkan compute passes, a librashader `.slangp` chain
   runs at present after ShadeBoost/FXAA (`pcsx2/Config.h:1053`), and LSFG frame
   generation is wired into the Vulkan present path (`pcsx2/Config.h:1060`).
2. **Per-frame inference is a thermal non-starter on a handheld.** Sustained load,
   every frame, forever, on a GPU already busy presenting the game.

Texture-time inverts the cost model. Textures are uploaded, hashed, and cached, so
the work happens **once per unique texture** and the result lives in the hash
cache. Steady-state cost returns to zero.

| | Present-time | Texture-time |
| --- | --- | --- |
| Work unit | 2.07M px (1080p) | Usually <= 256x256 = 65K px |
| Frequency | Every frame, 60x/sec | Once per unique texture |
| Steady state | Constant load | Zero |
| Thermal shape | Sustained | Bursty, at scene loads |
| Latency budget | <16ms hard | Async, hidden |

Textures also need not be ready *this frame*. Queue them, run on a worker, inject
when done; the game draws the original meanwhile.

## Decided Design

### Two independent texture classes

World/3D textures and UI/font/2D textures get **separate menu entries**, each with
its own on/off **and** its own algorithm. They want genuinely different treatment:
a neural upscaler that flatters a painted wall will mangle a HUD font, and xBRZ
that makes a sprite crisp can look wrong on a photographic surface.

### Scale factor

Explicit **Off / 2x / 4x**, chosen by the user. Not auto-derived from the internal
resolution multiplier.

Thor's top display is 1080x1920, so 1080p is the output ceiling. Past ~4x the
extra pixels cannot be shown and only cost VRAM — 4x is the top of the useful
range, not an arbitrary cap.

### Scope

**Per-game, with a global default.** PCSX2 already has per-game properties and
this fork has a Compose pause menu. The right algorithm differs sharply per title,
so per-game is where this actually lives.

### Menu presentation

Grouped by family, with a `RECOMMENDED` badge on the suggested pick **and a short
plain-language comment on every algorithm**. Twenty bare names like `2xSaI` teach
nobody anything on a handheld screen; the one-liner is what makes the list usable.

### No per-game recommendation database

**Style presets, user picks.** Ship named presets — 2D Sprite, Cel-shaded,
Painted, Photographic, Neutral — that set both texture classes at once. No curated
serial/CRC table to maintain and no heuristic to be silently wrong.

### VRAM budget behavior

**Warn once in the UI, then evict**, with two guards that matter more than the
policy itself:

- **Batch-evict to a low-water mark** (~80% of budget), never one texture at a
  time at 100%. Otherwise a busy scene sits pinned at the ceiling.
- **Mark evicted hashes "do not retry"** for a period. Without this the same
  texture re-upscales every few seconds, costing more than never upscaling it.

`HashCacheEntry` already carries `refcount` and `age` (`GSTextureCache.h:130`), so
LRU is available.

## The Algorithm Library

Roughly twenty, grouped as the menu groups them. Comments here are the source for
the menu comments.

**Status.** Seventeen entries are selectable:

- *Resample*: Nearest, Bilinear, Sharp Bilinear, Bicubic (Catmull-Rom), Mitchell,
  Lanczos-3, Lanczos+CAS
- *Edge-directed*: Scale2x, Eagle, SuperEagle, 2xSaI, Super2xSaI, xBR
- *Neural*: Anime4K, FSRCNN, SESR, ESPCN — architecture only, each needs a model file
  (see [neural-models.md](neural-models.md))

Still declining and left native: **HQx, xBRZ, SuperxBR, ScaleFX, OmniScale**. These
were deliberately not written from memory. HQ2x is a 256-case table keyed on a
neighbour-difference pattern, xBRZ is several hundred lines of rotation-based blend
decisions, and the other three are shader-shaped rather than scalar. A subtly wrong
implementation shipped under a famous filter's name is worse than an absent one -
people would judge the filter, not the bug. They need porting from a reference, not
reconstructing.

The picker orders entries softest-to-sharpest within a family rather than by enum
order, because that is how someone actually auditions filters.

### Pixel-art / edge-directed
Best on indexed, sprite, and UI art — which is most of what PS2 stores, since its
4MB VRAM pushed games hard toward PSMT8/PSMT4.

| Algorithm | Comment |
| --- | --- |
| Scale2x | Fastest. Blocky but clean; good on very low-res sprites. |
| Eagle | Old and chunky. Nostalgic look more than a quality pick. |
| 2xSaI | Smoother, softer edges. |
| Super 2xSaI | Smoother still; loses fine detail. |
| Super Eagle | Sharper Eagle variant. |
| HQ2x / HQ3x / HQ4x | Smooth gradients, rounded corners. The classic emulator look. |
| xBR | Strong edge detection; preserves line art well. |
| **xBRZ** | Sharpest edges, fewest artifacts. Best default for 2D. |
| Super xBR | Less cartoony than xBRZ; better on semi-photographic art. |
| ScaleFX | Shader-based; keeps fine detail others smear. |
| MMPX | Modern (2020), built for pixel art. Excellent on tiny sprites and text. |
| Omniscale | Hybrid; handles flat art and gradients in one pass. |

### Resample / sharpen
Neutral and nearly free. Never makes art look *wrong*, only softer.

| Algorithm | Comment |
| --- | --- |
| Bilinear | Soft and safe. The do-no-harm option. |
| Bicubic / Catmull-Rom | Slightly sharper than bilinear. |
| Lanczos | Sharpest classical resample; can ring on hard edges. |
| **Lanczos + CAS** | Resample then contrast-adaptive sharpen. Best neutral pick. |

### Neural
Best on painted and photographic textures, worst on crisp UI. Costliest to build
and to run.

| Algorithm | Comment |
| --- | --- |
| Anime4K (A/B/C) | Cel-shaded and anime art; restores line work. Already runnable through the existing slang chain. |
| FSRCNN / FSRCNNX | Classic real-time SR CNN; existing shader ports. |
| **SESR (M3/M5)** | Arm-designed for mobile. Most efficient neural option here. |
| ESPCN | Cheapest CNN, weakest result. |

### Suggested preset mappings

| Preset | World textures | UI / 2D |
| --- | --- | --- |
| 2D Sprite | xBRZ | MMPX |
| Cel-shaded | Anime4K | xBRZ |
| Painted | SESR | Off |
| Photographic | Lanczos + CAS | Off |
| Neutral | Bilinear | Off |

## Hooks That Already Exist

None of this needs inventing:

- **Injection point.** `GSTextureCache::InjectHashCacheTexture(key, tex, alpha_minmax)`
  at `pcsx2/GS/Renderers/HW/GSTextureCache.cpp:8919`. `HashCacheEntry` carries
  `is_replacement` (`GSTextureCache.h:130`), so upscaled entries stay
  distinguishable from native ones.
- **Async executor.** `GSTextureReplacements::QueueWorkerThreadItem(fn, high_priority)`
  (`GSTextureReplacements.cpp:126`) — an existing priority queue with a live worker.
- **Cache lifecycle.** `refcount` and `age` on `HashCacheEntry`.
- **GPU gating.** `MobileGpuArchitecture::Adreno7xx` (`GSGPUProfile.h:43`), already
  used to gate LSFG.

The feature is: hook the hash-cache miss, queue, inject.

## Target Hardware

| Model | SoC | GPU |
| --- | --- | --- |
| Base / Pro / Max | Snapdragon 8 Gen 2 | Adreno 740 |
| Lite | Snapdragon 865 | Adreno 650 |

The 8 Gen 2 Hexagon tensor accelerator is rated **4.35 TOPS** (up from 2.45 on Gen
1), with INT4 support. Anything added must degrade cleanly on the 865 Lite and be
gated on the existing Adreno detection rather than assuming 8 Gen 2.

### GPU compute vs Hexagon NPU

Counter-intuitive conclusion: **the NPU is probably the wrong target** for the
neural algorithms, despite being faster silicon on paper.

- The texture is **already in GPU memory**. Hexagon means GPU -> system memory ->
  DSP -> back; for a 256x256 texture that round trip plausibly dominates the
  convolution itself.
- Hexagon needs QNN or SNPE — proprietary, per-SoC runtime binaries, a real
  packaging burden for a sideloaded app.
- **NNAPI is deprecated as of Android 15.** The generic path is gone; replacements
  are LiteRT or ONNX Runtime with the QNN execution provider.
- A Vulkan compute shader stays on-GPU, ships as SPIR-V, and reuses the backend
  this fork already targets.

So: **Vulkan compute first.** This is an argument from data locality and
packaging, not from benchmarks — it should be measured, not trusted.

Adreno 7xx is expected to expose `VK_KHR_shader_float16_int8` for FP16 math. Not
confirmed against Qualcomm documentation; probe at runtime and fall back to FP32.

## Known Hard Problems

In rough order of how likely each is to sink the feature:

- **The upscale is currently synchronous.** `LookupHashCache` scales the texture inline
  before returning it, so the cost lands on the GS thread at upload time. The design always
  said "queue it, run on a worker, inject when ready" and that is *not* what got built. It
  is tolerable for Scale2x; it is not tolerable for Lanczos-3 (36 taps per output pixel) and
  it is a non-starter for anything neural. `QueueWorkerThreadItem` already exists - this is
  wiring, not invention, but it has to land before the heavier filters are usable.
- **Toggling on mid-game looks broken.** Settings apply live via `applyGSSettingsLive()`,
  but already-cached textures stay native until they are evicted and re-uploaded, so the
  screen barely changes at first. A user will read that as "it does nothing". Either say so
  in the UI or flush the hash cache when the setting changes.
- **VRAM blowup.** 4x on a 256x256 RGBA texture is 256KB -> 4MB. A few thousand
  unique textures will not fit. The budget and eviction policy above are mandatory,
  not polish.
- **Per-frame hash churn.** Animated textures re-hash constantly — see
  [PCSX2 #11792](https://github.com/PCSX2/pcsx2/issues/11792), where Silent Hill 2's
  fog dumps thousands of files in seconds. Needs a stability heuristic: only
  upscale a hash seen N times or across M frames.
- **Palette textures.** PSMT8/PSMT4 are indexed. Upscaling indices is meaningless;
  the texture must resolve through its CLUT first, which interacts with the
  separate `CLUTHash`.
- **Alpha.** `InjectHashCacheTexture` takes `alpha_minmax` and the HW renderer makes
  real decisions from it. An upscaler that shifts alpha range breaks those.
- **Pack precedence.** Pack wins over upscaler. A replacement found synchronously
  returns from `LookupHashCache` before the upscaler gate, but one still loading only
  sets `replacement_texture_pending`, and the async loader and the upscale worker both
  land through `InjectHashCacheTexture` with last-writer-wins. The gate therefore also
  skips on `replacement_texture_pending`. Cost: a pack texture that then fails to load
  (device without ASTC) stays native rather than upscaled, which is the right side to
  err on.
- **Filtering interaction.** Upscaled textures change how `TriFilter` and the
  bilinear hacks behave. Expect per-game regressions.

## Implementation Map

Traced against the tree on 2026-08-21. More of this exists than expected.

**Status.** Config plumbing, `GSTextureUpscaler`, the hash-cache hook, budget/rate-limit/
decline accounting and eviction bookkeeping are in and building. Implemented scalers:
Bilinear, Scale2x, Eagle. Everything else in the enum declines and leaves the texture
native.

Settings UI is `ui/common/TextureUpscaleSection.kt`, mounted as its own collapsible section
on the renderer tab — deliberately not grouped with the present-time filters, since that is
exactly the confusion to avoid. Per-class enable, algorithm and scale, plus a shared VRAM
budget slider. The picker exposes only filters that have a kernel; offering the rest would
be a menu of no-ops. Fields are registered in `SettingsResetFields.kt` so Reset covers them.

Also mounted in the in-game pause menu (`EmulationMenuScreen.kt`), in the graphics pane
next to the shader chain. Being able to change scale and algorithm *while* a game runs is
what makes filters comparable at all - bouncing out to All Settings and back between every
comparison is how you fail to compare them.

### Native — where the work goes

- **Config.** `GSTextureUpscaleAlgorithm` and the seven `TextureUpscale*` fields are in
  `pcsx2/Config.h`, serialized in `pcsx2/Pcsx2Config.cpp` (`OpEqu` block plus
  `SettingsWrapIntEnumEx` / `SettingsWrapBitfieldEx`). Done.
- **Hook point.** `GSTextureCache::LookupHashCache`, after the "expand/upload texture"
  block near `GSTextureCache.cpp:7290`, where `CreateTexture` + `PreloadTexture` produce
  the native texture and it is inserted as
  `const HashCacheEntry entry{tex, 1u, 0u, alpha_minmax, compute_alpha_minmax, false};`
- **Replacement path is the template.** The `if (replace)` branch just above
  (`GSTextureCache.cpp:7245`) already builds a `HashCacheEntry` from a foreign texture and
  accounts for it. An upscaled texture is the same shape.
- **Memory accounting already exists.** `m_hash_cache_memory_usage` and
  `m_hash_cache_replacement_memory_usage` are maintained on both insert and
  `RemoveFromHashCache`. The VRAM budget extends these rather than adding a parallel
  counter.
- **Batch eviction already exists.** `AgeHashCache()` (`GSTextureCache.cpp:7349`) runs
  `MAX_HASH_CACHE_SIZE = 800` / `MAX_HASH_CACHE_AGE = 30` and purges through
  `s_hash_cache_purge_list` in a batch, not one entry at a time. The low-water-mark
  behavior the design calls for is largely there; it evicts by count and needs to also
  evict by bytes.

### Android — where the UI goes

The fork's established pattern is one section in `ui/common/`, mounted from two call sites
so a single definition serves both All Settings and the pause menu, with the caller wiring
its own settings tier:

- Define `TextureUpscaleSection.kt` in
  `platforms/android/app/src/main/java/com/armsx2/ui/common/`, following `LsfgSection.kt`.
- Mount from `ui/settings/RendererTab.kt` and `ui/emulation/EmulationMenuScreen.kt`.
- Register the new fields in `ui/settingshub/SettingsResetFields.kt` so Reset covers them.
- Two sections, not one — world and UI classes are independent everywhere, config keys
  included.

## Suggested Order Of Work

1. **Scaffold with one cheap scaler.** Hash-cache hook, stability heuristic, VRAM
   budget with batch eviction, async queue, injection, and the two-class split.
   The scaffold is the hard part; the scaler is swappable.
2. **Fill in the pixel-art and resample families.** Pure CPU/shader work, no
   models, immediate visible payoff.
3. **Add the neural family last**, as Vulkan compute. By then everything risky is
   solved and measurable.

Do not start at step 3.

Automated A/B comparison across twenty algorithms is impractical by hand — see
[mcp-server.md](mcp-server.md), which exists largely to make this measurable.

## Sources

- [SESR / Collapsible Linear Blocks](https://arxiv.org/abs/2103.09404)
- [FSRCNN](https://arxiv.org/pdf/1608.00367)
- [Anime4K](https://github.com/bloc97/Anime4K)
- [NTIRE 2025 Efficient SR Challenge](https://arxiv.org/pdf/2504.10686)
- [Android NNAPI migration guide](https://developer.android.com/ndk/guides/neuralnetworks/migration-guide)
- [Snapdragon 8 Gen 2 deep dive](https://www.androidauthority.com/snapdragon-8-gen-2-explained-3231826/)
- [AYN Thor specifications](https://www.tldevtech.com/gadget/android-handheld/ayn-thor)
- [PCSX2 #11792 — hash-changing textures](https://github.com/PCSX2/pcsx2/issues/11792)
