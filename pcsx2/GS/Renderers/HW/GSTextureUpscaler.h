// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "Config.h"

#include "common/Pcsx2Defs.h"

/// Per-texture upscaling, applied when a texture is uploaded rather than to the presented
/// frame. The result lives in the hash cache, so the cost is once per unique texture and
/// steady state returns to zero — the opposite cost model to GSUpscaler/FSR1, which pay
/// per frame forever. See docs/texture-upscaling-research.md.
///
/// This module is deliberately pure: it decides what to do and scales pixel buffers, but
/// never touches the hash cache. GSTextureCache owns insertion, because the cache map is
/// private to it and inventing a second owner of that lifetime is how it gets corrupted.
namespace GSTextureUpscaler
{
	/// World/3D and UI/2D are independent everywhere — separate enables, separate
	/// algorithms, separate scale factors. A neural model that flatters a painted wall
	/// will mangle a HUD font, so they never share a setting.
	enum class TextureClass : u8
	{
		World,
		Ui,
	};

	struct Plan
	{
		/// 1 means "leave this texture alone". 2 or 4 otherwise.
		u8 scale = 1;
		GSTextureUpscaleAlgorithm algorithm = GSTextureUpscaleAlgorithm::Bilinear;
		TextureClass texture_class = TextureClass::World;
	};

	/// True when either texture class is enabled. Cheap enough to call per texture upload,
	/// and lets the caller skip all of the below without paying for a plan.
	bool IsEnabled();

	/// Decide whether this texture should be upscaled, and how.
	///
	/// Returns a plan with scale == 1 for anything declined: the owning class disabled, an
	/// algorithm not implemented yet, the VRAM budget exhausted, the per-frame rate limit
	/// reached, or a hash previously declined for budget.
	Plan MakePlan(u64 tex0_hash, int tw, int th);

	/// Scale an RGBA8 buffer. Returns false when the algorithm is not implemented, in which
	/// case the caller must fall back to the native texture path.
	///
	/// `scale` must be 2 or 4; 4 is two 2x passes.
	bool ScaleBuffer(GSTextureUpscaleAlgorithm algorithm, const u8* src, int sw, int sh, u32 src_pitch,
		u8* dst, u32 dst_pitch, u8 scale);

	/// Whether this algorithm has an implementation yet. The enum deliberately lists the
	/// whole intended library, so most of it answers false for now.
	bool IsAlgorithmImplemented(GSTextureUpscaleAlgorithm algorithm);

	/// Called by the cache once a plan has actually produced a texture, so the budget knows
	/// what it is holding.
	void NoteUpscaled(u64 tex0_hash, u32 bytes);

	/// Called when an upscaled texture leaves the cache.
	void NoteEvicted(u64 tex0_hash, u32 bytes);

	/// Per-frame bookkeeping: resets the rate limiter.
	void NextFrame();

	/// Drops all accounting. Call when the hash cache is cleared.
	void Reset();

	/// Bytes currently held by upscaled textures.
	u32 GetMemoryUsage();
} // namespace GSTextureUpscaler
