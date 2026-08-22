# Third-Party Code

Attribution for algorithms ported into this fork.

## Anime4K — MIT

The `Anime4K` texture filter in `pcsx2/GS/Renderers/HW/GSTextureUpscaler.cpp` is a port of
[Anime4K](https://github.com/bloc97/Anime4K) (MIT, Copyright (c) 2019 bloc97), by way of the
GLSL texture filter in [Citra](https://github.com/citra-emu/citra) / Azahar
(`texture_filtering/refine.frag`, `x_gradient.frag`, `y_gradient.frag`), which carries the
same MIT notice for the Anime4K-derived parts.

### Which Anime4K this is, and why that matters

This is **Anime4K v1 — the "push/gradient" algorithm, not a neural network.** It computes a
separable Sobel edge-strength map, then blends each pixel toward whichever side has a lighter
ridge, straightening staircased diagonals.

That distinction is the whole reason it can ship at all:

- **v1 is an algorithm.** No trained weights, nothing to distribute, nothing to convert. It
  is source code, and MIT source code at that.
- **The later Anime4K CNN modes are networks.** Their weights live inside multi-pass mpv-hook
  GLSL — 63 `mat4` constants across 16 HOOK/BIND/SAVE passes for the small x2 model alone.
  Converting those to this fork's `.a2nn` format is a genuine port rather than an extraction,
  and they would very likely exceed the 4000 MACs/pixel CPU ceiling described in
  [neural-models.md](neural-models.md).

Citra reached the same split independently: its *texture filter* is the v1 gradient/push
algorithm, while its Anime4K CNN lives in `host_shaders/present_anime4k.frag` as a
**present-time** shader. Texture-time gets the cheap deterministic algorithm; the networks
run over the whole frame on the GPU.

### Anime4K at present-time, already available

For the CNN-style Anime4K over the whole screen, nothing needs porting: the RetroArch
slang-shader pack the in-app **Download** button already pulls from ships
`sharpen/Anime4k.slangp`. Enable the shader chain and pick it.

The two are complementary, not alternatives — one sharpens each texture as it is uploaded and
is cached, the other filters the finished frame every frame.

## MMPX and xBRZ — GPLv2-or-later

Both are ports of the GLSL texture filters in [Citra](https://github.com/citra-emu/citra) /
Azahar (`texture_filtering/mmpx.frag`, `texture_filtering/xbrz_freescale.frag`), Copyright
2023 Citra Emulator Project, licensed GPLv2-or-later.

Upstream of those: MMPX is McGuire & Barr-Brisebois'
[style-preserving pixel-art magnification](https://casual-effects.com/research/McGuire2021PixelArt/McGuire2021PixelArt.pdf),
and xBRZ is Zenju's refinement of Hyllian's xBR.

Two notes on fidelity, since both were ported rather than reinvented:

- **MMPX**: Citra's shader has `P` and `S` both at offset `(0, 2)`, which differs from the
  paper. Reproduced as-is. That is the behaviour Azahar ships and what people have actually
  compared against; quietly correcting it would make this filter differ from the reference it
  claims to be.
- **xBRZ**: this is the *free-scale* variant, which decides a blend per output pixel from
  where that pixel sits inside its source texel. That is why it is dispatched like a
  resampler rather than as a doubling pass, and why 4x is one pass instead of 2x twice.

## ScaleForce — MIT

Ported from Citra/Azahar's `texture_filtering/scale_force.frag` (MIT).

**One deliberate deviation.** The reference computes its colour distance as the *sum* of the
YCbCr components:

```glsl
vec4 color_dist = vec3(1.0) * YCbCr;   // sum of each column
```

The chroma rows of a YCbCr matrix sum to zero by construction, so that expression collapses
to `0.6 * (red difference)` and discards green and blue entirely — a green-on-blue edge
measures as zero distance. Checked numerically against the shader's own constants:

```
col0 (Y)  sum =  0.600000
col1 (Cb) sum =  0.000000
col2 (Cr) sum = -0.000000
```

This port uses the *length* of the YCbCr vector instead, which is plainly what the
surrounding code means and what xBRZ's `ColorDist` in the same codebase already does.

This is the opposite call to the MMPX one above, on purpose. MMPX's difference is a tap
offset with a modest visual effect, so matching the reference mattered more than being
"right". Here the metric is degenerate across two of three colour channels, and shipping
that under the ScaleForce name would mean handing over a filter that ignores most colour
edges.

## Deposterize — GPLv2-or-later

Ported from [PPSSPP](https://github.com/hrydgard/ppsspp)'s
`GPU/Common/TextureScalerCommon.cpp` (`deposterizeH` / `deposterizeV`), Copyright 2012-
PPSSPP Project, GPLv2-or-later.

A pre-pass rather than a filter. It only interpolates where the centre already equals one
neighbour and the other is within 8/255 of it — the signature of a quantisation step rather
than a real two-colour boundary — which is what keeps it from being a blur.

Run as H, V, H, V (two full separable passes), matching PPSSPP. A single pass leaves diagonal
banding visibly untouched.

Particularly apt here: PS2 stores a great deal of texture data as PSMCT16, which is 5:5:5:1,
so posterised gradients are the norm rather than an occasional artefact — and a good scaler
otherwise preserves the banding faithfully and then makes it larger.

## Licence compatibility

ARMSX2 is GPLv3. MIT is compatible with it, and Citra's and PPSSPP's code is
GPLv2-or-later, which is also compatible. The MIT notice above is reproduced because MIT requires it.
