// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "pcsx2/GS/Renderers/HW/GSTextureUpscaler.h"

/// RAISR-HD: learned upscaling kernels, fit offline on HD texture packs by
/// tools/raisr_train.py. Not a neural network. Every input pixel is hashed by the angle,
/// strength and coherence of its local gradient into one of a few hundred buckets, and
/// each bucket owns one small kernel per output phase. Runtime cost is one hash and one
/// k*k convolution per output pixel - an order of magnitude under the neural path's
/// MACs/pixel ceiling - so it lives on the same CPU worker thread as the classic filters.
///
/// Kernel files (`.a2rk`) are bundled in the APK under resources/upscale and loaded per
/// texture class and scale: world_x2, world_x4, ui_x2, ui_x4. The reference implementation
/// the C++ must match bit-for-bit in structure (hashing, phase order, patch layout) is the
/// numpy `raisr_upscale` in tools/raisr_train.py.
namespace GSTextureUpscalerRaisr
{
	/// Scale `src` (sw x sh RGBA8, `src_stride` in pixels) by `scale` into `dst` using the kernel
	/// set for `texture_class`. Returns false when no usable kernel file exists for that
	/// class and scale; the caller leaves the texture native.
	bool Run(GSTextureUpscaler::TextureClass texture_class, const u32* src, int sw, int sh, u32 src_stride,
		u32* dst, u32 dst_stride, u8 scale);

	/// Drop every loaded kernel set so the next Run re-reads from disk. Called when the
	/// resources folder may have changed (game switch, settings reset).
	void Reset();

	/// Runs x2 on a fixed synthetic texture and logs dimensions plus a checksum, so "the
	/// kernels loaded and the filter ran" is answerable from the log without a game.
	void RunSelfTestOnce();
} // namespace GSTextureUpscalerRaisr
