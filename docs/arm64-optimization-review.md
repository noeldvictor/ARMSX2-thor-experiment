# ARM64 Optimization Review — AYN Thor

Reviewed 2026-08-21. Findings are grouped by confidence: what was verified in the
tree, and what was not audited. Nothing here has been benchmarked on device — that
is the honest limit of this pass.

Reference manuals for the Thor's exact cores are in
[docs/reference/arm/](reference/arm/README.md).

## The target is not one CPU

The Snapdragon 8 Gen 2 is a 1+4+3 heterogeneous complex:

| Cluster | Core | Count |
| --- | --- | --- |
| Prime | Cortex-X3 | 1 |
| Performance | Cortex-A715 | 2 |
| Performance | Cortex-A710 | 2 |
| Efficiency | Cortex-A510 | 3 |

These cores do not share a pipeline model. Instruction latency, issue width and
NEON throughput differ enough that "faster on the X3" and "faster on the A510" are
separate questions, and the emulator's threads land on different clusters. Any
micro-optimization claim needs to say which core it was measured on.

**The Thor Lite is a Snapdragon 865** — Cortex-A77/A55, ARMv8.2 but a different
core generation entirely. It matches none of the guides above.

## Finding 1 — local debug builds use different codegen than releases

**Verified. Cheap to fix. Probably the highest value item here.**

`platforms/android/tools/build-release-targets.sh` tiers releases properly:

| Target | march |
| --- | --- |
| legacy sdk26 | `armv8-a` + outline atomics |
| a11 / a13 / a15 | `armv8.2-a+fp16+dotprod` |

But `armsx2.march` is **not set in `platforms/android/gradle.properties`**, so a
plain `./gradlew :app:assembleGithubDebug` — the dev-loop build, and the one that
has been going onto the Thor — falls through to `BuildParameters.cmake`'s
`-march=armv8.1-a` default.

Consequences:

- FEAT_FP16 and FEAT_DotProd are optional at ARMv8.2 and must be named explicitly
  (the release script says exactly this). At `armv8.1-a` they are simply absent, so
  the Thor's half-precision and dot-product instructions never get emitted locally.
- Local performance testing measures codegen that no released APK uses. Any timing
  taken from a debug build is not a measurement of the shipped binary.

Fix is one line — set the default in `gradle.properties`:

```properties
armsx2.march=armv8.2-a+fp16+dotprod
```

The release script passes `-Parmsx2.march=` explicitly, which overrides a property
default, so the legacy `armv8-a` tier is unaffected. Verify that before relying on
it, and note this makes debug APKs unrunnable on pre-v8.2 devices — correct for a
Thor-targeted fork, wrong if you hand a debug build to someone with an older phone.

## Finding 2 — the texture upscaler kernels are scalar

**Verified — it is code added in this fork today.**

`pcsx2/GS/Renderers/HW/GSTextureUpscaler.cpp` processes one pixel at a time.
`ScaleBilinear` is the worst of the three: a per-pixel loop over four channels, each
doing two multiplies and shifts, with no vectorization at all.

This is a good NEON candidate and an unusually safe one:

- The work is embarrassingly parallel across pixels.
- The codebase already has the right abstraction — `GSVector4i` is NEON-backed on
  ARM64 via `pcsx2/GS/GSVector4i_arm64.h`, so this does not mean hand-writing
  intrinsics or an ISA fork.
- It runs on the GS thread during texture upload, so it is directly in the path of
  the stutter this feature can cause.

Bilinear in particular maps onto widening multiply-accumulate almost directly.
Scale2x and Eagle are comparison-and-select, which vectorizes too but with less
obvious payoff since they are already cheap.

Worth doing **after** the kernels are known-correct and something has measured that
they actually cost enough to matter. Vectorizing first would be optimizing a path
nobody has shown is hot.

## Already vectorized — do not redo these

Surveyed by searching for `arm_neon.h`, `vld1q`, `vaddq_`, `__aarch64__`:

| Area | Files |
| --- | --- |
| GS vector types | `GS/GSVector4i_arm64.h`, `GS/GSVector4_arm64.h`, `GS/GSVertexKick.h` |
| SPU2 | `SPU2/spu2_neon.cpp`, `SPU2/Mixer.cpp`, `SPU2/defs.h` |
| IPU | `IPU/IPUdither.cpp`, `IPU/IPU_MultiISA.cpp`, `IPU/yuv2rgb.cpp` |
| microVU | `arm64/microVU-arm64.h`, `microVU_Divtrace.cpp` |
| Common | `common/VectorIntrin.h`, `common/SingleRegisterTypes.h` |

The GS swizzle/unswizzle paths in `GSLocalMemory` are built on `GSVector4i`, so they
are already NEON-backed by construction rather than by explicit intrinsics.

There are also existing runtime toggles implying prior NEON work with known
trade-offs — `vuNeonFusions`, `spu2NeonReverb`. Treat those as evidence someone
already went down this road and found it needed a switch.

## Not audited

Stated plainly so this document is not mistaken for a full pass:

- No profiling was run. Nothing here identifies a *measured* hot path.
- The JIT backends (`arm64/`, microVU, vixl usage) were not reviewed for
  instruction selection.
- Memory ordering / atomics were not reviewed beyond noting the outline-atomics
  handling in the legacy release tier.
- No comparison of A510 vs X3 behaviour for anything.

## Suggested order

1. Set the `gradle.properties` march default so local builds match shipped codegen.
   Everything else is unmeasurable until this is true.
2. Profile on device and find out what is actually hot. The Thor has a
   `simpleperf`-capable NDK.
3. Only then vectorize, starting with whatever profiling says — which may well not
   be the upscaler.
