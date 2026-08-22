# Neural Texture Upscaling Models

ARMSX2 contains the network architecture but **no trained weights**. Every neural
algorithm declines — leaving textures at native resolution — until a model file is
installed. Shipping someone else's trained weights is a licensing question rather
than a technical one, so the format is documented and the file is left to you.

## Installing a model

Put the file at:

```
<Textures>/models/<name>_x<scale>.a2nn
```

On the test device that is `/sdcard/armsxdata/textures/models/`. Names are fixed per
algorithm:

| Algorithm in the picker | File |
| --- | --- |
| Anime4K | `anime4k_x2.a2nn`, `anime4k_x4.a2nn` |
| FSRCNN | `fsrcnn_x2.a2nn`, `fsrcnn_x4.a2nn` |
| SESR | `sesr_x2.a2nn`, `sesr_x4.a2nn` |
| ESPCN | `espcn_x2.a2nn`, `espcn_x4.a2nn` |

A missing file is not an error — it is the normal state. A *malformed* file logs why
and the `nomodel` counter on the GS stats OSD goes up.

## Proving the path works before you have weights

`tools/make_a2nn.py --identity` emits a model that reproduces nearest-neighbour
upscaling exactly: a single 1x1 convolution copying RGB into every sub-pixel slot.

```
python tools/make_a2nn.py --identity --scale 2 -o espcn_x2.a2nn
adb push espcn_x2.a2nn /sdcard/armsxdata/textures/models/
```

Nothing about it is neural in any useful sense. The point is that it exercises the
whole path — load, validate, convolve, pixel-shuffle, write back — with an output you
can check by eye. If the result looks like plain nearest-neighbour 2x, the plumbing
is correct and only the weights are missing.

## The self-test

Every session, the first time the texture cache is created, each installed model is run
over a small synthetic 8x8 gradient with a hard edge and the result is logged:

```
Texture upscaling: loaded .../espcn_x2.a2nn (1 layers, 36 MACs/pixel).
Texture upscaling: model self-test espcn x2 OK - 8x8 to 16x16, checksum b2870000. The neural path ran.
```

This runs **whether or not upscaling is switched on**. "Is my model file usable" and "is
the feature enabled" are different questions, and answering the first should not require
getting the second right first — which is exactly the trap that made this hard to verify in
the first place. No model installed means no work and no output.

The checksum is over the whole output in row-major order (`checksum = checksum * 31 + pixel`,
32-bit wrapping). For the `--identity` model at x2 it is deterministic: **`b2870000`**. Any
other value means the convolution or the pixel shuffle is wrong, not merely that something
ran.

## Converting a real model

```
python tools/make_a2nn.py --from-torch model.pth --scale 2 -o sesr_x2.a2nn
```

The converter takes 4D conv weights in checkpoint order, pairs each with its bias,
assumes ReLU between layers and a linear final layer. Anything more exotic — residual
connections, depthwise convolutions, the collapsed form of a SESR block — needs
converting to a plain feed-forward stack of convolutions first. SESR is specifically
designed to collapse to that at inference time, which is part of why it is a good fit.

## Format

Little-endian throughout.

```
char  magic[4]      "A2NN"
u32   version       1
u32   scale         2 or 4
u32   layers
  per layer:
    u32  in_channels
    u32  out_channels
    u32  kernel        odd, 1..7
    u32  activation    0 = none, 1 = ReLU
    f32  weights[out][in][ky][kx]
    f32  biases[out]
```

Rules the loader enforces, each of which rejects the file with a log line:

- The first layer takes **3 channels** (RGB, 0..1), and each layer's `in_channels`
  must equal the previous layer's `out_channels`.
- The final layer emits **`scale * scale * 3`** channels, pixel-shuffled into the
  output in the standard sub-pixel order.
- Total cost must stay under **4000 MACs/pixel**.

## Why the MAC ceiling exists

This runs on the CPU, on the upscaler's worker thread. A 256x256 texture is ~65k
pixels, so 4000 MACs/pixel is around 260 MMAC per texture — a fraction of a second on
one core, which the async path hides.

A full-size ESPCN with 64 and 32 channel hidden layers is roughly 26,000 MACs/pixel,
about 1.75 GMAC for the same texture. That does not hide: textures would pop in
minutes after the scene they belong to, which is worse than not upscaling at all. Keep
hidden layers small — 8 and 4 channels is the right order of magnitude here.

If that ceiling turns out to be the binding constraint, the answer is moving inference
to Vulkan compute rather than raising it. See the GPU-vs-NPU argument in
[texture-upscaling-research.md](texture-upscaling-research.md); FSR1's existing compute
passes in `GSDeviceVK` are the template.

## Alpha

Carried through from the source, not predicted. These networks are trained on RGB, and
asking one to invent alpha produces soft edges on exactly the cutout textures where a
hard edge matters most.
