// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "Config.h"

#include "GS/Renderers/HW/GSTextureCache.h"

#include "common/Pcsx2Defs.h"

#include <utility>
#include <vector>

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

	/// Counters for one session, so the feature can be judged by numbers rather than by
	/// squinting at a screenshot. Every decline reason is separate on purpose: "nothing got
	/// upscaled" has half a dozen very different causes and they need different fixes.
	struct Stats
	{
		u32 upscaled = 0;
		u32 evicted = 0;
		u32 held = 0;
		u32 memory_usage = 0;
		/// Skipped before a plan was even made - palette, mipmaps or a source region.
		u32 skipped_guard = 0;
		u32 declined_class_disabled = 0;
		u32 declined_unimplemented = 0;
		u32 declined_rate_limit = 0;
		u32 declined_budget = 0;
		/// A neural algorithm was selected but no usable model file is installed.
		u32 declined_no_model = 0;
	};

	/// A finished upscale, waiting to be turned into a texture on the GS thread.
	struct CompletedUpscale
	{
		GSTextureCache::HashCacheKey key;
		std::vector<u32> pixels;
		int width = 0;
		int height = 0;
		/// The source was sampled with mip levels, so the injected texture needs a chain too.
		bool mipmap = false;
		std::pair<u8, u8> alpha_minmax{0u, 255u};
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
	/// `scale` must be 2 or 4; 4 is two 2x passes for the classic filters. `texture_class`
	/// only matters to RAISR-HD, which carries a kernel set per class.
	bool ScaleBuffer(GSTextureUpscaleAlgorithm algorithm, const u8* src, int sw, int sh, u32 src_pitch,
		u8* dst, u32 dst_pitch, u8 scale, TextureClass texture_class = TextureClass::World);

	/// Whether this algorithm has an implementation yet. The enum deliberately lists the
	/// whole intended library, so most of it answers false for now.
	bool IsAlgorithmImplemented(GSTextureUpscaleAlgorithm algorithm);

	/// Called by the cache once a plan has actually produced a texture, so the budget knows
	/// what it is holding.
	void NoteUpscaled(u64 tex0_hash, u32 bytes);

	/// Called when an upscaled texture leaves the cache.
	void NoteEvicted(u64 tex0_hash, u32 bytes);

	/// Called when a texture never reached MakePlan because of the caller's guards.
	void NoteGuardSkipped();

	/// Hand a texture to the worker thread to be scaled. Copies the source pixels, so the
	/// caller's buffer can be reused the moment this returns.
	///
	/// The caller does NOT wait: it carries on and creates the native-resolution texture, and
	/// the upscaled one replaces it a frame or two later. That is the whole point - scaling
	/// inline puts Lanczos on the GS thread at upload time, which is exactly the hitch this
	/// feature must not cause.
	void QueueUpscale(const GSTextureCache::HashCacheKey& key, const u8* src, int sw, int sh, u32 src_pitch,
		GSTextureUpscaleAlgorithm algorithm, u8 scale, TextureClass texture_class, bool mipmap,
		const std::pair<u8, u8>& alpha_minmax);

	/// Move finished jobs out for injection, stopping once `max_bytes` have been taken so a
	/// burst cannot stall one frame. GS thread only.
	void PopCompleted(std::vector<CompletedUpscale>& out, u32 max_bytes);

	/// Stop and join the worker. Safe to call when it was never started.
	void Shutdown();

	/// Per-frame bookkeeping: resets the rate limiter.
	void NextFrame();

	/// Drops all accounting. Call when the hash cache is cleared.
	void Reset();

	/// Bytes currently held by upscaled textures.
	u32 GetMemoryUsage();

	/// Session counters. See Stats.
	const Stats& GetStats();

	/// Short name of the world-texture algorithm currently selected, for the OSD. Seeing the
	/// filter's name on screen is how you tell a setting actually took effect - "it says
	/// Scale2x" and "I chose Scale2x" being the same thing is the whole point.
	const char* CurrentAlgorithmName();

	/// Once per session, run every implemented filter over a small synthetic texture and log a
	/// one-line summary, naming any that fail.
	///
	/// These kernels are pure functions of a pixel buffer, so a smoke test is cheap and catches
	/// the failure that matters most here - a filter that crashes, writes out of bounds, or
	/// silently produces nothing - without needing a game, a setting, or the right texture to
	/// come along.
	void RunFilterSelfTestOnce();
} // namespace GSTextureUpscaler
