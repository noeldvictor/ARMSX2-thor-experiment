#!/usr/bin/env python3
"""Build or convert an `.a2nn` model for ARMSX2's neural texture upscaler.

ARMSX2 ships the network architecture but no trained weights, so a neural filter does
nothing until a model exists at `<textures>/models/<name>_x<scale>.a2nn`.

Two modes:

  --identity   Emit a model that provably reproduces nearest-neighbour upscaling. Nothing
               about it is "neural" in a useful sense - the point is that it exercises the
               entire path (load, validate, convolve, pixel-shuffle, write back) with an
               output you can verify by eye. If this looks like plain nearest-neighbour 2x,
               the plumbing is correct and only the weights are missing.

  --from-torch Convert a PyTorch checkpoint whose conv layers are in forward order.

Usage:
    python tools/make_a2nn.py --identity --scale 2 -o espcn_x2.a2nn
    python tools/make_a2nn.py --from-torch model.pth --scale 2 -o sesr_x2.a2nn

Then copy the result to <textures>/models/ on the device and pick the matching neural
algorithm in the texture upscaling settings.
"""

from __future__ import annotations

import argparse
import struct
import sys

MAGIC = b"A2NN"
VERSION = 1

# Mirrors MAX_MACS_PER_PIXEL in GSTextureUpscalerNN.cpp. Kept here so the tool refuses to
# emit a model the emulator will only reject later.
MAX_MACS_PER_PIXEL = 4000

ACT_NONE = 0
ACT_RELU = 1


class Layer:
    def __init__(self, in_ch: int, out_ch: int, kernel: int, activation: int, weights, biases):
        self.in_ch = in_ch
        self.out_ch = out_ch
        self.kernel = kernel
        self.activation = activation
        self.weights = weights  # flat, [out][in][ky][kx]
        self.biases = biases

    @property
    def macs(self) -> int:
        return self.in_ch * self.out_ch * self.kernel * self.kernel


def write_model(path: str, scale: int, layers: list[Layer]) -> None:
    expected_in = 3
    for index, layer in enumerate(layers):
        if layer.in_ch != expected_in:
            sys.exit(f"layer {index} takes {layer.in_ch} channels, previous produces {expected_in}")
        if len(layer.weights) != layer.out_ch * layer.in_ch * layer.kernel * layer.kernel:
            sys.exit(f"layer {index} weight count does not match its dimensions")
        if len(layer.biases) != layer.out_ch:
            sys.exit(f"layer {index} bias count does not match out_channels")
        expected_in = layer.out_ch

    needed = scale * scale * 3
    if expected_in != needed:
        sys.exit(
            f"final layer emits {expected_in} channels; a x{scale} model must emit {needed} "
            f"for pixel shuffle"
        )

    total_macs = sum(layer.macs for layer in layers)
    if total_macs > MAX_MACS_PER_PIXEL:
        sys.exit(
            f"model needs {total_macs} MACs/pixel, over the {MAX_MACS_PER_PIXEL} limit the "
            f"emulator enforces. Shrink the channel counts."
        )

    with open(path, "wb") as handle:
        handle.write(MAGIC)
        handle.write(struct.pack("<III", VERSION, scale, len(layers)))
        for layer in layers:
            handle.write(
                struct.pack("<IIII", layer.in_ch, layer.out_ch, layer.kernel, layer.activation)
            )
            handle.write(struct.pack(f"<{len(layer.weights)}f", *layer.weights))
            handle.write(struct.pack(f"<{len(layer.biases)}f", *layer.biases))

    print(f"wrote {path}: {len(layers)} layer(s), {total_macs} MACs/pixel, x{scale}")


def build_identity(scale: int) -> list[Layer]:
    """One 1x1 conv mapping RGB to every sub-pixel slot.

    After pixel shuffle each of the scale*scale sub-pixels holds the source colour
    unchanged, which is nearest-neighbour by construction.
    """
    out_ch = scale * scale * 3
    weights = []
    for oc in range(out_ch):
        for ic in range(3):
            weights.append(1.0 if (oc % 3) == ic else 0.0)
    return [Layer(3, out_ch, 1, ACT_NONE, weights, [0.0] * out_ch)]


def build_from_torch(checkpoint: str, scale: int) -> list[Layer]:
    try:
        import torch
    except ImportError:
        sys.exit("--from-torch needs PyTorch installed (pip install torch)")

    state = torch.load(checkpoint, map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    if not isinstance(state, dict):
        sys.exit("checkpoint is not a state dict")

    # Pair each *.weight with its *.bias, in the order they appear. Conv weights are
    # [out, in, kh, kw], which is already the layout the format wants.
    layers: list[Layer] = []
    for key, tensor in state.items():
        if not key.endswith(".weight") or tensor.dim() != 4:
            continue
        bias_key = key[: -len(".weight")] + ".bias"
        out_ch, in_ch, kh, kw = tensor.shape
        if kh != kw:
            sys.exit(f"{key} is {kh}x{kw}; only square kernels are supported")
        biases = state[bias_key].flatten().tolist() if bias_key in state else [0.0] * out_ch
        layers.append(
            Layer(in_ch, out_ch, kh, ACT_RELU, tensor.flatten().tolist(), biases)
        )

    if not layers:
        sys.exit("no 4D conv weights found in the checkpoint")

    # The last layer feeds pixel shuffle, so it must be linear.
    layers[-1].activation = ACT_NONE
    print(f"found {len(layers)} conv layers; assuming forward order and ReLU between them")
    return layers


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--identity", action="store_true", help="emit a nearest-neighbour model")
    source.add_argument("--from-torch", metavar="CHECKPOINT", help="convert a PyTorch checkpoint")
    parser.add_argument("--scale", type=int, choices=(2, 4), default=2)
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    layers = build_identity(args.scale) if args.identity else build_from_torch(args.from_torch, args.scale)
    write_model(args.output, args.scale, layers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
