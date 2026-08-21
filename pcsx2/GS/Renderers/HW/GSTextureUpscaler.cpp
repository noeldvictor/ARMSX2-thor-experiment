// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#include "GS/Renderers/HW/GSTextureUpscaler.h"

#include "GS/GS.h"

#include "common/Console.h"

#include <algorithm>
#include <cmath>
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

			case GSTextureUpscaleAlgorithm::Bicubic:
				ScaleBicubic(src_px, sw, sh, src_stride, dst_px, dst_stride, scale);
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
	}

	u32 GetMemoryUsage()
	{
		return s_memory_usage;
	}

	const Stats& GetStats()
	{
		s_stats.held = static_cast<u32>(s_held.size());
		s_stats.memory_usage = s_memory_usage;
		return s_stats;
	}
} // namespace GSTextureUpscaler
