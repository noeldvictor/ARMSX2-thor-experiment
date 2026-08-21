// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#include "GS/Renderers/HW/GSTextureUpscaler.h"

#include "GS/GS.h"

#include "common/Console.h"

#include <algorithm>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace GSTextureUpscaler
{
	namespace
	{
		/// A texture at or below this on both axes is treated as UI/2D. PS2 HUD art, fonts and
		/// overlays are overwhelmingly small; world surfaces are not. This is a heuristic and
		/// will misfile some textures — that is why the two classes are separately switchable,
		/// so a wrong guess is something the user can turn off rather than something they are
		/// stuck with.
		constexpr int UI_CLASS_MAX_DIMENSION = 128;

		/// Ceiling on upscales started per frame. Animated textures re-hash every frame (see
		/// Silent Hill 2's fog, PCSX2 #11792) and would otherwise generate unbounded work at a
		/// scene transition. Bounding the rate keeps a bad case slow rather than fatal.
		constexpr u32 MAX_UPSCALES_PER_FRAME = 8;

		u32 s_memory_usage = 0;
		u32 s_upscales_this_frame = 0;
		bool s_warned_over_budget = false;

		/// Hashes refused because the budget was full when they came up. Without this the same
		/// texture is retried every time it misses, which costs more than never upscaling it.
		std::unordered_set<u64> s_declined;

		/// Bytes per upscaled texture, so eviction can give the budget back.
		std::unordered_map<u64, u32> s_held;

		u32 GetBudgetBytes()
		{
			return static_cast<u32>(GSConfig.TextureUpscaleVramBudgetMB) * 1024u * 1024u;
		}

		TextureClass ClassifyTexture(int tw, int th)
		{
			return (tw <= UI_CLASS_MAX_DIMENSION && th <= UI_CLASS_MAX_DIMENSION) ? TextureClass::Ui :
																					TextureClass::World;
		}

		u32 ClampScale(u8 configured)
		{
			// Only 2 and 4 are meaningful. Thor's panel is 1080x1920, so past 4x the extra
			// pixels cannot be shown and only cost VRAM.
			return (configured >= 4) ? 4u : 2u;
		}

		inline u32 SamplePixel(const u32* src, int sw, int sh, u32 stride_px, int x, int y)
		{
			x = std::clamp(x, 0, sw - 1);
			y = std::clamp(y, 0, sh - 1);
			return src[static_cast<size_t>(y) * stride_px + static_cast<size_t>(x)];
		}

		/// Scale2x / AdvMAME2x. Replicates neighbours across a hard edge and leaves everything
		/// else alone, so it never invents a colour that was not already there.
		void PassScale2x(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 E = SamplePixel(src, sw, sh, src_stride, x, y);
					const u32 B = SamplePixel(src, sw, sh, src_stride, x, y - 1);
					const u32 H = SamplePixel(src, sw, sh, src_stride, x, y + 1);
					const u32 D = SamplePixel(src, sw, sh, src_stride, x - 1, y);
					const u32 F = SamplePixel(src, sw, sh, src_stride, x + 1, y);

					u32 e0 = E, e1 = E, e2 = E, e3 = E;
					if (B != H && D != F)
					{
						e0 = (D == B) ? D : E;
						e1 = (B == F) ? F : E;
						e2 = (D == H) ? D : E;
						e3 = (H == F) ? F : E;
					}

					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = e0;
					out[1] = e1;
					out[dst_stride] = e2;
					out[dst_stride + 1] = e3;
				}
			}
		}

		/// Eagle. Corner-biased: an output quadrant takes a neighbour only when all three
		/// pixels around that corner agree.
		void PassEagle(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 C = SamplePixel(src, sw, sh, src_stride, x, y);
					const u32 S = SamplePixel(src, sw, sh, src_stride, x - 1, y - 1);
					const u32 T = SamplePixel(src, sw, sh, src_stride, x, y - 1);
					const u32 U = SamplePixel(src, sw, sh, src_stride, x + 1, y - 1);
					const u32 V = SamplePixel(src, sw, sh, src_stride, x - 1, y);
					const u32 W = SamplePixel(src, sw, sh, src_stride, x + 1, y);
					const u32 X = SamplePixel(src, sw, sh, src_stride, x - 1, y + 1);
					const u32 Y = SamplePixel(src, sw, sh, src_stride, x, y + 1);
					const u32 Z = SamplePixel(src, sw, sh, src_stride, x + 1, y + 1);

					const u32 e0 = (S == T && S == V) ? S : C;
					const u32 e1 = (T == U && U == W) ? U : C;
					const u32 e2 = (V == X && X == Y) ? X : C;
					const u32 e3 = (W == Z && Z == Y) ? Z : C;

					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = e0;
					out[1] = e1;
					out[dst_stride] = e2;
					out[dst_stride + 1] = e3;
				}
			}
		}

		void ScaleBilinear(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			// Fixed point, 8 fractional bits. Sampling at pixel centres, hence the half-texel
			// shift — without it the whole image drifts by half a source pixel.
			for (int y = 0; y < dh; y++)
			{
				const int fy = ((y * 256 + 128) / static_cast<int>(scale)) - 128;
				const int y0 = fy >> 8;
				const int wy = fy & 0xFF;
				for (int x = 0; x < dw; x++)
				{
					const int fx = ((x * 256 + 128) / static_cast<int>(scale)) - 128;
					const int x0 = fx >> 8;
					const int wx = fx & 0xFF;

					const u32 p00 = SamplePixel(src, sw, sh, src_stride, x0, y0);
					const u32 p10 = SamplePixel(src, sw, sh, src_stride, x0 + 1, y0);
					const u32 p01 = SamplePixel(src, sw, sh, src_stride, x0, y0 + 1);
					const u32 p11 = SamplePixel(src, sw, sh, src_stride, x0 + 1, y0 + 1);

					u32 out = 0;
					for (int c = 0; c < 4; c++)
					{
						const int shift = c * 8;
						const int c00 = (p00 >> shift) & 0xFF;
						const int c10 = (p10 >> shift) & 0xFF;
						const int c01 = (p01 >> shift) & 0xFF;
						const int c11 = (p11 >> shift) & 0xFF;
						const int top = c00 + (((c10 - c00) * wx) >> 8);
						const int bot = c01 + (((c11 - c01) * wx) >> 8);
						const int val = top + (((bot - top) * wy) >> 8);
						out |= static_cast<u32>(std::clamp(val, 0, 255)) << shift;
					}
					dst[static_cast<size_t>(y) * dst_stride + static_cast<size_t>(x)] = out;
				}
			}
		}
	} // namespace

	bool IsAlgorithmImplemented(GSTextureUpscaleAlgorithm algorithm)
	{
		// The enum lists the whole intended library so the menu and the config format are
		// stable from the start. Only these have kernels so far; everything else declines and
		// the texture stays native. See docs/texture-upscaling-research.md for the rest.
		switch (algorithm)
		{
			case GSTextureUpscaleAlgorithm::Bilinear:
			case GSTextureUpscaleAlgorithm::Scale2x:
			case GSTextureUpscaleAlgorithm::Eagle:
				return true;
			default:
				return false;
		}
	}

	bool IsEnabled()
	{
		return GSConfig.TextureUpscaleWorldEnabled || GSConfig.TextureUpscaleUiEnabled;
	}

	Plan MakePlan(u64 tex0_hash, int tw, int th)
	{
		Plan plan;
		plan.texture_class = ClassifyTexture(tw, th);

		const bool class_enabled = (plan.texture_class == TextureClass::Ui) ?
									   GSConfig.TextureUpscaleUiEnabled :
									   GSConfig.TextureUpscaleWorldEnabled;
		if (!class_enabled)
			return plan;

		plan.algorithm = (plan.texture_class == TextureClass::Ui) ? GSConfig.TextureUpscaleUiAlgorithm :
																	GSConfig.TextureUpscaleWorldAlgorithm;
		if (!IsAlgorithmImplemented(plan.algorithm))
			return plan;

		if (s_upscales_this_frame >= MAX_UPSCALES_PER_FRAME)
			return plan;

		if (s_declined.find(tex0_hash) != s_declined.end())
			return plan;

		const u32 scale = ClampScale((plan.texture_class == TextureClass::Ui) ?
										 GSConfig.TextureUpscaleUiScale :
										 GSConfig.TextureUpscaleWorldScale);

		// Refuse before allocating rather than after: going over and then evicting is how a
		// busy scene ends up pinned at the ceiling.
		const u64 projected = static_cast<u64>(tw) * static_cast<u64>(th) * scale * scale * 4ull;
		if (static_cast<u64>(s_memory_usage) + projected > static_cast<u64>(GetBudgetBytes()))
		{
			s_declined.insert(tex0_hash);
			if (!s_warned_over_budget)
			{
				s_warned_over_budget = true;
				Console.Warning("Texture upscaling: VRAM budget of %u MB reached, leaving further "
								"textures native. Lower the scale factor or raise the budget.",
					static_cast<u32>(GSConfig.TextureUpscaleVramBudgetMB));
			}
			return plan;
		}

		plan.scale = static_cast<u8>(scale);
		return plan;
	}

	bool ScaleBuffer(GSTextureUpscaleAlgorithm algorithm, const u8* src, int sw, int sh, u32 src_pitch,
		u8* dst, u32 dst_pitch, u8 scale)
	{
		if (sw <= 0 || sh <= 0 || (scale != 2 && scale != 4))
			return false;

		const u32 src_stride = src_pitch / sizeof(u32);
		const u32 dst_stride = dst_pitch / sizeof(u32);
		const u32* src_px = reinterpret_cast<const u32*>(src);
		u32* dst_px = reinterpret_cast<u32*>(dst);

		switch (algorithm)
		{
			case GSTextureUpscaleAlgorithm::Bilinear:
				ScaleBilinear(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::Scale2x:
			case GSTextureUpscaleAlgorithm::Eagle:
			{
				const auto pass = (algorithm == GSTextureUpscaleAlgorithm::Scale2x) ? PassScale2x : PassEagle;
				if (scale == 2)
				{
					pass(src_px, sw, sh, src_stride, dst_px, dst_stride);
					return true;
				}

				// 4x is two 2x passes through a temporary at the intermediate size.
				const int mw = sw * 2;
				const int mh = sh * 2;
				std::vector<u32> mid(static_cast<size_t>(mw) * static_cast<size_t>(mh));
				pass(src_px, sw, sh, src_stride, mid.data(), static_cast<u32>(mw));
				pass(mid.data(), mw, mh, static_cast<u32>(mw), dst_px, dst_stride);
				return true;
			}

			default:
				// Not implemented yet — caller falls back to the native texture.
				return false;
		}
	}

	void NoteUpscaled(u64 tex0_hash, u32 bytes)
	{
		s_memory_usage += bytes;
		s_upscales_this_frame++;
		s_held[tex0_hash] = bytes;
	}

	void NoteEvicted(u64 tex0_hash, u32 bytes)
	{
		const auto it = s_held.find(tex0_hash);
		if (it == s_held.end())
			return;

		s_memory_usage -= std::min(s_memory_usage, bytes);
		s_held.erase(it);
	}

	void NextFrame()
	{
		s_upscales_this_frame = 0;
	}

	void Reset()
	{
		s_memory_usage = 0;
		s_upscales_this_frame = 0;
		s_warned_over_budget = false;
		s_declined.clear();
		s_held.clear();
	}

	u32 GetMemoryUsage()
	{
		return s_memory_usage;
	}
} // namespace GSTextureUpscaler
