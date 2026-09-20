// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#include "GS/Renderers/HW/GSTextureUpscaler.h"

#include "GS/GS.h"
#include "GS/Renderers/HW/GSTextureUpscalerNN.h"
#include "GS/Renderers/HW/GSTextureUpscalerRaisr.h"

#include "common/Console.h"

#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <iterator>
#include <mutex>
#include <thread>
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

		Stats s_stats;

		/// Hashes refused because the budget was full when they came up. Without this the same
		/// texture is retried every time it misses, which costs more than never upscaling it.
		std::unordered_set<u64> s_declined;

		/// Bytes per upscaled texture, so eviction can give the budget back.
		std::unordered_map<u64, u32> s_held;

		// ---- worker thread -------------------------------------------------------------
		//
		// One thread, started lazily the first time anything is queued. Deliberately not
		// reusing GSTextureReplacements' worker: that one only runs while dumping or
		// replacement is enabled, and upscaling has to work with both of those off.

		struct PendingJob
		{
			GSTextureCache::HashCacheKey key;
			std::vector<u32> src;
			int sw = 0;
			int sh = 0;
			u32 src_stride = 0;
			GSTextureUpscaleAlgorithm algorithm = GSTextureUpscaleAlgorithm::Bilinear;
			u8 scale = 2;
			TextureClass texture_class = TextureClass::World;
			bool mipmap = false;
			bool deposterize = false;
			std::pair<u8, u8> alpha_minmax{0u, 255u};
		};

		std::thread s_worker;
		std::mutex s_worker_mutex;
		std::condition_variable s_worker_cv;
		std::deque<PendingJob> s_pending;
		std::vector<CompletedUpscale> s_completed;
		bool s_worker_running = false;
		bool s_worker_quit = false;

		/// Hashes already queued, so a texture that misses the cache repeatedly while its
		/// upscale is still in flight does not queue the same work several times over.
		std::unordered_set<u64> s_in_flight;

		// Defined further down with the other kernels; the worker is declared before them.
		void Deposterize(std::vector<u32>& px, int w, int h);

		void WorkerLoop()
		{
			for (;;)
			{
				PendingJob job;
				{
					std::unique_lock<std::mutex> lock(s_worker_mutex);
					s_worker_cv.wait(lock, [] { return s_worker_quit || !s_pending.empty(); });
					if (s_worker_quit)
						return;
					job = std::move(s_pending.front());
					s_pending.pop_front();
				}

				// Pre-pass first: it is cheap, and every filter below benefits from not
				// being handed banded input.
				if (job.deposterize)
					Deposterize(job.src, job.sw, job.sh);

				const int dw = job.sw * static_cast<int>(job.scale);
				const int dh = job.sh * static_cast<int>(job.scale);
				std::vector<u32> out(static_cast<size_t>(dw) * static_cast<size_t>(dh));

				const bool ok = ScaleBuffer(job.algorithm, reinterpret_cast<const u8*>(job.src.data()), job.sw,
					job.sh, job.src_stride * sizeof(u32), reinterpret_cast<u8*>(out.data()),
					static_cast<u32>(dw) * sizeof(u32), job.scale, job.texture_class);

				std::unique_lock<std::mutex> lock(s_worker_mutex);
				s_in_flight.erase(job.key.TEX0Hash);
				if (!ok)
					continue;

				CompletedUpscale done;
				done.key = job.key;
				done.pixels = std::move(out);
				done.width = dw;
				done.height = dh;
				done.mipmap = job.mipmap;
				done.alpha_minmax = job.alpha_minmax;
				s_completed.push_back(std::move(done));
			}
		}

		void EnsureWorker()
		{
			if (s_worker_running)
				return;
			s_worker_quit = false;
			s_worker = std::thread(WorkerLoop);
			s_worker_running = true;
		}

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

		// ---------------------------------------------------------------------------------
		// Pixel helpers. Everything below works on packed RGBA8 in a u32.
		// ---------------------------------------------------------------------------------

		inline u32 SamplePixel(const u32* src, int sw, int sh, u32 stride_px, int x, int y)
		{
			x = std::clamp(x, 0, sw - 1);
			y = std::clamp(y, 0, sh - 1);
			return src[static_cast<size_t>(y) * stride_px + static_cast<size_t>(x)];
		}

		/// Average of two pixels, per channel.
		inline u32 Mix2(u32 a, u32 b)
		{
			// Standard SWAR average: the low bits of each channel are handled separately so
			// the halves cannot carry into the neighbouring channel.
			return (((a ^ b) & 0xFEFEFEFEu) >> 1) + (a & b);
		}

		/// Average of four pixels, per channel.
		inline u32 Mix4(u32 a, u32 b, u32 c, u32 d)
		{
			return Mix2(Mix2(a, b), Mix2(c, d));
		}

		/// Weighted blend, weights out of 'total'. Used by the edge-directed filters where a
		/// 50/50 mix is too blunt.
		inline u32 BlendWeighted(u32 a, u32 b, int wa, int wb)
		{
			const int total = wa + wb;
			u32 out = 0;
			for (int c = 0; c < 4; c++)
			{
				const int shift = c * 8;
				const int ca = static_cast<int>((a >> shift) & 0xFF);
				const int cb = static_cast<int>((b >> shift) & 0xFF);
				out |= static_cast<u32>(std::clamp((ca * wa + cb * wb) / total, 0, 255)) << shift;
			}
			return out;
		}

		/// Perceptual-ish colour distance. Weighted toward luma because the edge-directed
		/// filters are deciding "is this the same surface", and hue drift matters far less
		/// there than brightness does.
		inline int ColorDistance(u32 a, u32 b)
		{
			const int dr = static_cast<int>((a >> 0) & 0xFF) - static_cast<int>((b >> 0) & 0xFF);
			const int dg = static_cast<int>((a >> 8) & 0xFF) - static_cast<int>((b >> 8) & 0xFF);
			const int db = static_cast<int>((a >> 16) & 0xFF) - static_cast<int>((b >> 16) & 0xFF);
			const int da = static_cast<int>((a >> 24) & 0xFF) - static_cast<int>((b >> 24) & 0xFF);
			return std::abs(dr) * 3 + std::abs(dg) * 6 + std::abs(db) * 1 + std::abs(da) * 4;
		}

		// ---------------------------------------------------------------------------------
		// Resample family. Neutral, cheap, and never invents an edge that was not there.
		// ---------------------------------------------------------------------------------

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

		/// Catmull-Rom. Sharper than bilinear without Lanczos' ringing, and it passes through
		/// the source samples exactly, which matters for flat-coloured art.
		inline float CatmullRomWeight(float t, int tap)
		{
			const float t2 = t * t;
			const float t3 = t2 * t;
			switch (tap)
			{
				case 0: return -0.5f * t3 + t2 - 0.5f * t;
				case 1: return 1.5f * t3 - 2.5f * t2 + 1.0f;
				case 2: return -1.5f * t3 + 2.0f * t2 + 0.5f * t;
				default: return 0.5f * t3 - 0.5f * t2;
			}
		}

		void ScaleBicubic(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			const float inv = 1.0f / static_cast<float>(scale);

			for (int y = 0; y < dh; y++)
			{
				const float sy = (static_cast<float>(y) + 0.5f) * inv - 0.5f;
				const int y0 = static_cast<int>(std::floor(sy));
				const float ty = sy - static_cast<float>(y0);
				float wy[4];
				for (int i = 0; i < 4; i++)
					wy[i] = CatmullRomWeight(ty, i);

				for (int x = 0; x < dw; x++)
				{
					const float sx = (static_cast<float>(x) + 0.5f) * inv - 0.5f;
					const int x0 = static_cast<int>(std::floor(sx));
					const float tx = sx - static_cast<float>(x0);
					float wx[4];
					for (int i = 0; i < 4; i++)
						wx[i] = CatmullRomWeight(tx, i);

					float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
					for (int j = 0; j < 4; j++)
					{
						for (int i = 0; i < 4; i++)
						{
							const u32 p = SamplePixel(src, sw, sh, src_stride, x0 - 1 + i, y0 - 1 + j);
							const float w = wx[i] * wy[j];
							for (int c = 0; c < 4; c++)
								acc[c] += w * static_cast<float>((p >> (c * 8)) & 0xFF);
						}
					}

					u32 out = 0;
					for (int c = 0; c < 4; c++)
						out |= static_cast<u32>(std::clamp(static_cast<int>(acc[c] + 0.5f), 0, 255)) << (c * 8);
					dst[static_cast<size_t>(y) * dst_stride + static_cast<size_t>(x)] = out;
				}
			}
		}

		void ScaleNearest(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			for (int y = 0; y < dh; y++)
			{
				const int sy = y / static_cast<int>(scale);
				for (int x = 0; x < dw; x++)
					dst[static_cast<size_t>(y) * dst_stride + x] =
						SamplePixel(src, sw, sh, src_stride, x / static_cast<int>(scale), sy);
			}
		}

		/// Mitchell-Netravali with B = C = 1/3, the values the original paper settles on as the
		/// best subjective compromise. Softer than Catmull-Rom but it does not ring, which makes
		/// it the safer of the two on textures with hard colour steps.
		inline float MitchellKernel(float x)
		{
			constexpr float B = 1.0f / 3.0f;
			constexpr float C = 1.0f / 3.0f;
			x = std::fabs(x);
			const float x2 = x * x;
			const float x3 = x2 * x;
			if (x < 1.0f)
				return ((12.0f - 9.0f * B - 6.0f * C) * x3 + (-18.0f + 12.0f * B + 6.0f * C) * x2 +
						   (6.0f - 2.0f * B)) /
					   6.0f;
			if (x < 2.0f)
				return ((-B - 6.0f * C) * x3 + (6.0f * B + 30.0f * C) * x2 + (-12.0f * B - 48.0f * C) * x +
						   (8.0f * B + 24.0f * C)) /
					   6.0f;
			return 0.0f;
		}

		inline float MitchellWeight(float t, int tap)
		{
			// Taps sit at -1, 0, 1, 2 relative to the floored source pixel.
			const float distances[4] = {t + 1.0f, t, 1.0f - t, 2.0f - t};
			return MitchellKernel(distances[tap]);
		}

		void ScaleMitchell(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			const float inv = 1.0f / static_cast<float>(scale);

			for (int y = 0; y < dh; y++)
			{
				const float sy = (static_cast<float>(y) + 0.5f) * inv - 0.5f;
				const int y0 = static_cast<int>(std::floor(sy));
				const float ty = sy - static_cast<float>(y0);
				float wy[4];
				for (int i = 0; i < 4; i++)
					wy[i] = MitchellWeight(ty, i);

				for (int x = 0; x < dw; x++)
				{
					const float sx = (static_cast<float>(x) + 0.5f) * inv - 0.5f;
					const int x0 = static_cast<int>(std::floor(sx));
					const float tx = sx - static_cast<float>(x0);
					float wx[4];
					for (int i = 0; i < 4; i++)
						wx[i] = MitchellWeight(tx, i);

					float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
					for (int j = 0; j < 4; j++)
					{
						for (int i = 0; i < 4; i++)
						{
							const u32 p = SamplePixel(src, sw, sh, src_stride, x0 - 1 + i, y0 - 1 + j);
							const float w = wx[i] * wy[j];
							for (int c = 0; c < 4; c++)
								acc[c] += w * static_cast<float>((p >> (c * 8)) & 0xFF);
						}
					}

					u32 out = 0;
					for (int c = 0; c < 4; c++)
						out |= static_cast<u32>(std::clamp(static_cast<int>(acc[c] + 0.5f), 0, 255)) << (c * 8);
					dst[static_cast<size_t>(y) * dst_stride + x] = out;
				}
			}
		}

		/// Sharp bilinear. Keeps each source texel flat and confines the blend to a one-output-
		/// pixel ramp at the boundary, so it reads as crisp as nearest without nearest's uneven
		/// block sizes. The usual choice for pixel art that should not look filtered at all.
		void ScaleSharpBilinear(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride,
			u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			const float inv = 1.0f / static_cast<float>(scale);
			const float fscale = static_cast<float>(scale);
			// Half the texel, minus half an output pixel: the width of the flat region either
			// side of a texel centre. Everything outside it is the ramp.
			const float region = 0.5f - 0.5f / fscale;

			const auto remap = [&](float coord) -> float {
				const float floored = std::floor(coord);
				const float frac = coord - floored;
				const float centre_dist = frac - 0.5f;
				const float ramped =
					(centre_dist - std::clamp(centre_dist, -region, region)) * fscale + 0.5f;
				return floored + ramped;
			};

			for (int y = 0; y < dh; y++)
			{
				const float sy = remap((static_cast<float>(y) + 0.5f) * inv);
				const int y0 = static_cast<int>(std::floor(sy - 0.5f));
				const float ty = (sy - 0.5f) - static_cast<float>(y0);

				for (int x = 0; x < dw; x++)
				{
					const float sx = remap((static_cast<float>(x) + 0.5f) * inv);
					const int x0 = static_cast<int>(std::floor(sx - 0.5f));
					const float tx = (sx - 0.5f) - static_cast<float>(x0);

					const u32 p00 = SamplePixel(src, sw, sh, src_stride, x0, y0);
					const u32 p10 = SamplePixel(src, sw, sh, src_stride, x0 + 1, y0);
					const u32 p01 = SamplePixel(src, sw, sh, src_stride, x0, y0 + 1);
					const u32 p11 = SamplePixel(src, sw, sh, src_stride, x0 + 1, y0 + 1);

					u32 out = 0;
					for (int c = 0; c < 4; c++)
					{
						const int shift = c * 8;
						const float c00 = static_cast<float>((p00 >> shift) & 0xFF);
						const float c10 = static_cast<float>((p10 >> shift) & 0xFF);
						const float c01 = static_cast<float>((p01 >> shift) & 0xFF);
						const float c11 = static_cast<float>((p11 >> shift) & 0xFF);
						const float top = c00 + (c10 - c00) * tx;
						const float bot = c01 + (c11 - c01) * tx;
						const float v = top + (bot - top) * ty;
						out |= static_cast<u32>(std::clamp(static_cast<int>(v + 0.5f), 0, 255)) << shift;
					}
					dst[static_cast<size_t>(y) * dst_stride + x] = out;
				}
			}
		}

		// -------------------------------------------------------------------------------
		// Anime4K (v1 "push/gradient"), ported from the GLSL texture filter in Citra/Azahar,
		// which is itself bloc97's Anime4K under the MIT licence. See docs/third-party.md.
		//
		// The thing worth knowing: Anime4K v1 is NOT a neural network. It is a hand-written
		// edge-refinement pass - Sobel gradient, then push colours along it - which is exactly
		// why it can ship as an algorithm with no weights file, while the later Anime4K CNN
		// modes cannot. So it belongs beside the edge-directed filters, not the model-driven
		// ones.
		// -------------------------------------------------------------------------------

		/// BT.2020 luma weights, matching the original.
		inline float Luma(u32 px)
		{
			const float r = static_cast<float>((px >> 0) & 0xFF) * (1.0f / 255.0f);
			const float g = static_cast<float>((px >> 8) & 0xFF) * (1.0f / 255.0f);
			const float b = static_cast<float>((px >> 16) & 0xFF) * (1.0f / 255.0f);
			return 0.2627f * r + 0.6780f * g + 0.0593f * b;
		}

		inline u32 Anime4KAverage(u32 cc, u32 a, u32 b, u32 c, float strength)
		{
			u32 out = 0;
			for (int ch = 0; ch < 4; ch++)
			{
				const int shift = ch * 8;
				const float mean = (static_cast<float>((a >> shift) & 0xFF) +
									   static_cast<float>((b >> shift) & 0xFF) +
									   static_cast<float>((c >> shift) & 0xFF)) /
								   3.0f;
				const float v = static_cast<float>((cc >> shift) & 0xFF) * (1.0f - strength) + mean * strength;
				out |= static_cast<u32>(std::clamp(static_cast<int>(v + 0.5f), 0, 255)) << shift;
			}
			return out;
		}

		void ScaleAnime4K(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			// Refinement runs at the OUTPUT resolution: it sharpens the soft edges the resample
			// just created, so doing it first would refine detail that is about to be blurred
			// away again.
			ScaleBilinear(src, sw, sh, src_stride, dst, dst_stride, scale);

			const int w = sw * static_cast<int>(scale);
			const int h = sh * static_cast<int>(scale);
			const size_t n = static_cast<size_t>(w) * static_cast<size_t>(h);

			const auto image = [&](int x, int y) -> u32 {
				x = std::clamp(x, 0, w - 1);
				y = std::clamp(y, 0, h - 1);
				return dst[static_cast<size_t>(y) * dst_stride + x];
			};

			// Pass 1, horizontal half of a separable Sobel: (difference, weighted sum).
			std::vector<float> gx(n), gy(n);
			for (int y = 0; y < h; y++)
			{
				for (int x = 0; x < w; x++)
				{
					const float l = Luma(image(x - 1, y));
					const float c = Luma(image(x, y));
					const float r = Luma(image(x + 1, y));
					gx[static_cast<size_t>(y) * w + x] = r - l;
					gy[static_cast<size_t>(y) * w + x] = l + 2.0f * c + r;
				}
			}

			// Pass 2, vertical half. lumad is 1 - |gradient|, so a HIGH value means a flat area
			// and a low one means an edge - which is why the threshold below reads as "leave
			// flat areas alone" rather than the other way round.
			std::vector<float> lumad(n);
			for (int y = 0; y < h; y++)
			{
				for (int x = 0; x < w; x++)
				{
					const auto at = [&](int yy) -> size_t {
						return static_cast<size_t>(std::clamp(yy, 0, h - 1)) * static_cast<size_t>(w) +
							   static_cast<size_t>(x);
					};
					const float sum = gx[at(y - 1)] + 2.0f * gx[at(y)] + gx[at(y + 1)];
					const float diff = gy[at(y + 1)] - gy[at(y - 1)];
					lumad[static_cast<size_t>(y) * w + x] = 1.0f - std::sqrt(sum * sum + diff * diff);
				}
			}

			// Pass 3: push. Eight directional kernels, each asking "is there a light ridge on
			// one side and a darker one opposite", and if so blending the centre toward the
			// light side. That is what straightens a staircased diagonal.
			constexpr float LINE_DETECT_THRESHOLD = 0.4f;
			constexpr float STRENGTH = 0.6f;

			// gx/gy have done their job; releasing them here halves peak scratch before the
			// push pass allocates anything.
			gx.clear();
			gx.shrink_to_fit();
			gy.clear();
			gy.shrink_to_fit();

			const auto lum = [&](int x, int y) -> float {
				x = std::clamp(x, 0, w - 1);
				y = std::clamp(y, 0, h - 1);
				return lumad[static_cast<size_t>(y) * w + x];
			};
			const auto min3 = [](float a, float b, float c) { return std::min(std::min(a, b), c); };
			const auto max3 = [](float a, float b, float c) { return std::max(std::max(a, b), c); };

			// Rolling originals: prev = row y-1 as it was before the push wrote it, cur = row y.
			// Row y+1 is still untouched in dst, so it can be read directly.
			std::vector<u32> prev(w), cur(w), line(w);
			for (int x = 0; x < w; x++)
				cur[static_cast<size_t>(x)] = dst[static_cast<size_t>(0) * dst_stride + x];
			prev = cur;

			for (int y = 0; y < h; y++)
			{
				const auto colour = [&](int cx, int cy) -> u32 {
					cx = std::clamp(cx, 0, w - 1);
					if (cy < y)
						return prev[static_cast<size_t>(cx)];
					if (cy == y)
						return cur[static_cast<size_t>(cx)];
					const int ny2 = std::min(y + 1, h - 1);
					return dst[static_cast<size_t>(ny2) * dst_stride + cx];
				};

				for (int x = 0; x < w; x++)
				{
					const u32 cc = colour(x, y);
					const float ccl = lum(x, y);
					u32 result = cc;

					// lumad is 1 - |gradient|, so LOW means a strong edge. This branch is the
					// one that pushes; a flat area (high lumad) falls through and keeps exactly
					// what the resample produced.
					if (ccl <= LINE_DETECT_THRESHOLD)
					{
						const u32 tl = colour(x - 1, y - 1), tc = colour(x, y - 1), tr = colour(x + 1, y - 1);
						const u32 lc = colour(x - 1, y), rc = colour(x + 1, y);
						const u32 bl = colour(x - 1, y + 1), bc = colour(x, y + 1), br = colour(x + 1, y + 1);
						const float tll = lum(x - 1, y - 1), tcl = lum(x, y - 1), trl = lum(x + 1, y - 1);
						const float lcl = lum(x - 1, y), rcl = lum(x + 1, y);
						const float bll = lum(x - 1, y + 1), bcl = lum(x, y + 1), brl = lum(x + 1, y + 1);

						bool done = false;
						float maxDark, minLight;

						// Kernels 0 and 4 - horizontal ridges.
						maxDark = max3(brl, bcl, bll);
						minLight = min3(tll, tcl, trl);
						if (minLight > ccl && minLight > maxDark)
						{
							result = Anime4KAverage(cc, tl, tc, tr, STRENGTH);
							done = true;
						}
						else
						{
							maxDark = max3(tll, tcl, trl);
							minLight = min3(brl, bcl, bll);
							if (minLight > ccl && minLight > maxDark)
							{
								result = Anime4KAverage(cc, br, bc, bl, STRENGTH);
								done = true;
							}
						}

						// Kernels 1 and 5 - one diagonal.
						if (!done)
						{
							maxDark = max3(ccl, lcl, bcl);
							minLight = min3(rcl, tcl, trl);
							if (minLight > maxDark)
							{
								result = Anime4KAverage(cc, rc, tc, tr, STRENGTH);
								done = true;
							}
							else
							{
								maxDark = max3(ccl, rcl, tcl);
								minLight = min3(bll, lcl, bcl);
								if (minLight > maxDark)
								{
									result = Anime4KAverage(cc, bl, lc, bc, STRENGTH);
									done = true;
								}
							}
						}

						// Kernels 2 and 6 - vertical ridges.
						if (!done)
						{
							maxDark = max3(lcl, tll, bll);
							minLight = min3(rcl, brl, trl);
							if (minLight > ccl && minLight > maxDark)
							{
								result = Anime4KAverage(cc, rc, br, tr, STRENGTH);
								done = true;
							}
							else
							{
								maxDark = max3(rcl, brl, trl);
								minLight = min3(lcl, tll, bll);
								if (minLight > ccl && minLight > maxDark)
								{
									result = Anime4KAverage(cc, lc, tl, bl, STRENGTH);
									done = true;
								}
							}
						}

						// Kernels 3 and 7 - the other diagonal.
						if (!done)
						{
							maxDark = max3(ccl, lcl, tcl);
							minLight = min3(rcl, brl, bcl);
							if (minLight > maxDark)
							{
								result = Anime4KAverage(cc, rc, br, bc, STRENGTH);
							}
							else
							{
								maxDark = max3(ccl, rcl, bcl);
								minLight = min3(tcl, lcl, tll);
								if (minLight > maxDark)
									result = Anime4KAverage(cc, tc, lc, tl, STRENGTH);
							}
						}
					}

					line[static_cast<size_t>(x)] = result;
				}

				// Rows are rotated rather than kept in a full-size scratch image: the push reads
				// one row either side, and rows below have not been written yet, so three rolling
				// rows is all the history needed. A whole extra output-sized buffer here was the
				// difference between tens and hundreds of megabytes on a large texture.
				prev.swap(cur);
				const int ny = std::min(y + 1, h - 1);
				for (int x = 0; x < w; x++)
					cur[static_cast<size_t>(x)] = dst[static_cast<size_t>(ny) * dst_stride + x];
				for (int x = 0; x < w; x++)
					dst[static_cast<size_t>(y) * dst_stride + x] = line[static_cast<size_t>(x)];
			}
		}

		// -------------------------------------------------------------------------------
		// xBRZ (free-scale), ported from Citra/Azahar's GLSL texture filter (GPLv2-or-later),
		// itself derived from Zenju's xBRZ. See docs/third-party.md.
		//
		// "Free-scale" is why this one is dispatched like a resampler rather than as a 2x pass:
		// it decides a blend per OUTPUT pixel from that pixel's position inside its source
		// texel, so any scale factor works and 4x is a single pass rather than 2x applied twice.
		// -------------------------------------------------------------------------------

		constexpr int XBRZ_BLEND_NONE = 0;
		constexpr int XBRZ_BLEND_NORMAL = 1;
		constexpr int XBRZ_BLEND_DOMINANT = 2;
		constexpr float XBRZ_EQUAL_COLOR_TOLERANCE = 30.0f / 255.0f;
		constexpr float XBRZ_STEEP_DIRECTION_THRESHOLD = 2.2f;
		constexpr float XBRZ_DOMINANT_DIRECTION_THRESHOLD = 3.6f;

		/// Distance in a BT.2020-derived YCbCr space, weighted by alpha at both ends so a
		/// difference behind a transparent texel does not read as an edge.
		inline float XbrzColorDist(u32 pa, u32 pb)
		{
			const float ar = static_cast<float>((pa >> 0) & 0xFF) * (1.0f / 255.0f);
			const float ag = static_cast<float>((pa >> 8) & 0xFF) * (1.0f / 255.0f);
			const float ab = static_cast<float>((pa >> 16) & 0xFF) * (1.0f / 255.0f);
			const float aa = static_cast<float>((pa >> 24) & 0xFF) * (1.0f / 255.0f);
			const float br = static_cast<float>((pb >> 0) & 0xFF) * (1.0f / 255.0f);
			const float bg = static_cast<float>((pb >> 8) & 0xFF) * (1.0f / 255.0f);
			const float bb = static_cast<float>((pb >> 16) & 0xFF) * (1.0f / 255.0f);
			const float ba = static_cast<float>((pb >> 24) & 0xFF) * (1.0f / 255.0f);

			const float dr = ar - br, dg = ag - bg, db = ab - bb, da = aa - ba;

			constexpr float KR = 0.2627f, KG = 0.6780f, KB = 0.0593f;
			// Columns of the GLSL mat3, kept in that layout so this stays comparable with the
			// shader it came from.
			const float c0 = dr * KR + dg * KG + db * KB;
			const float c1 = dr * (-0.5f * KR / (1.0f - KB)) + dg * (-0.5f * KG / (1.0f - KB)) + db * 0.5f;
			const float c2 = dr * 0.5f + dg * (-0.5f * KG / (1.0f - KR)) + db * (-0.5f * KB / (1.0f - KR));

			// Alpha weighting, with one guard the reference does not need. PS2 sources expanded
			// through TEXA can be uniformly alpha-0 (PSMCT24 with TA0 = 0), and the reference
			// then returns 0 for EVERY pair - so every blend test fails and the filter silently
			// degrades to nearest while the OSD still counts the texture as upscaled. When both
			// alphas are zero there is no transparency information to weight by, so ignore it.
			const float aw = (aa <= 0.0f && ba <= 0.0f) ? 1.0f : (aa * ba);
			const float d = std::sqrt(c0 * c0 + c1 * c1 + c2 * c2);
			return std::sqrt(aw * d * d + da * da);
		}

		inline bool XbrzPixEqual(u32 a, u32 b)
		{
			return XbrzColorDist(a, b) < XBRZ_EQUAL_COLOR_TOLERANCE;
		}

		inline float XbrzSmoothStep(float edge0, float edge1, float x)
		{
			const float t = std::clamp((x - edge0) / (edge1 - edge0), 0.0f, 1.0f);
			return t * t * (3.0f - 2.0f * t);
		}

		/// How far the output pixel sits on the "left" side of a blending line, smoothed. This
		/// is the part that makes the result scale-independent.
		inline float XbrzLeftRatio(float cx, float cy, float ox, float oy, float dx, float dy, float scale)
		{
			const float p0x = cx - ox;
			const float p0y = cy - oy;
			const float dd = dx * dx + dy * dy;
			const float t = (dd > 0.0f) ? ((p0x * dx + p0y * dy) / dd) : 0.0f;
			const float distx = p0x - dx * t;
			const float disty = p0y - dy * t;
			// Orthogonal of the direction, to get which side we are on.
			const float side = (p0x * -dy + p0y * dx) < 0.0f ? -1.0f : 1.0f;
			const float v = side * std::sqrt((distx * scale) * (distx * scale) + (disty * scale) * (disty * scale));
			constexpr float H = 0.70710678f; // sqrt(2)/2
			return XbrzSmoothStep(-H, H, v);
		}

		inline u32 XbrzMix(u32 a, u32 b, float t)
		{
			u32 out = 0;
			for (int c = 0; c < 4; c++)
			{
				const int shift = c * 8;
				const float av = static_cast<float>((a >> shift) & 0xFF);
				const float bv = static_cast<float>((b >> shift) & 0xFF);
				const float v = av + (bv - av) * t;
				out |= static_cast<u32>(std::clamp(static_cast<int>(v + 0.5f), 0, 255)) << shift;
			}
			return out;
		}

		void ScaleXbrz(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			const float fscale = static_cast<float>(scale);

			for (int oy = 0; oy < dh; oy++)
			{
				for (int ox = 0; ox < dw; ox++)
				{
					// Texel this output pixel belongs to, and where inside it we are.
					const float tx = (static_cast<float>(ox) + 0.5f) / fscale;
					const float ty = (static_cast<float>(oy) + 0.5f) / fscale;
					const int bx = static_cast<int>(std::floor(tx));
					const int by = static_cast<int>(std::floor(ty));
					const float posx = (tx - static_cast<float>(bx)) - 0.5f;
					const float posy = (ty - static_cast<float>(by)) - 0.5f;

					const auto P = [&](int x, int y) -> u32 {
						return SamplePixel(src, sw, sh, src_stride, bx + x, by + y);
					};

					const u32 A = P(-1, -1), B = P(0, -1), C = P(1, -1);
					const u32 D = P(-1, 0), E = P(0, 0), F = P(1, 0);
					const u32 G = P(-1, 1), H = P(0, 1), I = P(1, 1);

					int blend_x = XBRZ_BLEND_NONE, blend_y = XBRZ_BLEND_NONE;
					int blend_z = XBRZ_BLEND_NONE, blend_w = XBRZ_BLEND_NONE;

					if (!((E == F && H == I) || (E == H && F == I)))
					{
						const float dist_H_F = XbrzColorDist(G, E) + XbrzColorDist(E, C) +
											   XbrzColorDist(P(0, 2), I) + XbrzColorDist(I, P(2, 0)) +
											   4.0f * XbrzColorDist(H, F);
						const float dist_E_I = XbrzColorDist(D, H) + XbrzColorDist(H, P(1, 2)) +
											   XbrzColorDist(B, F) + XbrzColorDist(F, P(2, 1)) +
											   4.0f * XbrzColorDist(E, I);
						const bool dominant = (XBRZ_DOMINANT_DIRECTION_THRESHOLD * dist_H_F) < dist_E_I;
						blend_z = ((dist_H_F < dist_E_I) && E != F && E != H) ?
									  (dominant ? XBRZ_BLEND_DOMINANT : XBRZ_BLEND_NORMAL) :
									  XBRZ_BLEND_NONE;
					}
					if (!((D == E && G == H) || (D == G && E == H)))
					{
						const float dist_G_E = XbrzColorDist(P(-2, 1), D) + XbrzColorDist(D, B) +
											   XbrzColorDist(P(-1, 2), H) + XbrzColorDist(H, F) +
											   4.0f * XbrzColorDist(G, E);
						const float dist_D_H = XbrzColorDist(P(-2, 0), G) + XbrzColorDist(G, P(0, 2)) +
											   XbrzColorDist(A, E) + XbrzColorDist(E, I) +
											   4.0f * XbrzColorDist(D, H);
						const bool dominant = (XBRZ_DOMINANT_DIRECTION_THRESHOLD * dist_D_H) < dist_G_E;
						blend_w = ((dist_G_E > dist_D_H) && E != D && E != H) ?
									  (dominant ? XBRZ_BLEND_DOMINANT : XBRZ_BLEND_NORMAL) :
									  XBRZ_BLEND_NONE;
					}
					if (!((B == C && E == F) || (B == E && C == F)))
					{
						const float dist_E_C = XbrzColorDist(D, B) + XbrzColorDist(B, P(1, -2)) +
											   XbrzColorDist(H, F) + XbrzColorDist(F, P(2, -1)) +
											   4.0f * XbrzColorDist(E, C);
						const float dist_B_F = XbrzColorDist(A, E) + XbrzColorDist(E, I) +
											   XbrzColorDist(P(0, -2), C) + XbrzColorDist(C, P(2, 0)) +
											   4.0f * XbrzColorDist(B, F);
						const bool dominant = (XBRZ_DOMINANT_DIRECTION_THRESHOLD * dist_B_F) < dist_E_C;
						blend_y = ((dist_E_C > dist_B_F) && E != B && E != F) ?
									  (dominant ? XBRZ_BLEND_DOMINANT : XBRZ_BLEND_NORMAL) :
									  XBRZ_BLEND_NONE;
					}
					if (!((A == B && D == E) || (A == D && B == E)))
					{
						const float dist_D_B = XbrzColorDist(P(-2, 0), A) + XbrzColorDist(A, P(0, -2)) +
											   XbrzColorDist(G, E) + XbrzColorDist(E, C) +
											   4.0f * XbrzColorDist(D, B);
						const float dist_A_E = XbrzColorDist(P(-2, -1), D) + XbrzColorDist(D, H) +
											   XbrzColorDist(P(-1, -2), B) + XbrzColorDist(B, F) +
											   4.0f * XbrzColorDist(A, E);
						const bool dominant = (XBRZ_DOMINANT_DIRECTION_THRESHOLD * dist_D_B) < dist_A_E;
						blend_x = ((dist_D_B < dist_A_E) && E != D && E != B) ?
									  (dominant ? XBRZ_BLEND_DOMINANT : XBRZ_BLEND_NORMAL) :
									  XBRZ_BLEND_NONE;
					}

					u32 res = E;
					constexpr float INV_SQRT2 = 0.70710678f;

					if (blend_z != XBRZ_BLEND_NONE)
					{
						const float dist_F_G = XbrzColorDist(F, G);
						const float dist_H_C = XbrzColorDist(H, C);
						const bool doLineBlend =
							(blend_z == XBRZ_BLEND_DOMINANT ||
								!((blend_y != XBRZ_BLEND_NONE && !XbrzPixEqual(E, G)) ||
									(blend_w != XBRZ_BLEND_NONE && !XbrzPixEqual(E, C)) ||
									(XbrzPixEqual(G, H) && XbrzPixEqual(H, I) && XbrzPixEqual(I, F) &&
										XbrzPixEqual(F, C) && !XbrzPixEqual(E, I))));
						float ox2 = 0.0f, oy2 = INV_SQRT2, dx = 1.0f, dy = -1.0f;
						if (doLineBlend)
						{
							const bool shallow =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_F_G <= dist_H_C) && E != G && D != G;
							const bool steep =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_H_C <= dist_F_G) && E != C && B != C;
							ox2 = 0.0f;
							oy2 = shallow ? 0.25f : 0.5f;
							dx += shallow ? 1.0f : 0.0f;
							dy -= steep ? 1.0f : 0.0f;
						}
						const u32 blendPix = (XbrzColorDist(E, H) >= XbrzColorDist(E, F)) ? F : H;
						res = XbrzMix(res, blendPix, XbrzLeftRatio(posx, posy, ox2, oy2, dx, dy, fscale));
					}
					if (blend_w != XBRZ_BLEND_NONE)
					{
						const float dist_H_A = XbrzColorDist(H, A);
						const float dist_D_I = XbrzColorDist(D, I);
						const bool doLineBlend =
							(blend_w == XBRZ_BLEND_DOMINANT ||
								!((blend_z != XBRZ_BLEND_NONE && !XbrzPixEqual(E, A)) ||
									(blend_x != XBRZ_BLEND_NONE && !XbrzPixEqual(E, I)) ||
									(XbrzPixEqual(A, D) && XbrzPixEqual(D, G) && XbrzPixEqual(G, H) &&
										XbrzPixEqual(H, I) && !XbrzPixEqual(E, G))));
						float ox2 = -INV_SQRT2, oy2 = 0.0f, dx = 1.0f, dy = 1.0f;
						if (doLineBlend)
						{
							const bool shallow =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_H_A <= dist_D_I) && E != A && B != A;
							const bool steep =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_D_I <= dist_H_A) && E != I && F != I;
							ox2 = shallow ? -0.25f : -0.5f;
							oy2 = 0.0f;
							dy += shallow ? 1.0f : 0.0f;
							dx += steep ? 1.0f : 0.0f;
						}
						const u32 blendPix = (XbrzColorDist(E, H) >= XbrzColorDist(E, D)) ? D : H;
						res = XbrzMix(res, blendPix, XbrzLeftRatio(posx, posy, ox2, oy2, dx, dy, fscale));
					}
					if (blend_y != XBRZ_BLEND_NONE)
					{
						const float dist_B_I = XbrzColorDist(B, I);
						const float dist_F_A = XbrzColorDist(F, A);
						const bool doLineBlend =
							(blend_y == XBRZ_BLEND_DOMINANT ||
								!((blend_x != XBRZ_BLEND_NONE && !XbrzPixEqual(E, I)) ||
									(blend_z != XBRZ_BLEND_NONE && !XbrzPixEqual(E, A)) ||
									(XbrzPixEqual(I, F) && XbrzPixEqual(F, C) && XbrzPixEqual(C, B) &&
										XbrzPixEqual(B, A) && !XbrzPixEqual(E, C))));
						float ox2 = INV_SQRT2, oy2 = 0.0f, dx = -1.0f, dy = -1.0f;
						if (doLineBlend)
						{
							const bool shallow =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_B_I <= dist_F_A) && E != I && H != I;
							const bool steep =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_F_A <= dist_B_I) && E != A && D != A;
							ox2 = shallow ? 0.25f : 0.5f;
							oy2 = 0.0f;
							dy -= shallow ? 1.0f : 0.0f;
							dx -= steep ? 1.0f : 0.0f;
						}
						const u32 blendPix = (XbrzColorDist(E, F) >= XbrzColorDist(E, B)) ? B : F;
						res = XbrzMix(res, blendPix, XbrzLeftRatio(posx, posy, ox2, oy2, dx, dy, fscale));
					}
					if (blend_x != XBRZ_BLEND_NONE)
					{
						const float dist_D_C = XbrzColorDist(D, C);
						const float dist_B_G = XbrzColorDist(B, G);
						const bool doLineBlend =
							(blend_x == XBRZ_BLEND_DOMINANT ||
								!((blend_w != XBRZ_BLEND_NONE && !XbrzPixEqual(E, C)) ||
									(blend_y != XBRZ_BLEND_NONE && !XbrzPixEqual(E, G)) ||
									(XbrzPixEqual(C, B) && XbrzPixEqual(B, A) && XbrzPixEqual(A, D) &&
										XbrzPixEqual(D, G) && !XbrzPixEqual(E, A))));
						float ox2 = 0.0f, oy2 = -INV_SQRT2, dx = -1.0f, dy = 1.0f;
						if (doLineBlend)
						{
							const bool shallow =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_D_C <= dist_B_G) && E != C && F != C;
							const bool steep =
								(XBRZ_STEEP_DIRECTION_THRESHOLD * dist_B_G <= dist_D_C) && E != G && H != G;
							ox2 = 0.0f;
							oy2 = shallow ? -0.25f : -0.5f;
							dx -= shallow ? 1.0f : 0.0f;
							dy += steep ? 1.0f : 0.0f;
						}
						const u32 blendPix = (XbrzColorDist(E, D) >= XbrzColorDist(E, B)) ? B : D;
						res = XbrzMix(res, blendPix, XbrzLeftRatio(posx, posy, ox2, oy2, dx, dy, fscale));
					}

					dst[static_cast<size_t>(oy) * dst_stride + ox] = res;
				}
			}
		}

		// -------------------------------------------------------------------------------
		// ScaleForce, ported from Citra/Azahar's scale_force.frag (MIT). Rather than choosing
		// between neighbours, it nudges the sampling position away from edges and then samples
		// bicubically - so it smooths without softening the edges themselves.
		//
		// DEVIATION FROM THE REFERENCE, deliberate: the shader computes its colour distance as
		// the SUM of the YCbCr components. The chroma rows of any YCbCr matrix sum to zero, so
		// that expression collapses to 0.6 * (red difference) and discards green and blue
		// entirely - a green-on-blue edge measures as zero distance. Here the distance is the
		// LENGTH of the YCbCr vector instead, which is what the surrounding code plainly means
		// and what xBRZ's ColorDist in the same codebase already does. See docs/third-party.md.
		// -------------------------------------------------------------------------------

		/// Cubic B-spline basis, matching the reference's cubic().
		inline void ScaleForceCubic(float v, float* w)
		{
			const float n0 = 1.0f - v, n1 = 2.0f - v, n2 = 3.0f - v;
			const float s0 = n0 * n0 * n0, s1 = n1 * n1 * n1, s2 = n2 * n2 * n2;
			const float x = s0;
			const float y = s1 - 4.0f * s0;
			const float z = s2 - 4.0f * s1 + 6.0f * s0;
			// (x, y, z, w) are the weights for taps at offsets -1, 0, +1, +2 in that order,
			// which is the order the caller walks them in. Assigning them in reverse mirrors the
			// reconstruction about the texel centre and slides the whole image one source texel.
			w[0] = x / 6.0f;
			w[1] = y / 6.0f;
			w[2] = z / 6.0f;
			w[3] = (6.0f - x - y - z) / 6.0f;
		}

		u32 ScaleForceSampleBicubic(const u32* src, int sw, int sh, u32 src_stride, float fx, float fy)
		{
			const float px = fx - 0.5f;
			const float py = fy - 0.5f;
			const int x0 = static_cast<int>(std::floor(px));
			const int y0 = static_cast<int>(std::floor(py));
			float wx[4], wy[4];
			ScaleForceCubic(px - static_cast<float>(x0), wx);
			ScaleForceCubic(py - static_cast<float>(y0), wy);

			float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
			for (int j = 0; j < 4; j++)
			{
				for (int i = 0; i < 4; i++)
				{
					const u32 p = SamplePixel(src, sw, sh, src_stride, x0 - 1 + i, y0 - 1 + j);
					const float w = wx[i] * wy[j];
					for (int c = 0; c < 4; c++)
						acc[c] += w * static_cast<float>((p >> (c * 8)) & 0xFF);
				}
			}

			u32 out = 0;
			for (int c = 0; c < 4; c++)
				out |= static_cast<u32>(std::clamp(static_cast<int>(acc[c] + 0.5f), 0, 255)) << (c * 8);
			return out;
		}

		/// Distance from the centre colour, weighted by both alphas so a difference behind a
		/// transparent texel does not pull the sample position around.
		inline float ScaleForceDist(u32 other, u32 centre)
		{
			const float dr = (static_cast<float>((other >> 0) & 0xFF) -
								 static_cast<float>((centre >> 0) & 0xFF)) * (1.0f / 255.0f);
			const float dg = (static_cast<float>((other >> 8) & 0xFF) -
								 static_cast<float>((centre >> 8) & 0xFF)) * (1.0f / 255.0f);
			const float db = (static_cast<float>((other >> 16) & 0xFF) -
								 static_cast<float>((centre >> 16) & 0xFF)) * (1.0f / 255.0f);
			const float oa = static_cast<float>((other >> 24) & 0xFF) * (1.0f / 255.0f);
			const float ca = static_cast<float>((centre >> 24) & 0xFF) * (1.0f / 255.0f);
			const float da = oa - ca;

			constexpr float KR = 0.2627f, KG = 0.6780f, KB = 0.0593f;
			constexpr float LW = 0.6f;
			const float y = dr * KR * LW + dg * KG * LW + db * KB * LW;
			const float cb = dr * (-0.5f * KR / (1.0f - KB)) + dg * (-0.5f * KG / (1.0f - KB)) + db * 0.5f;
			const float cr = dr * 0.5f + dg * (-0.5f * KG / (1.0f - KR)) + db * (-0.5f * KB / (1.0f - KR));

			// Same uniformly-transparent guard as XbrzColorDist; without it a PSMCT24 texture
			// expanded with TA0 = 0 makes every distance zero, ScaleForce takes its
			// total_dist <= 0 early-out for every pixel, and the whole texture passes through
			// unfiltered.
			const float aw = (oa <= 0.0f && ca <= 0.0f) ? 1.0f : (oa * ca);
			const float d = std::sqrt(y * y + cb * cb + cr * cr);
			return std::sqrt((d * d + std::fabs(da)) * aw);
		}

		void ScaleForceFilter(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride,
			u32 scale)
		{
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			const float fscale = static_cast<float>(scale);

			for (int oy = 0; oy < dh; oy++)
			{
				for (int ox = 0; ox < dw; ox++)
				{
					const float tx = (static_cast<float>(ox) + 0.5f) / fscale;
					const float ty = (static_cast<float>(oy) + 0.5f) / fscale;
					const int bx = static_cast<int>(std::floor(tx));
					const int by = static_cast<int>(std::floor(ty));

					// Named as the reference names them, where +y is UP; this buffer has +y
					// down, hence the negated row offsets.
					const auto at = [&](int dx, int dy) -> u32 {
						return SamplePixel(src, sw, sh, src_stride, bx + dx, by - dy);
					};

					const u32 cc = at(0, 0);
					const u32 tl = at(-1, 1), tc = at(0, 1), tr = at(1, 1);
					const u32 cl = at(-1, 0), cr = at(1, 0);
					const u32 bl = at(-1, -1), bc = at(0, -1), br = at(1, -1);

					const float o_tl[4] = {ScaleForceDist(tl, cc), ScaleForceDist(tc, cc),
						ScaleForceDist(tr, cc), ScaleForceDist(cr, cc)};
					const float o_br[4] = {ScaleForceDist(br, cc), ScaleForceDist(bc, cc),
						ScaleForceDist(bl, cc), ScaleForceDist(cl, cc)};

					float total_dist = 0.0f;
					for (int i = 0; i < 4; i++)
						total_dist += o_tl[i] + o_br[i];

					if (total_dist <= 0.0f)
					{
						// Bicubic just past an edge where the offset is zero produces black
						// floaters, and with no colour change the filter choice is moot anyway.
						dst[static_cast<size_t>(oy) * dst_stride + ox] = cc;
						continue;
					}

					float tmp[4];
					for (int i = 0; i < 4; i++)
						tmp[i] = o_tl[i] - o_br[i];

					// total_offset = tmp.wy + tmp.zz + vec2(-tmp.x, tmp.x)
					float offx = tmp[3] + tmp[2] - tmp[0];
					float offy = tmp[1] + tmp[2] + tmp[0];

					// Thin features split apart when the offset reaches into clear areas; this
					// keeps it bounded, exactly as the reference does.
					const float clamp_val = std::sqrt(offx * offx + offy * offy) / total_dist;
					offx = std::clamp(offx, -clamp_val, clamp_val);
					offy = std::clamp(offy, -clamp_val, clamp_val);

					// The offset is in the reference's +y-up space, so subtracting it there is
					// adding it here.
					dst[static_cast<size_t>(oy) * dst_stride + ox] =
						ScaleForceSampleBicubic(src, sw, sh, src_stride, tx - offx, ty + offy);
				}
			}
		}

		// -------------------------------------------------------------------------------
		// Deposterize, ported from PPSSPP's TextureScalerCommon.cpp (GPLv2-or-later).
		//
		// A PRE-pass, not a filter: it removes the stair-stepping that low-bit-depth sources
		// leave in gradients, before any scaler runs. Without it a good scaler faithfully
		// preserves the banding and then makes it bigger.
		//
		// Especially apt on PS2, where PSMCT16 is 5:5:5:1 - posterised gradients are the norm
		// there, not an occasional artefact.
		// -------------------------------------------------------------------------------

		/// One separable half-pass. `horizontal` picks the neighbour axis.
		void DeposterizePass(const std::vector<u32>& in, std::vector<u32>& out, int w, int h, bool horizontal)
		{
			// Only a step of this size or less counts as banding. Anything larger is a real
			// edge and must survive untouched - which is what separates this from a blur.
			constexpr int T = 8;

			out.resize(in.size());
			for (int y = 0; y < h; y++)
			{
				for (int x = 0; x < w; x++)
				{
					const size_t idx = static_cast<size_t>(y) * w + x;
					const u32 centre = in[idx];

					const bool at_edge = horizontal ? (x == 0 || x == w - 1) : (y == 0 || y == h - 1);
					if (at_edge)
					{
						out[idx] = centre;
						continue;
					}

					const u32 a = horizontal ? in[idx - 1] : in[idx - static_cast<size_t>(w)];
					const u32 b = horizontal ? in[idx + 1] : in[idx + static_cast<size_t>(w)];

					u32 result = 0;
					for (int c = 0; c < 4; c++)
					{
						const int shift = c * 8;
						const int ac = static_cast<int>((a >> shift) & 0xFF);
						const int cc = static_cast<int>((centre >> shift) & 0xFF);
						const int bc = static_cast<int>((b >> shift) & 0xFF);

						// Only interpolate where the centre already equals one neighbour and the
						// other is within a hair of it: that is the signature of a quantisation
						// step, as opposed to a genuine two-colour boundary.
						const bool banding = (ac != bc) && ((ac == cc && std::abs(bc - cc) <= T) ||
															   (bc == cc && std::abs(ac - cc) <= T));
						result |= static_cast<u32>(banding ? ((bc + ac) / 2) : cc) << shift;
					}
					out[idx] = result;
				}
			}
		}

		/// H, V, H, V - two full separable passes, matching PPSSPP. One pass alone leaves
		/// diagonal banding visibly untouched.
		void Deposterize(std::vector<u32>& px, int w, int h)
		{
			std::vector<u32> tmp;
			DeposterizePass(px, tmp, w, h, true);
			DeposterizePass(tmp, px, w, h, false);
			DeposterizePass(px, tmp, w, h, true);
			DeposterizePass(tmp, px, w, h, false);
		}

		inline float Sinc(float x)
		{
			if (std::fabs(x) < 1e-6f)
				return 1.0f;
			const float px = 3.14159265358979f * x;
			return std::sin(px) / px;
		}

		/// Lanczos-3. Sharpest of the resamplers; the trade is ringing on hard edges, which is
		/// exactly why it is offered next to bicubic rather than instead of it.
		void ScaleLanczos(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride, u32 scale)
		{
			constexpr int A = 3;
			const int dw = sw * static_cast<int>(scale);
			const int dh = sh * static_cast<int>(scale);
			const float inv = 1.0f / static_cast<float>(scale);

			for (int y = 0; y < dh; y++)
			{
				const float sy = (static_cast<float>(y) + 0.5f) * inv - 0.5f;
				const int y0 = static_cast<int>(std::floor(sy));
				float wy[2 * A];
				float sum_y = 0.0f;
				for (int j = 0; j < 2 * A; j++)
				{
					const float d = sy - static_cast<float>(y0 - A + 1 + j);
					wy[j] = Sinc(d) * Sinc(d / static_cast<float>(A));
					sum_y += wy[j];
				}

				for (int x = 0; x < dw; x++)
				{
					const float sx = (static_cast<float>(x) + 0.5f) * inv - 0.5f;
					const int x0 = static_cast<int>(std::floor(sx));
					float wx[2 * A];
					float sum_x = 0.0f;
					for (int i = 0; i < 2 * A; i++)
					{
						const float d = sx - static_cast<float>(x0 - A + 1 + i);
						wx[i] = Sinc(d) * Sinc(d / static_cast<float>(A));
						sum_x += wx[i];
					}

					// Normalising by the actual weight sum keeps the result energy-preserving
					// even where the kernel is clipped at the texture edge.
					const float norm = (sum_x * sum_y);
					const float inv_norm = (std::fabs(norm) > 1e-6f) ? (1.0f / norm) : 1.0f;

					float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
					for (int j = 0; j < 2 * A; j++)
					{
						for (int i = 0; i < 2 * A; i++)
						{
							const u32 p = SamplePixel(src, sw, sh, src_stride, x0 - A + 1 + i, y0 - A + 1 + j);
							const float w = wx[i] * wy[j];
							for (int c = 0; c < 4; c++)
								acc[c] += w * static_cast<float>((p >> (c * 8)) & 0xFF);
						}
					}

					u32 out = 0;
					for (int c = 0; c < 4; c++)
					{
						const int v = static_cast<int>(acc[c] * inv_norm + 0.5f);
						out |= static_cast<u32>(std::clamp(v, 0, 255)) << (c * 8);
					}
					dst[static_cast<size_t>(y) * dst_stride + static_cast<size_t>(x)] = out;
				}
			}
		}

		/// Contrast-adaptive sharpen, applied in place over an already-scaled image.
		///
		/// Follows AMD's CAS idea rather than a fixed unsharp mask: the sharpening amount is
		/// derived per pixel from local contrast, so flat areas are left alone and already-hard
		/// edges do not get haloed.
		void ApplyCAS(u32* img, int w, int h, u32 stride, float strength)
		{
			std::vector<u32> copy(static_cast<size_t>(w) * static_cast<size_t>(h));
			for (int y = 0; y < h; y++)
				for (int x = 0; x < w; x++)
					copy[static_cast<size_t>(y) * w + x] = img[static_cast<size_t>(y) * stride + x];

			const auto at = [&](int x, int y) -> u32 {
				x = std::clamp(x, 0, w - 1);
				y = std::clamp(y, 0, h - 1);
				return copy[static_cast<size_t>(y) * w + x];
			};

			for (int y = 0; y < h; y++)
			{
				for (int x = 0; x < w; x++)
				{
					const u32 e = at(x, y);
					const u32 n = at(x, y - 1);
					const u32 s = at(x, y + 1);
					const u32 wv = at(x - 1, y);
					const u32 ev = at(x + 1, y);

					u32 out = 0;
					for (int c = 0; c < 3; c++) // alpha deliberately untouched
					{
						const int shift = c * 8;
						const int ce = (e >> shift) & 0xFF;
						const int cn = (n >> shift) & 0xFF;
						const int cs = (s >> shift) & 0xFF;
						const int cw = (wv >> shift) & 0xFF;
						const int cev = (ev >> shift) & 0xFF;

						const int mn = std::min({ce, cn, cs, cw, cev});
						const int mx = std::max({ce, cn, cs, cw, cev});
						// Less headroom to the nearest clip point means less sharpening, which
						// is what stops CAS from ringing where a plain unsharp mask would.
						const float amp = std::sqrt(std::min(static_cast<float>(mn), 255.0f - static_cast<float>(mx)) / 255.0f);
						const float wgt = -amp * strength;
						const float denom = 1.0f + 4.0f * wgt;
						const float v = (static_cast<float>(ce) + wgt * static_cast<float>(cn + cs + cw + cev)) /
										((std::fabs(denom) > 1e-6f) ? denom : 1.0f);
						out |= static_cast<u32>(std::clamp(static_cast<int>(v + 0.5f), 0, 255)) << shift;
					}
					out |= (e & 0xFF000000u);
					img[static_cast<size_t>(y) * stride + x] = out;
				}
			}
		}

		// ---------------------------------------------------------------------------------
		// Edge-directed family. All of these are 2x passes; 4x is the pass applied twice.
		// ---------------------------------------------------------------------------------

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

		// Kreed's 2xSaI family. These three share a 4x4 window and the same two tie-break
		// helpers; the differences are in how aggressively they blend.
		//
		//     I E F J
		//     G A B K
		//     H C D L
		//     M N O P
		//
		// A is the pixel being expanded into the 2x2 output.

		inline int SaIResult1(u32 a, u32 b, u32 c, u32 d)
		{
			int x = 0, y = 0, r = 0;
			if (a == c) x++; else if (b == c) y++;
			if (a == d) x++; else if (b == d) y++;
			if (x <= 1) r++;
			if (y <= 1) r--;
			return r;
		}

		inline int SaIResult2(u32 a, u32 b, u32 c, u32 d)
		{
			int x = 0, y = 0, r = 0;
			if (a == c) x++; else if (b == c) y++;
			if (a == d) x++; else if (b == d) y++;
			if (x <= 1) r--;
			if (y <= 1) r++;
			return r;
		}

		void Pass2xSaI(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 I = SamplePixel(src, sw, sh, src_stride, x - 1, y - 1);
					const u32 E = SamplePixel(src, sw, sh, src_stride, x, y - 1);
					const u32 F = SamplePixel(src, sw, sh, src_stride, x + 1, y - 1);
					const u32 J = SamplePixel(src, sw, sh, src_stride, x + 2, y - 1);
					const u32 G = SamplePixel(src, sw, sh, src_stride, x - 1, y);
					const u32 A = SamplePixel(src, sw, sh, src_stride, x, y);
					const u32 B = SamplePixel(src, sw, sh, src_stride, x + 1, y);
					const u32 K = SamplePixel(src, sw, sh, src_stride, x + 2, y);
					const u32 H = SamplePixel(src, sw, sh, src_stride, x - 1, y + 1);
					const u32 C = SamplePixel(src, sw, sh, src_stride, x, y + 1);
					const u32 D = SamplePixel(src, sw, sh, src_stride, x + 1, y + 1);
					const u32 L = SamplePixel(src, sw, sh, src_stride, x + 2, y + 1);
					const u32 M = SamplePixel(src, sw, sh, src_stride, x - 1, y + 2);
					const u32 N = SamplePixel(src, sw, sh, src_stride, x, y + 2);
					const u32 O = SamplePixel(src, sw, sh, src_stride, x + 1, y + 2);

					u32 product, product1, product2;

					if (A == D && B != C)
					{
						product = ((A == E && B == L) || (A == C && A == F && B != E && B == J)) ? A : Mix2(A, B);
						product1 = ((A == G && C == O) || (A == B && A == H && G != C && C == M)) ? A : Mix2(A, C);
						product2 = A;
					}
					else if (B == C && A != D)
					{
						product = ((B == F && A == H) || (B == E && B == D && A != F && A == I)) ? B : Mix2(A, B);
						product1 = ((C == H && A == F) || (C == G && C == D && A != H && A == I)) ? C : Mix2(A, C);
						product2 = B;
					}
					else if (A == D && B == C)
					{
						if (A == B)
						{
							product = A;
							product1 = A;
							product2 = A;
						}
						else
						{
							product = Mix2(A, B);
							product1 = Mix2(A, C);
							int r = 0;
							r += SaIResult1(A, B, G, E);
							r += SaIResult2(B, A, K, F);
							r += SaIResult2(B, A, H, N);
							r += SaIResult1(A, B, L, O);
							if (r > 0)
								product2 = A;
							else if (r < 0)
								product2 = B;
							else
								product2 = Mix4(A, B, C, D);
						}
					}
					else
					{
						product2 = Mix4(A, B, C, D);
						if (A == C && A == F && B != E && B == J)
							product = A;
						else if (B == E && B == D && A != F && A == I)
							product = B;
						else
							product = Mix2(A, B);

						if (A == B && A == H && G != C && C == M)
							product1 = A;
						else if (C == G && C == D && A != H && A == I)
							product1 = C;
						else
							product1 = Mix2(A, C);
					}

					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = A;
					out[1] = product;
					out[dst_stride] = product1;
					out[dst_stride + 1] = product2;
				}
			}
		}

		void PassSuper2xSaI(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 I = SamplePixel(src, sw, sh, src_stride, x - 1, y - 1);
					const u32 E = SamplePixel(src, sw, sh, src_stride, x, y - 1);
					const u32 F = SamplePixel(src, sw, sh, src_stride, x + 1, y - 1);
					const u32 J = SamplePixel(src, sw, sh, src_stride, x + 2, y - 1);
					const u32 G = SamplePixel(src, sw, sh, src_stride, x - 1, y);
					const u32 A = SamplePixel(src, sw, sh, src_stride, x, y);
					const u32 B = SamplePixel(src, sw, sh, src_stride, x + 1, y);
					const u32 K = SamplePixel(src, sw, sh, src_stride, x + 2, y);
					const u32 H = SamplePixel(src, sw, sh, src_stride, x - 1, y + 1);
					const u32 C = SamplePixel(src, sw, sh, src_stride, x, y + 1);
					const u32 D = SamplePixel(src, sw, sh, src_stride, x + 1, y + 1);
					const u32 L = SamplePixel(src, sw, sh, src_stride, x + 2, y + 1);
					const u32 M = SamplePixel(src, sw, sh, src_stride, x - 1, y + 2);
					const u32 N = SamplePixel(src, sw, sh, src_stride, x, y + 2);
					const u32 O = SamplePixel(src, sw, sh, src_stride, x + 1, y + 2);
					const u32 P = SamplePixel(src, sw, sh, src_stride, x + 2, y + 2);

					// Top-left of the output block: biased by the pixels above and left.
					u32 e0;
					if (A == C && A == F && B != E && B == J)
						e0 = A;
					else if (B == E && B == D && A != F && A == I)
						e0 = B;
					else
						e0 = Mix2(A, B);

					u32 e1;
					if (A == B && A == H && G != C && C == M)
						e1 = A;
					else if (C == G && C == D && A != H && A == I)
						e1 = C;
					else
						e1 = Mix2(A, C);

					u32 e2 = A;
					u32 e3;
					if (A == D && B != C)
					{
						if ((A == E && B == L) || (A == C && A == F && B != E && B == J))
							e3 = A;
						else
							e3 = Mix2(A, B);
					}
					else if (B == C && A != D)
					{
						if ((B == F && A == H) || (B == E && B == D && A != F && A == I))
							e3 = B;
						else
							e3 = Mix2(A, B);
					}
					else if (A == D && B == C)
					{
						if (A == B)
						{
							e3 = A;
						}
						else
						{
							int r = 0;
							r += SaIResult1(A, B, G, E);
							r += SaIResult2(B, A, K, F);
							r += SaIResult2(B, A, H, N);
							r += SaIResult1(A, B, L, O);
							if (r > 0)
								e3 = A;
							else if (r < 0)
								e3 = B;
							else
								e3 = Mix4(A, B, C, D);
						}
					}
					else
					{
						e3 = Mix4(A, B, C, D);
					}

					// P participates only through the four-way average above; referencing it
					// here keeps the window read explicit and the compiler quiet.
					(void)P;

					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = e2;
					out[1] = e0;
					out[dst_stride] = e1;
					out[dst_stride + 1] = e3;
				}
			}
		}

		void PassSuperEagle(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 E = SamplePixel(src, sw, sh, src_stride, x, y - 1);
					const u32 F = SamplePixel(src, sw, sh, src_stride, x + 1, y - 1);
					const u32 G = SamplePixel(src, sw, sh, src_stride, x - 1, y);
					const u32 A = SamplePixel(src, sw, sh, src_stride, x, y);
					const u32 B = SamplePixel(src, sw, sh, src_stride, x + 1, y);
					const u32 K = SamplePixel(src, sw, sh, src_stride, x + 2, y);
					const u32 H = SamplePixel(src, sw, sh, src_stride, x - 1, y + 1);
					const u32 C = SamplePixel(src, sw, sh, src_stride, x, y + 1);
					const u32 D = SamplePixel(src, sw, sh, src_stride, x + 1, y + 1);
					const u32 L = SamplePixel(src, sw, sh, src_stride, x + 2, y + 1);
					const u32 N = SamplePixel(src, sw, sh, src_stride, x, y + 2);
					const u32 O = SamplePixel(src, sw, sh, src_stride, x + 1, y + 2);

					u32 e0, e1, e2, e3;

					if (A == D)
					{
						if (B != C)
						{
							// Diagonal runs from top-left to bottom-right: bias the off-diagonal
							// corners toward the run so the staircase reads as a line.
							e0 = A;
							e3 = A;
							e1 = (A == E && A == H) ? A : ((A == E) ? BlendWeighted(A, B, 3, 1) : ((A == H) ? BlendWeighted(A, C, 3, 1) : Mix2(A, B)));
							e2 = (A == O && A == K) ? A : ((A == K) ? BlendWeighted(A, B, 3, 1) : ((A == O) ? BlendWeighted(A, C, 3, 1) : Mix2(A, C)));
						}
						else
						{
							int r = 0;
							r += SaIResult1(A, B, G, E);
							r += SaIResult2(B, A, K, F);
							r += SaIResult2(B, A, H, N);
							r += SaIResult1(A, B, L, O);
							if (r > 0)
							{
								e0 = A; e1 = A; e2 = A; e3 = A;
							}
							else if (r < 0)
							{
								e0 = B; e1 = B; e2 = B; e3 = B;
							}
							else
							{
								const u32 m = Mix2(A, B);
								e0 = m; e1 = m; e2 = m; e3 = m;
							}
						}
					}
					else if (B == C)
					{
						// The other diagonal.
						e1 = B;
						e2 = B;
						e0 = (B == E && B == G) ? B : Mix2(A, B);
						e3 = (B == L && B == N) ? B : Mix2(C, D);
					}
					else
					{
						e0 = Mix2(Mix2(A, B), Mix2(A, C));
						e1 = Mix2(Mix2(B, A), Mix2(B, D));
						e2 = Mix2(Mix2(C, A), Mix2(C, D));
						e3 = Mix2(Mix2(D, B), Mix2(D, C));
					}

					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = e0;
					out[1] = e1;
					out[dst_stride] = e2;
					out[dst_stride + 1] = e3;
				}
			}
		}

		/// xBR, 2x. Unlike the SaI family's equality tests, this scores each diagonal with a
		/// colour distance, so it survives gradients and anti-aliased source art rather than
		/// only exact-match pixel art.
		void PassXbr(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			// Weight of the "against" term when deciding whether a diagonal wins. 2:1 is
			// Hyllian's constant and it is what stops thin features being eaten.
			constexpr int WEIGHT = 2;

			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 E = SamplePixel(src, sw, sh, src_stride, x, y);

					// The 3x3 ring, plus the four "outer" samples each diagonal test needs.
					const u32 A = SamplePixel(src, sw, sh, src_stride, x - 1, y - 1);
					const u32 B = SamplePixel(src, sw, sh, src_stride, x, y - 1);
					const u32 C = SamplePixel(src, sw, sh, src_stride, x + 1, y - 1);
					const u32 D = SamplePixel(src, sw, sh, src_stride, x - 1, y);
					const u32 F = SamplePixel(src, sw, sh, src_stride, x + 1, y);
					const u32 G = SamplePixel(src, sw, sh, src_stride, x - 1, y + 1);
					const u32 H = SamplePixel(src, sw, sh, src_stride, x, y + 1);
					const u32 I = SamplePixel(src, sw, sh, src_stride, x + 1, y + 1);

					const u32 B1 = SamplePixel(src, sw, sh, src_stride, x, y - 2);
					const u32 C1 = SamplePixel(src, sw, sh, src_stride, x + 1, y - 2);
					const u32 D0 = SamplePixel(src, sw, sh, src_stride, x - 2, y);
					const u32 G0 = SamplePixel(src, sw, sh, src_stride, x - 2, y + 1);
					const u32 F4 = SamplePixel(src, sw, sh, src_stride, x + 2, y);
					const u32 I4 = SamplePixel(src, sw, sh, src_stride, x + 2, y + 1);
					const u32 H5 = SamplePixel(src, sw, sh, src_stride, x, y + 2);
					const u32 I5 = SamplePixel(src, sw, sh, src_stride, x + 1, y + 2);

					u32 e0 = E, e1 = E, e2 = E, e3 = E;

					// Each corner asks the same question rotated: does a diagonal edge pass
					// through here, and if so which neighbour should bleed into the corner?
					const auto corner = [&](u32 e, u32 i, u32 h, u32 f, u32 g, u32 c, u32 d, u32 b,
											 u32 f4, u32 i4, u32 h5, u32 i5) -> u32 {
						const int weighted = WEIGHT * ColorDistance(e, i);
						const int against = ColorDistance(h, d) + ColorDistance(h, f4) + ColorDistance(i5, i4) +
											ColorDistance(i5, h5) + ColorDistance(f, i);
						const int forward = ColorDistance(e, g) + ColorDistance(e, c) + ColorDistance(i, f4) +
											ColorDistance(i, h5) + WEIGHT * ColorDistance(h, f);
						if (weighted + against < forward)
						{
							// Blend toward whichever of the two orthogonal neighbours is closer
							// to E, so the new pixel stays on the same surface.
							const u32 pick = (ColorDistance(e, f) <= ColorDistance(e, h)) ? f : h;
							(void)b;
							return BlendWeighted(e, pick, 3, 1);
						}
						return e;
					};

					e3 = corner(E, I, H, F, G, C, D, B, F4, I4, H5, I5);
					e1 = corner(E, C, F, B, I, A, H, D, B1, C1, D0, G0);
					e2 = corner(E, G, D, H, A, I, B, F, H5, G0, B1, D0);
					e0 = corner(E, A, B, D, C, G, F, H, D0, B1, C1, G0);

					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = e0;
					out[1] = e1;
					out[dst_stride] = e2;
					out[dst_stride + 1] = e3;
				}
			}
		}

		/// MMPX, ported from the GLSL texture filter in Citra/Azahar (GPLv2-or-later), itself
		/// an implementation of McGuire & Barr-Brisebois' style-preserving magnification.
		///
		/// Unlike the SaI family this is a rule table over exact-colour matches, and unlike xBR
		/// it never invents a colour outside the source palette beyond a single 50% blend - which
		/// is what "style preserving" means and why it holds up on small sprites and text where
		/// the smoother filters turn letterforms to mush.
		void PassMMPX(const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			// Weighted by (1 - alpha) exactly as the reference does, which is worth stating
			// plainly because it is counter-intuitive: an OPAQUE texel scores 0 and a fully
			// transparent one scores its full colour luma. The luma comparisons below therefore
			// order by transparency first and brightness second. Kept as-is to match MMPX, but
			// do not read these as brightness tests.
			const auto luma = [](u32 c) -> float {
				const float r = static_cast<float>((c >> 0) & 0xFF) * (1.0f / 255.0f);
				const float g = static_cast<float>((c >> 8) & 0xFF) * (1.0f / 255.0f);
				const float b = static_cast<float>((c >> 16) & 0xFF) * (1.0f / 255.0f);
				const float a = static_cast<float>((c >> 24) & 0xFF) * (1.0f / 255.0f);
				return (0.2126f * r + 0.7152f * g + 0.0722f * b) * (1.0f - a);
			};

			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const auto at = [&](int dx, int dy) -> u32 {
						return SamplePixel(src, sw, sh, src_stride, x + dx, y + dy);
					};

					const u32 E = at(0, 0);
					const u32 A = at(-1, -1), B = at(0, -1), C = at(1, -1);
					const u32 D = at(-1, 0), F = at(1, 0);
					const u32 G = at(-1, 1), H = at(0, 1), I = at(1, 1);

					u32 J = E, K = E, L = E, M = E;

					const bool flat = (E == A && E == B && E == C && E == D && E == F && E == G &&
									   E == H && E == I);
					if (!flat)
					{
						// P and S are both (0,2) here, matching the Citra shader exactly rather
						// than the paper. Reproduced deliberately: this is the behaviour Azahar
						// ships and what people have actually looked at, and silently "fixing" it
						// would make this filter differ from the reference it claims to be.
						const u32 P = at(0, 2), Q = at(-2, 0), R = at(2, 0), S = at(0, 2);

						const float Bl = luma(B), Dl = luma(D), El = luma(E), Fl = luma(F), Hl = luma(H);

						const auto any_eq3 = [](u32 b, u32 a0, u32 a1, u32 a2) {
							return b == a0 || b == a1 || b == a2;
						};
						const auto all_eq2 = [](u32 b, u32 a0, u32 a1) { return b == a0 && b == a1; };
						const auto all_eq3 = [](u32 b, u32 a0, u32 a1, u32 a2) {
							return b == a0 && b == a1 && b == a2;
						};
						const auto all_eq4 = [](u32 b, u32 a0, u32 a1, u32 a2, u32 a3) {
							return b == a0 && b == a1 && b == a2 && b == a3;
						};
						const auto none_eq2 = [](u32 b, u32 a0, u32 a1) { return b != a0 && b != a1; };
						const auto none_eq4 = [](u32 b, u32 a0, u32 a1, u32 a2, u32 a3) {
							return b != a0 && b != a1 && b != a2 && b != a3;
						};

						if ((D == B && D != H && D != F) && ((El >= Dl) || E == A) && any_eq3(E, A, C, G) &&
							((El < Dl) || A != D || E != P || E != Q))
							J = Mix2(D, J);
						if ((B == F && B != D && B != H) && ((El >= Bl) || E == C) && any_eq3(E, A, C, I) &&
							((El < Bl) || C != B || E != P || E != R))
							K = Mix2(B, K);
						if ((H == D && H != F && H != B) && ((El >= Hl) || E == G) && any_eq3(E, A, G, I) &&
							((El < Hl) || G != H || E != S || E != Q))
							L = Mix2(H, L);
						if ((F == H && F != B && F != D) && ((El >= Fl) || E == I) && any_eq3(E, C, G, I) &&
							((El < Fl) || I != H || E != R || E != S))
							M = Mix2(F, M);

						if ((E != F && all_eq4(E, C, I, D, Q) && all_eq2(F, B, H)) && F != at(3, 0))
						{
							M = Mix2(M, F);
							K = Mix2(K, M);
						}
						if ((E != D && all_eq4(E, A, G, F, R) && all_eq2(D, B, H)) && D != at(-3, 0))
						{
							L = Mix2(L, D);
							J = Mix2(J, L);
						}
						if ((E != H && all_eq4(E, G, I, B, P) && all_eq2(H, D, F)) && H != at(0, 3))
						{
							M = Mix2(M, H);
							L = Mix2(L, M);
						}
						if ((E != B && all_eq4(E, A, C, H, S) && all_eq2(B, D, F)) && B != at(0, -3))
						{
							K = Mix2(K, B);
							J = Mix2(J, K);
						}

						if ((Bl < El) && all_eq4(E, G, H, I, S) && none_eq4(E, A, D, C, F))
						{
							K = Mix2(K, B);
							J = Mix2(J, K);
						}
						if ((Hl < El) && all_eq4(E, A, B, C, P) && none_eq4(E, D, G, I, F))
						{
							M = Mix2(M, H);
							L = Mix2(L, M);
						}
						if ((Fl < El) && all_eq4(E, A, D, G, Q) && none_eq4(E, B, C, I, H))
						{
							M = Mix2(M, F);
							K = Mix2(K, M);
						}
						if ((Dl < El) && all_eq4(E, C, F, I, R) && none_eq4(E, B, A, G, H))
						{
							L = Mix2(L, D);
							J = Mix2(J, L);
						}

						if (H != B)
						{
							if (H != A && H != E && H != C)
							{
								if (all_eq3(H, G, F, R) && none_eq2(H, D, at(2, -1)))
									L = Mix2(M, L);
								if (all_eq3(H, I, D, Q) && none_eq2(H, F, at(-2, -1)))
									M = Mix2(L, M);
							}
							if (B != I && B != G && B != E)
							{
								if (all_eq3(B, A, F, R) && none_eq2(B, D, at(2, 1)))
									J = Mix2(K, L);
								if (all_eq3(B, C, D, Q) && none_eq2(B, F, at(-2, 1)))
									K = Mix2(J, K);
							}
						}

						if (F != D)
						{
							if (D != I && D != E && D != C)
							{
								if (all_eq3(D, A, H, S) && none_eq2(D, B, at(1, 2)))
									J = Mix2(L, J);
								if (all_eq3(D, G, B, P) && none_eq2(D, H, at(1, 2)))
									L = Mix2(J, L);
							}
							if (F != E && F != A && F != G)
							{
								if (all_eq3(F, C, H, S) && none_eq2(F, B, at(-1, 2)))
									K = Mix2(M, K);
								if (all_eq3(F, I, B, P) && none_eq2(F, H, at(-1, -2)))
									M = Mix2(K, M);
							}
						}
					}

					// J top-left, K top-right, L bottom-left, M bottom-right.
					u32* out = dst + (static_cast<size_t>(y) * 2) * dst_stride + (static_cast<size_t>(x) * 2);
					out[0] = J;
					out[1] = K;
					out[dst_stride] = L;
					out[dst_stride + 1] = M;
				}
			}
		}

		using PassFn = void (*)(const u32*, int, int, u32, u32*, u32);

		PassFn PassForAlgorithm(GSTextureUpscaleAlgorithm algorithm)
		{
			switch (algorithm)
			{
				case GSTextureUpscaleAlgorithm::Scale2x: return PassScale2x;
				case GSTextureUpscaleAlgorithm::Eagle: return PassEagle;
				case GSTextureUpscaleAlgorithm::SaI2x: return Pass2xSaI;
				case GSTextureUpscaleAlgorithm::SuperSaI2x: return PassSuper2xSaI;
				case GSTextureUpscaleAlgorithm::SuperEagle: return PassSuperEagle;
				case GSTextureUpscaleAlgorithm::xBR: return PassXbr;
				case GSTextureUpscaleAlgorithm::MMPX: return PassMMPX;
				default: return nullptr;
			}
		}
	} // namespace

	bool IsAlgorithmImplemented(GSTextureUpscaleAlgorithm algorithm)
	{
		// The enum lists the whole intended library so the menu and the config format are
		// stable from the start. Anything without a kernel declines and the texture stays
		// native. See docs/texture-upscaling-research.md for what is left.
		switch (algorithm)
		{
			case GSTextureUpscaleAlgorithm::Bilinear:
			case GSTextureUpscaleAlgorithm::Bicubic:
			case GSTextureUpscaleAlgorithm::Lanczos:
			case GSTextureUpscaleAlgorithm::LanczosCAS:
			case GSTextureUpscaleAlgorithm::Scale2x:
			case GSTextureUpscaleAlgorithm::Eagle:
			case GSTextureUpscaleAlgorithm::SuperEagle:
			case GSTextureUpscaleAlgorithm::SaI2x:
			case GSTextureUpscaleAlgorithm::SuperSaI2x:
			case GSTextureUpscaleAlgorithm::xBR:
			case GSTextureUpscaleAlgorithm::MMPX:
			case GSTextureUpscaleAlgorithm::xBRZ:
			case GSTextureUpscaleAlgorithm::ScaleForce:
			case GSTextureUpscaleAlgorithm::Anime4K:
			case GSTextureUpscaleAlgorithm::Nearest:
			case GSTextureUpscaleAlgorithm::Mitchell:
			case GSTextureUpscaleAlgorithm::SharpBilinear:
				return true;

			// Architecture is present; whether it can actually run depends on a model file
			// being installed, which ScaleBuffer answers per texture. Reported implemented so
			// the picker offers them and the user is told what is missing, rather than the
			// entries silently not existing.
			case GSTextureUpscaleAlgorithm::FSRCNN:
			case GSTextureUpscaleAlgorithm::SESR:
			case GSTextureUpscaleAlgorithm::ESPCN:
				return true;

			// Kernels ship in the APK, so this one really does run out of the box; a missing
			// or damaged kernel file declines per texture the same way a missing model does.
			case GSTextureUpscaleAlgorithm::RaisrHD:
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
		{
			s_stats.declined_class_disabled++;
			return plan;
		}

		plan.algorithm = (plan.texture_class == TextureClass::Ui) ? GSConfig.TextureUpscaleUiAlgorithm :
																	GSConfig.TextureUpscaleWorldAlgorithm;
		if (!IsAlgorithmImplemented(plan.algorithm))
		{
			s_stats.declined_unimplemented++;
			return plan;
		}

		if (s_upscales_this_frame >= MAX_UPSCALES_PER_FRAME)
		{
			s_stats.declined_rate_limit++;
			return plan;
		}

		if (s_declined.find(tex0_hash) != s_declined.end())
		{
			s_stats.declined_budget++;
			return plan;
		}

		const u32 scale = ClampScale((plan.texture_class == TextureClass::Ui) ?
										 GSConfig.TextureUpscaleUiScale :
										 GSConfig.TextureUpscaleWorldScale);

		// Refuse before allocating rather than after: going over and then evicting is how a
		// busy scene ends up pinned at the ceiling.
		const u64 projected = static_cast<u64>(tw) * static_cast<u64>(th) * scale * scale * 4ull;
		if (static_cast<u64>(s_memory_usage) + projected > static_cast<u64>(GetBudgetBytes()))
		{
			s_declined.insert(tex0_hash);
			s_stats.declined_budget++;
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
		u8* dst, u32 dst_pitch, u8 scale, TextureClass texture_class)
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

			case GSTextureUpscaleAlgorithm::Bicubic:
				ScaleBicubic(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::Nearest:
				ScaleNearest(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::Mitchell:
				ScaleMitchell(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::SharpBilinear:
				ScaleSharpBilinear(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::Anime4K:
				ScaleAnime4K(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::xBRZ:
				ScaleXbrz(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::ScaleForce:
				ScaleForceFilter(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::Lanczos:
				ScaleLanczos(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				return true;

			case GSTextureUpscaleAlgorithm::LanczosCAS:
				ScaleLanczos(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
				// 0.6 sits between AMD's "sharpen a little" and "sharpen a lot" stops. Applied
				// after resampling on purpose - sharpening the source first would magnify its
				// own aliasing along with the detail.
				ApplyCAS(dst_px, sw * scale, sh * scale, dst_stride, 0.6f);
				return true;

			case GSTextureUpscaleAlgorithm::FSRCNN:
			case GSTextureUpscaleAlgorithm::SESR:
			case GSTextureUpscaleAlgorithm::ESPCN:
			{
				if (GSTextureUpscalerNN::Run(algorithm, src_px, sw, sh, src_stride, dst_px, dst_stride, scale))
					return true;
				// No model installed, or the texture is outside the size one will be run on.
				// Counted so the OSD can say which, instead of the user seeing nothing happen.
				s_stats.declined_no_model++;
				return false;
			}

			case GSTextureUpscaleAlgorithm::RaisrHD:
			{
				if (GSTextureUpscalerRaisr::Run(texture_class, src_px, sw, sh, src_stride, dst_px, dst_stride, scale))
					return true;
				s_stats.declined_no_model++;
				return false;
			}

			default:
				break;
		}

		const PassFn pass = PassForAlgorithm(algorithm);
		if (!pass)
			return false;

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

	void NoteUpscaled(u64 tex0_hash, u32 bytes)
	{
		s_memory_usage += bytes;
		s_upscales_this_frame++;
		s_held[tex0_hash] = bytes;
		s_stats.upscaled++;
	}

	void NoteGuardSkipped()
	{
		s_stats.skipped_guard++;
	}

	void NoteEvicted(u64 tex0_hash, u32 bytes)
	{
		const auto it = s_held.find(tex0_hash);
		if (it == s_held.end())
			return;

		s_memory_usage -= std::min(s_memory_usage, bytes);
		s_held.erase(it);
		s_stats.evicted++;
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
		s_stats = {};

		// Queued work refers to hash-cache keys that no longer exist, so it is dropped rather
		// than allowed to inject into a cache that has moved on. The thread itself stays up.
		std::unique_lock<std::mutex> lock(s_worker_mutex);
		s_pending.clear();
		s_completed.clear();
		s_in_flight.clear();
		lock.unlock();

		// Models are keyed by the texture folder, which moves with the game.
		GSTextureUpscalerNN::Reset();
		GSTextureUpscalerRaisr::Reset();
	}

	void QueueUpscale(const GSTextureCache::HashCacheKey& key, const u8* src, int sw, int sh, u32 src_pitch,
		GSTextureUpscaleAlgorithm algorithm, u8 scale, TextureClass texture_class, bool mipmap,
		const std::pair<u8, u8>& alpha_minmax)
	{
		const u32 src_stride = src_pitch / sizeof(u32);

		PendingJob job;
		job.key = key;
		job.sw = sw;
		job.sh = sh;
		job.src_stride = static_cast<u32>(sw);
		job.algorithm = algorithm;
		job.scale = scale;
		job.texture_class = texture_class;
		job.mipmap = mipmap;
		job.deposterize = GSConfig.TextureUpscaleDeposterize;
		job.alpha_minmax = alpha_minmax;

		// Compacted to a tight sw-wide buffer on the way in. The caller's buffer is a shared
		// scratch area that will be overwritten by the very next texture upload, and it is
		// block-aligned rather than tightly packed, so neither its lifetime nor its stride
		// can be relied on once this returns.
		job.src.resize(static_cast<size_t>(sw) * static_cast<size_t>(sh));
		const u32* src_px = reinterpret_cast<const u32*>(src);
		for (int y = 0; y < sh; y++)
		{
			std::copy_n(src_px + static_cast<size_t>(y) * src_stride, static_cast<size_t>(sw),
				job.src.begin() + static_cast<size_t>(y) * static_cast<size_t>(sw));
		}

		std::unique_lock<std::mutex> lock(s_worker_mutex);
		if (!s_in_flight.insert(key.TEX0Hash).second)
			return; // already queued; a repeated cache miss must not queue the work twice

		EnsureWorker();
		s_pending.push_back(std::move(job));
		s_worker_cv.notify_one();
	}

	void PopCompleted(std::vector<CompletedUpscale>& out, u32 max_bytes)
	{
		std::unique_lock<std::mutex> lock(s_worker_mutex);
		u32 taken = 0;
		while (!s_completed.empty())
		{
			const u32 bytes = static_cast<u32>(s_completed.front().pixels.size() * sizeof(u32));
			// Always take at least one, so a texture bigger than the whole per-frame budget
			// still gets through on its own frame instead of wedging the queue forever.
			if (taken != 0 && taken + bytes > max_bytes)
				break;
			out.push_back(std::move(s_completed.front()));
			s_completed.erase(s_completed.begin());
			taken += bytes;
		}
	}

	void Shutdown()
	{
		{
			std::unique_lock<std::mutex> lock(s_worker_mutex);
			if (!s_worker_running)
				return;
			s_worker_quit = true;
			s_pending.clear();
		}
		s_worker_cv.notify_all();
		if (s_worker.joinable())
			s_worker.join();
		s_worker_running = false;

		std::unique_lock<std::mutex> lock(s_worker_mutex);
		s_completed.clear();
		s_in_flight.clear();
	}

	u32 GetMemoryUsage()
	{
		return s_memory_usage;
	}

	void RunFilterSelfTestOnce()
	{
		static bool tested = false;
		if (tested)
			return;
		tested = true;

		static constexpr GSTextureUpscaleAlgorithm ALL[] = {
			GSTextureUpscaleAlgorithm::Nearest, GSTextureUpscaleAlgorithm::Bilinear,
			GSTextureUpscaleAlgorithm::SharpBilinear, GSTextureUpscaleAlgorithm::Bicubic,
			GSTextureUpscaleAlgorithm::Mitchell, GSTextureUpscaleAlgorithm::Lanczos,
			GSTextureUpscaleAlgorithm::LanczosCAS, GSTextureUpscaleAlgorithm::Scale2x,
			GSTextureUpscaleAlgorithm::Eagle, GSTextureUpscaleAlgorithm::SuperEagle,
			GSTextureUpscaleAlgorithm::SaI2x, GSTextureUpscaleAlgorithm::SuperSaI2x,
			GSTextureUpscaleAlgorithm::xBR, GSTextureUpscaleAlgorithm::MMPX,
			GSTextureUpscaleAlgorithm::xBRZ, GSTextureUpscaleAlgorithm::ScaleForce,
			GSTextureUpscaleAlgorithm::Anime4K};
		static const char* const NAMES[] = {"Nearest", "Bilinear", "SharpBilinear", "Bicubic",
			"Mitchell", "Lanczos", "LanczosCAS", "Scale2x", "Eagle", "SuperEagle", "2xSaI",
			"Super2xSaI", "xBR", "MMPX", "xBRZ", "ScaleForce", "Anime4K"};
		static_assert(std::size(ALL) == std::size(NAMES), "filter self-test name list out of step");

		constexpr int W = 8;
		constexpr int H = 8;
		std::vector<u32> src(W * H);
		for (int y = 0; y < H; y++)
		{
			for (int x = 0; x < W; x++)
			{
				const u32 r = static_cast<u32>(x * 32);
				const u32 g = static_cast<u32>(y * 32);
				const u32 b = (x < W / 2) ? 0u : 255u;
				src[static_cast<size_t>(y) * W + x] = 0xFF000000u | (b << 16) | (g << 8) | r;
			}
		}

		u32 passed = 0;
		u32 failed = 0;
		for (size_t i = 0; i < std::size(ALL); i++)
		{
			for (const u8 scale : {u8(2), u8(4)})
			{
				const int dw = W * scale;
				const int dh = H * scale;
				// Guard band: filled with a sentinel and checked afterwards, so a kernel that
				// writes past its rows is caught here rather than as heap corruption later.
				constexpr u32 SENTINEL = 0xDEADBEEFu;
				std::vector<u32> dst(static_cast<size_t>(dw) * static_cast<size_t>(dh) + 16, SENTINEL);

				const bool ok = ScaleBuffer(ALL[i], reinterpret_cast<const u8*>(src.data()), W, H,
					W * sizeof(u32), reinterpret_cast<u8*>(dst.data()), static_cast<u32>(dw) * sizeof(u32),
					scale);

				bool overran = false;
				for (size_t g = dst.size() - 16; g < dst.size(); g++)
					overran |= (dst[g] != SENTINEL);

				if (!ok || overran)
				{
					failed++;
					Console.Error("Texture upscaling: filter self-test %s x%u %s.", NAMES[i],
						static_cast<u32>(scale), overran ? "WROTE OUT OF BOUNDS" : "failed to run");
					continue;
				}
				passed++;
			}
		}

		if (failed == 0)
			Console.WriteLn("Texture upscaling: filter self-test %u/%u OK.", passed, passed);
		else
			Console.Error("Texture upscaling: filter self-test %u OK, %u FAILED.", passed, failed);
	}

	const char* CurrentAlgorithmName()
	{
		switch (GSConfig.TextureUpscaleWorldEnabled ? GSConfig.TextureUpscaleWorldAlgorithm :
													  GSConfig.TextureUpscaleUiAlgorithm)
		{
			case GSTextureUpscaleAlgorithm::Nearest: return "Nearest";
			case GSTextureUpscaleAlgorithm::Bilinear: return "Bilinear";
			case GSTextureUpscaleAlgorithm::SharpBilinear: return "SharpBilinear";
			case GSTextureUpscaleAlgorithm::Bicubic: return "Bicubic";
			case GSTextureUpscaleAlgorithm::Mitchell: return "Mitchell";
			case GSTextureUpscaleAlgorithm::Lanczos: return "Lanczos";
			case GSTextureUpscaleAlgorithm::LanczosCAS: return "Lanczos+CAS";
			case GSTextureUpscaleAlgorithm::Scale2x: return "Scale2x";
			case GSTextureUpscaleAlgorithm::Eagle: return "Eagle";
			case GSTextureUpscaleAlgorithm::SuperEagle: return "SuperEagle";
			case GSTextureUpscaleAlgorithm::SaI2x: return "2xSaI";
			case GSTextureUpscaleAlgorithm::SuperSaI2x: return "Super2xSaI";
			case GSTextureUpscaleAlgorithm::xBR: return "xBR";
			case GSTextureUpscaleAlgorithm::MMPX: return "MMPX";
			case GSTextureUpscaleAlgorithm::xBRZ: return "xBRZ";
			case GSTextureUpscaleAlgorithm::ScaleForce: return "ScaleForce";
			case GSTextureUpscaleAlgorithm::Anime4K: return "Anime4K";
			case GSTextureUpscaleAlgorithm::FSRCNN: return "FSRCNN";
			case GSTextureUpscaleAlgorithm::SESR: return "SESR";
			case GSTextureUpscaleAlgorithm::ESPCN: return "ESPCN";
			default: return "?";
		}
	}

	const Stats& GetStats()
	{
		s_stats.held = static_cast<u32>(s_held.size());
		s_stats.memory_usage = s_memory_usage;
		return s_stats;
	}
} // namespace GSTextureUpscaler
