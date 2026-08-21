# Real-Time Texture And Sprite Enhancement On AYN Thor

Research notes, not a roadmap. Nothing here is implemented. This exists so the
next person (or the next AI session) does not re-derive the same constraints.

Last researched: 2026-08-21.

## The Reframe

The obvious idea is "upscale the frame with a neural net." That is the wrong axis
for this fork, for two reasons:

1. **Present-time is already covered here.** `GSUpscaler` (`pcsx2/Config.h:478`)
   ships FSR1 as EASU + RCAS Vulkan compute passes, a librashader `.slangp` chain
   runs at present after ShadeBoost/FXAA (`pcsx2/Config.h:1053`), and LSFG frame
   generation is wired into the Vulkan present path (`pcsx2/Config.h:1060`).
2. **Per-frame NN inference is a thermal non-starter on a handheld.** It is a
   sustained load on a GPU already busy presenting the game, every frame, forever.

The unexploited axis is **texture-time**. PS2 textures are uploaded, hashed, and
cached. Upscaling happens **once per unique texture**, then the result lives in
the hash cache. Steady-state cost returns to zero.

That single property is what makes a neural net affordable here and unaffordable
at present-time.

### Why texture-time is cheap

| | Present-time | Texture-time |
| --- | --- | --- |
| Work unit | 2.07M px (1080p) | Usually <= 256x256 = 65K px |
| Frequency | Every frame, 60x/sec | Once per unique texture |
| Steady state | Constant load | Zero |
| Thermal shape | Sustained | Bursty, at scene loads |
| Latency budget | <16ms hard | Async, hidden |

Textures also do not need to be finished *this frame*. They can be queued, run on
a worker, and injected when ready — the game draws the original in the meantime.

## The Hooks Already Exist

This is the part that makes it tractable. None of the following needs inventing:

- **Injection point.** `GSTextureCache::InjectHashCacheTexture(key, tex, alpha_minmax)`
  at `pcsx2/GS/Renderers/HW/GSTextureCache.cpp:8919`. `HashCacheEntry` carries an
  `is_replacement` flag (`GSTextureCache.h:130`), so an upscaled entry is
  distinguishable from a native one.
- **Async executor.** `GSTextureReplacements::QueueWorkerThreadItem(fn, high_priority)`
  (`GSTextureReplacements.cpp:126`) is an existing priority work queue with a
  running worker thread.
- **Cache lifecycle.** `HashCacheEntry` already has `refcount` and `age`, so
  eviction policy exists.
- **GPU gating.** `MobileGpuArchitecture::Adreno7xx` (`GSGPUProfile.h:43`) already
  distinguishes Adreno generations; LSFG uses this to gate itself to 7xx+.

A texture-time upscaler is: hook the hash-cache miss, queue the work, inject the
result. The plumbing is present.

## Target Hardware

AYN Thor ships in two tiers, and they are not close:

| Model | SoC | GPU |
| --- | --- | --- |
| Base / Pro / Max | Snapdragon 8 Gen 2 | Adreno 740 |
| Lite | Snapdragon 865 | Adreno 650 |

The Snapdragon 8 Gen 2 Hexagon has a dedicated tensor accelerator rated at
**4.35 TOPS** (up from 2.45 on Gen 1), with INT4 support.

Anything added here must degrade cleanly on the 865 Lite, and must be gated on
the existing Adreno architecture detection rather than assuming 8 Gen 2.

## GPU Compute vs Hexagon NPU

Counter-intuitive conclusion: **the NPU is probably the wrong target**, despite
being the faster silicon on paper.

- The texture is **already in GPU memory**. Routing to Hexagon means
  GPU -> system memory -> DSP -> back. For a 256x256 texture the round-trip
  latency plausibly dominates the actual convolution.
- Hexagon requires QNN or SNPE — proprietary, per-SoC runtime binaries, a real
  packaging burden for a sideloaded Android app.
- **NNAPI is deprecated as of Android 15.** The generic path is gone; the
  replacements are LiteRT or ONNX Runtime with the QNN execution provider.
- A Vulkan compute shader stays on-GPU, ships as SPIR-V with no extra runtime,
  and reuses the backend this fork already targets.

So: **Vulkan compute first**. Treat the NPU as a later experiment, and only if
measurement contradicts the above. This ordering is an argument from data
locality and packaging, not from benchmarks — it should be measured, not trusted.

Adreno 7xx is expected to expose `VK_KHR_shader_float16_int8` for FP16 math.
This was not confirmed against Qualcomm documentation during this research —
probe it at runtime and fall back to FP32 rather than assuming.

## Candidate Approaches, Cheapest First

### Tier 0 — Anime4K through the existing slang chain (no code)

