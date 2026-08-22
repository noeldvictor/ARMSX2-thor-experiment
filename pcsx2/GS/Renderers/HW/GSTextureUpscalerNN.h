// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "Config.h"

#include "common/Pcsx2Defs.h"

/// Neural super-resolution for the texture upscaler.
///
/// Runs a small convolutional network over one texture, on the upscaler's worker thread.
/// This is only viable because the work is texture-time rather than per-frame: a texture is
/// scaled once and then cached, so a network that would be hopeless at 60fps over a whole
/// frame is affordable here. See docs/texture-upscaling-research.md.
///
/// ## Weights are not shipped
///
/// ARMSX2 contains the architecture, not a trained model. Networks are loaded from
/// `<Textures>/models/<name>_x<scale>.a2nn`, and every neural algorithm declines - leaving
/// the texture at native resolution - until the matching file exists. Shipping someone
/// else's trained weights is a licensing question, not a technical one, so the format is
/// documented and the file is left to the user.
///
/// ## CPU, not NPU, and why
///
/// The Thor's Hexagon NPU is faster silicon, but the texture is in a CPU buffer at this
/// point and round-tripping it through QNN costs more than the convolution for the small
/// textures involved. See the design doc for the full argument. If that turns out to be
/// wrong, this is the seam to replace.
namespace GSTextureUpscalerNN
{
	/// Whether a usable model exists for this algorithm and scale. Loads and caches it on
	/// first call, so a missing or malformed file costs one attempt rather than one per
	/// texture.
	bool IsAvailable(GSTextureUpscaleAlgorithm algorithm, u8 scale);

	/// Run the network. `src`/`dst` are packed RGBA8, strides in pixels.
	///
	/// Returns false when no model is available or the texture is outside the size the model
	/// will be run on, in which case the caller must fall back to leaving it native.
	bool Run(GSTextureUpscaleAlgorithm algorithm, const u32* src, int sw, int sh, u32 src_stride,
		u32* dst, u32 dst_stride, u8 scale);

	/// Drop cached models. Called when the texture folder or game changes.
	void Reset();
} // namespace GSTextureUpscalerNN