The librashader `.slangp` chain at `Config.h:1053` can already run
[Anime4K](https://github.com/bloc97/Anime4K) today. Anime4K is a CNN expressed
as GLSL shaders specifically so it can run real-time without a CUDA runtime.

PS2-era art — hand-painted textures, flat shading, strong line work, low colour
counts — sits close to Anime4K's design target. This is present-time, not
texture-time, so it carries the per-frame cost; but it needs zero native work,
which makes it the correct first experiment purely to establish whether the
*visual* result is even worth pursuing.

Cost: ship presets, add a UI entry. Answers "does this look good" before anyone
writes a compute shader.

### Tier 1 — Classic pixel scalers at texture-time (cheap, deterministic)

xBRZ, xBR, HQx, Scale2x/Eagle. No model, no weights, no inference runtime, fully
deterministic, trivially cheap.

These are the **right answer for sprites, 2D layers, and UI** — which is half of
what this is for. They are designed for exactly the low-colour indexed art that
PSMT8/PSMT4 textures carry, and PS2 leans hard on palettised formats because of
its 4MB VRAM budget.

Lowest risk, highest certainty of shipping. Should probably be built first even
though it is less exciting than the NN path.

### Tier 2 — Tiny CNN super-resolution at texture-time (the actual prize)

Run a small SR network as a Vulkan compute shader, inject via
`InjectHashCacheTexture`. Candidate architectures:

- **SESR** (Arm, *Collapsible Linear Blocks for Super-Efficient Super Resolution*,
  [arXiv:2103.09404](https://arxiv.org/abs/2103.09404)). Linear overparameterisation
  that collapses at inference. Reported 2x–330x fewer MACs than comparable
  models, 6x–8x higher FPS on mobile NPU, 1.5x–2x lower latency than FSRCNN on
  Arm CPU/GPU. Explicitly designed for this class of hardware. Best starting
  point.
- **FSRCNN / FSRCNNX** ([arXiv:1608.00367](https://arxiv.org/pdf/1608.00367)) —
  the classic real-time-capable SR CNN; FSRCNNX has existing shader ports.
- **ESPCN** — sub-pixel convolution, even cheaper, weaker.

Because this is amortised per-texture rather than per-frame, the model can be
meaningfully larger than anything viable at present-time. This is the freedom the
texture-time reframe buys.

### Not viable real-time

Real-ESRGAN, waifu2x, RealCUGAN and that weight class are offline tools. They
belong in the **static pack pipeline**, not the runtime.

## Relationship To The Texture Pack Idea

Runtime NN upscaling and offline HD texture packs are **the same subsystem**.
Both terminate at `InjectHashCacheTexture`. The natural architecture is a
fallback chain per texture:

1. Author-made HD replacement, if the pack has this hash
2. Runtime NN upscale, if enabled and the texture qualifies
3. Native texture

Runtime NN is what covers the coverage holes a pack can never fill — the
render-to-texture and runtime-palette cases that no static extraction reaches.
They are complements, not competitors.

## Known Hard Problems

Nothing above is free. In rough order of how likely each is to sink the feature:

- **VRAM blowup.** A 4x upscale of a 256x256 RGBA texture goes 256KB -> 4MB.
  A scene with a few thousand unique textures will not fit. A hard budget with
  eviction is mandatory, not optional. `HashCacheEntry::age` is the lever.
- **Per-frame hash churn.** Animated textures re-hash constantly — see
  [PCSX2 #11792](https://github.com/PCSX2/pcsx2/issues/11792), where Silent
  Hill 2's fog dumps thousands of files in seconds. Upscaling those every frame
  is unbounded work for zero benefit. Needs a stability heuristic: only upscale
  a hash seen N times, or seen across M frames.
- **Palette textures.** PSMT8/PSMT4 are indexed. Upscaling indices is meaningless;
  the texture must be resolved through its CLUT first, which changes what gets
  cached and interacts with the separate `CLUTHash`.
- **Alpha.** `InjectHashCacheTexture` takes `alpha_minmax`, and the HW renderer
  makes real decisions off it. An upscaler that alters alpha range breaks those.
- **Filtering interaction.** Upscaled textures change how `TriFilter` and the
  bilinear hacks behave. Expect per-game regressions.
- **UI and text.** Anything crisp and 2D usually looks *worse* through an SR
  network than through xBRZ or untouched. Needs an opt-out.

## Suggested Order Of Work

1. Ship Anime4K presets on the existing slang chain. Zero native code. Decides
   whether the visual payoff justifies anything below.
2. Build the texture-time scaffold with a trivial scaler (bilinear or xBRZ):
   hash-cache hook, stability heuristic, VRAM budget, async queue, injection.
   The scaffold is the hard part; the scaler is swappable.
3. Only then swap in a Vulkan compute SR net. By that point everything risky is
   already solved and measurable.

Do not start at step 3.

## Sources

- [SESR / Collapsible Linear Blocks](https://arxiv.org/abs/2103.09404)
- [FSRCNN](https://arxiv.org/pdf/1608.00367)
- [Anime4K](https://github.com/bloc97/Anime4K)
- [NTIRE 2025 Efficient SR Challenge](https://arxiv.org/pdf/2504.10686)
- [Android NNAPI migration guide](https://developer.android.com/ndk/guides/neuralnetworks/migration-guide)
- [Snapdragon 8 Gen 2 deep dive](https://www.androidauthority.com/snapdragon-8-gen-2-explained-3231826/)
- [AYN Thor specifications](https://www.tldevtech.com/gadget/android-handheld/ayn-thor)
- [PCSX2 #11792 — hash-changing textures](https://github.com/PCSX2/pcsx2/issues/11792)
