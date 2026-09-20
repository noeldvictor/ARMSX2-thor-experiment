// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#include "pcsx2/GS/Renderers/HW/GSTextureUpscalerRaisr.h"
#include "pcsx2/Config.h"

#include "common/Console.h"
#include "common/FileSystem.h"
#include "common/Path.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace GSTextureUpscalerRaisr
{
	namespace
	{
		/// `.a2rk` layout, little-endian throughout, written by tools/raisr_train.py:
		///   char[4]  "A2RK"
		///   u32      version (1)
		///   u32      scale (2 or 4)
		///   u32      k (kernel side, odd)
		///   u32      qa, qs, qc (angle / strength / coherence bin counts)
		///   u32      window (structure-tensor window side, odd)
		///   f32      sigma (Gaussian sigma of that window)
		///   f32[qs-1] strength thresholds, ascending
		///   f32[qc-1] coherence thresholds, ascending
		///   f32[scale*scale][qa*qs*qc][k*k] kernels; phase = dy*scale+dx, bucket = (a*qs+s)*qc+c,
		///            patch row-major with the centre tap at (k/2, k/2).
		constexpr u32 VERSION = 1;
		constexpr u32 HEADER_BYTES = 4 + 8 * 4;

		struct KernelSet
		{
			bool valid = false;
			u32 scale = 0;
			int k = 0;
			int qa = 0, qs = 0, qc = 0;
			int window = 0;
			std::vector<float> s_thresh;
			std::vector<float> c_thresh;
			std::vector<float> gauss; ///< 1D window weights, length `window`, sums to 1.
			std::vector<float> kernels; ///< [phase][bucket][k*k]
		};

		std::mutex s_mutex;
		std::map<std::string, KernelSet> s_sets;
		bool s_self_test_done = false;

		u32 ReadU32(const u8* p)
		{
			u32 v;
			std::memcpy(&v, p, sizeof(v));
			return v;
		}

		float ReadF32(const u8* p)
		{
			float v;
			std::memcpy(&v, p, sizeof(v));
			return v;
		}

		KernelSet LoadKernelSet(const std::string& path, u8 wanted_scale)
		{
			KernelSet set;
			std::optional<std::vector<u8>> data = FileSystem::ReadBinaryFile(path.c_str());
			if (!data.has_value())
			{
				Console.Warning("Texture upscaling: no RAISR-HD kernels at %s.", path.c_str());
				return set;
			}
			const std::vector<u8>& d = data.value();
			if (d.size() < HEADER_BYTES || std::memcmp(d.data(), "A2RK", 4) != 0)
			{
				Console.Error("Texture upscaling: %s is not an A2RK kernel file.", path.c_str());
				return set;
			}
			const u8* p = d.data() + 4;
			const u32 version = ReadU32(p + 0);
			set.scale = ReadU32(p + 4);
			set.k = static_cast<int>(ReadU32(p + 8));
			set.qa = static_cast<int>(ReadU32(p + 12));
			set.qs = static_cast<int>(ReadU32(p + 16));
			set.qc = static_cast<int>(ReadU32(p + 20));
			set.window = static_cast<int>(ReadU32(p + 24));
			const float sigma = ReadF32(p + 28);
			if (version != VERSION)
			{
				Console.Error("Texture upscaling: %s is A2RK v%u, expected v%u.", path.c_str(), version, VERSION);
				return set;
			}
			if (set.scale != wanted_scale)
			{
				Console.Error("Texture upscaling: %s is a x%u kernel set but x%u was requested.", path.c_str(),
					set.scale, static_cast<u32>(wanted_scale));
				return set;
			}
			if (set.k < 3 || set.k > 15 || (set.k & 1) == 0 || set.window < 3 || set.window > 15 ||
				(set.window & 1) == 0 || set.qa < 1 || set.qa > 64 || set.qs < 1 || set.qs > 8 || set.qc < 1 ||
				set.qc > 8 || !(sigma > 0.0f))
			{
				Console.Error("Texture upscaling: %s has unusable dimensions (k=%d window=%d bins=%d/%d/%d).",
					path.c_str(), set.k, set.window, set.qa, set.qs, set.qc);
				return set;
			}

			const size_t buckets = static_cast<size_t>(set.qa) * set.qs * set.qc;
			const size_t phases = static_cast<size_t>(set.scale) * set.scale;
			const size_t taps = static_cast<size_t>(set.k) * set.k;
			const size_t thresholds = static_cast<size_t>(set.qs - 1) + static_cast<size_t>(set.qc - 1);
			const size_t expected = HEADER_BYTES + (thresholds + phases * buckets * taps) * sizeof(float);
			if (d.size() != expected)
			{
				Console.Error("Texture upscaling: %s is %zu bytes, expected %zu.", path.c_str(), d.size(), expected);
				return set;
			}

			p = d.data() + HEADER_BYTES;
			set.s_thresh.resize(set.qs - 1);
			for (int i = 0; i < set.qs - 1; i++, p += 4)
				set.s_thresh[i] = ReadF32(p);
			set.c_thresh.resize(set.qc - 1);
			for (int i = 0; i < set.qc - 1; i++, p += 4)
				set.c_thresh[i] = ReadF32(p);
			set.kernels.resize(phases * buckets * taps);
			for (size_t i = 0; i < set.kernels.size(); i++, p += 4)
				set.kernels[i] = ReadF32(p);

			// Same normalised Gaussian as the trainer's gaussian_kernel(window, sigma).
			set.gauss.resize(set.window);
			const int r = set.window / 2;
			float sum = 0.0f;
			for (int i = 0; i < set.window; i++)
			{
				const float x = static_cast<float>(i - r);
				set.gauss[i] = std::exp(-(x * x) / (2.0f * sigma * sigma));
				sum += set.gauss[i];
			}
			for (float& g : set.gauss)
				g /= sum;

			set.valid = true;
			Console.WriteLn("Texture upscaling: loaded %s (x%u, %dx%d kernels, %zu buckets).", path.c_str(), set.scale,
				set.k, set.k, buckets);
			return set;
		}

		const char* ClassName(GSTextureUpscaler::TextureClass texture_class)
		{
			return (texture_class == GSTextureUpscaler::TextureClass::Ui) ? "ui" : "world";
		}

		/// Returns nullptr when there is no usable kernel set. Caller must hold s_mutex.
		const KernelSet* GetSetLocked(GSTextureUpscaler::TextureClass texture_class, u8 scale)
		{
			const std::string key = std::string(ClassName(texture_class)) + "_x" + std::to_string(static_cast<int>(scale));
			const auto it = s_sets.find(key);
			if (it != s_sets.end())
				return it->second.valid ? &it->second : nullptr;

			const std::string path = Path::Combine(Path::Combine(EmuFolders::Resources, "upscale"), key + ".a2rk");
			const KernelSet& stored = (s_sets[key] = LoadKernelSet(path, scale));
			return stored.valid ? &stored : nullptr;
		}

		inline float Chan(u32 pixel, int channel)
		{
			return static_cast<float>((pixel >> (channel * 8)) & 0xFF);
		}

		inline u32 PackClamped(const float* rgba)
		{
			u32 out = 0;
			for (int c = 0; c < 4; c++)
			{
				const float v = std::min(std::max(rgba[c] + 0.5f, 0.0f), 255.0f);
				out |= static_cast<u32>(v) << (c * 8);
			}
			return out;
		}

		/// Separable Gaussian-weighted window sum with clamp-to-edge, matching the trainer's
		/// `_window_sum` (np.pad mode="edge" clamps each axis independently, which is exactly
		/// what clamping the index in each pass does).
		void WindowSum(const std::vector<float>& in, std::vector<float>& tmp, std::vector<float>& out, int w, int h,
			const std::vector<float>& g)
		{
			const int r = static_cast<int>(g.size()) / 2;
			tmp.resize(in.size());
			out.resize(in.size());
			for (int y = 0; y < h; y++)
			{
				const float* row = in.data() + static_cast<size_t>(y) * w;
				float* trow = tmp.data() + static_cast<size_t>(y) * w;
				for (int x = 0; x < w; x++)
				{
					float acc = 0.0f;
					for (int i = -r; i <= r; i++)
						acc += g[i + r] * row[std::clamp(x + i, 0, w - 1)];
					trow[x] = acc;
				}
			}
			for (int y = 0; y < h; y++)
			{
				float* orow = out.data() + static_cast<size_t>(y) * w;
				for (int i = -r; i <= r; i++)
				{
					const float weight = g[i + r];
					const float* trow = tmp.data() + static_cast<size_t>(std::clamp(y + i, 0, h - 1)) * w;
					if (i == -r)
					{
						for (int x = 0; x < w; x++)
							orow[x] = weight * trow[x];
					}
					else
					{
						for (int x = 0; x < w; x++)
							orow[x] += weight * trow[x];
					}
				}
			}
		}

		/// Per-pixel bucket index from the luma plane. Mirrors Hasher.features/buckets.
		void ComputeBuckets(const KernelSet& set, const std::vector<float>& luma, int w, int h, std::vector<u32>& buckets)
		{
			const size_t n = static_cast<size_t>(w) * h;
			std::vector<float> gx(n), gy(n), gxx(n), gyy(n), gxy(n), tmp, sum;
			for (int y = 0; y < h; y++)
			{
				const int ym = std::max(y - 1, 0);
				const int yp = std::min(y + 1, h - 1);
				for (int x = 0; x < w; x++)
				{
					const int xm = std::max(x - 1, 0);
					const int xp = std::min(x + 1, w - 1);
					const size_t i = static_cast<size_t>(y) * w + x;
					gx[i] = (luma[static_cast<size_t>(y) * w + xp] - luma[static_cast<size_t>(y) * w + xm]) * 0.5f;
					gy[i] = (luma[static_cast<size_t>(yp) * w + x] - luma[static_cast<size_t>(ym) * w + x]) * 0.5f;
				}
			}
			for (size_t i = 0; i < n; i++)
			{
				gxx[i] = gx[i] * gx[i];
				gyy[i] = gy[i] * gy[i];
				gxy[i] = gx[i] * gy[i];
			}
			WindowSum(gxx, tmp, sum, w, h, set.gauss);
			gxx.swap(sum);
			WindowSum(gyy, tmp, sum, w, h, set.gauss);
			gyy.swap(sum);
			WindowSum(gxy, tmp, sum, w, h, set.gauss);
			gxy.swap(sum);

			constexpr float PI = 3.14159265358979323846f;
			buckets.resize(n);
			for (size_t i = 0; i < n; i++)
			{
				const float tr = gxx[i] + gyy[i];
				const float det = gxx[i] * gyy[i] - gxy[i] * gxy[i];
				const float disc = std::sqrt(std::max(tr * tr * 0.25f - det, 0.0f));
				const float l1 = tr * 0.5f + disc;
				const float l2 = std::max(tr * 0.5f - disc, 0.0f);
				float angle = std::fmod(0.5f * std::atan2(2.0f * gxy[i], gxx[i] - gyy[i]), PI);
				if (angle < 0.0f)
					angle += PI;
				const float s1 = std::sqrt(l1);
				const float s2 = std::sqrt(l2);
				const float strength = s1;
				const float coherence = (s1 - s2) / (s1 + s2 + 1e-6f);

				const int a = std::min(static_cast<int>(angle / PI * static_cast<float>(set.qa)), set.qa - 1);
				int s = 0;
				for (float t : set.s_thresh)
					s += (t < strength) ? 1 : 0;
				int c = 0;
				for (float t : set.c_thresh)
					c += (t < coherence) ? 1 : 0;
				buckets[i] = static_cast<u32>((a * set.qs + s) * set.qc + c);
			}
		}

		void Apply(const KernelSet& set, const u32* src, int sw, int sh, u32 src_stride, u32* dst, u32 dst_stride)
		{
			const int k = set.k;
			const int r = k / 2;
			const int scale = static_cast<int>(set.scale);
			const size_t taps = static_cast<size_t>(k) * k;
			const size_t buckets_n = static_cast<size_t>(set.qa) * set.qs * set.qc;

			// Luma and buckets at the input resolution.
			std::vector<float> luma(static_cast<size_t>(sw) * sh);
			for (int y = 0; y < sh; y++)
			{
				const u32* row = src + static_cast<size_t>(y) * src_stride;
				for (int x = 0; x < sw; x++)
				{
					const u32 px = row[x];
					luma[static_cast<size_t>(y) * sw + x] = Chan(px, 0) * 0.299f + Chan(px, 1) * 0.587f + Chan(px, 2) * 0.114f;
				}
			}
			std::vector<u32> buckets;
			ComputeBuckets(set, luma, sw, sh, buckets);

			// Clamp-to-edge padded RGBA float plane, so the per-tap gather has no branches.
			const int pw = sw + 2 * r;
			const int ph = sh + 2 * r;
			std::vector<float> pad(static_cast<size_t>(pw) * ph * 4);
			for (int y = 0; y < ph; y++)
			{
				const int sy = std::clamp(y - r, 0, sh - 1);
				const u32* row = src + static_cast<size_t>(sy) * src_stride;
				float* prow = pad.data() + static_cast<size_t>(y) * pw * 4;
				for (int x = 0; x < pw; x++)
				{
					const u32 px = row[std::clamp(x - r, 0, sw - 1)];
					prow[x * 4 + 0] = Chan(px, 0);
					prow[x * 4 + 1] = Chan(px, 1);
					prow[x * 4 + 2] = Chan(px, 2);
					prow[x * 4 + 3] = Chan(px, 3);
				}
			}

			for (int y = 0; y < sh; y++)
			{
				for (int x = 0; x < sw; x++)
				{
					const u32 bucket = std::min(buckets[static_cast<size_t>(y) * sw + x], static_cast<u32>(buckets_n - 1));
					// Patch origin in the padded plane: (y, x) here is the top-left tap because the
					// plane is offset by r.
					const float* patch0 = pad.data() + (static_cast<size_t>(y) * pw + x) * 4;
					for (int phase = 0; phase < scale * scale; phase++)
					{
						const float* kernel = set.kernels.data() + (static_cast<size_t>(phase) * buckets_n + bucket) * taps;
						float acc[4] = {0.0f, 0.0f, 0.0f, 0.0f};
						for (int ky = 0; ky < k; ky++)
						{
							const float* prow = patch0 + static_cast<size_t>(ky) * pw * 4;
							const float* krow = kernel + static_cast<size_t>(ky) * k;
							for (int kx = 0; kx < k; kx++)
							{
								const float wgt = krow[kx];
								const float* tap = prow + kx * 4;
								acc[0] += wgt * tap[0];
								acc[1] += wgt * tap[1];
								acc[2] += wgt * tap[2];
								acc[3] += wgt * tap[3];
							}
						}
						const int dy = phase / scale;
						const int dx = phase % scale;
						dst[(static_cast<size_t>(y) * scale + dy) * dst_stride + static_cast<size_t>(x) * scale + dx] =
							PackClamped(acc);
					}
				}
			}
		}
	} // namespace

	bool Run(GSTextureUpscaler::TextureClass texture_class, const u32* src, int sw, int sh, u32 src_stride, u32* dst,
		u32 dst_stride, u8 scale)
	{
		if (sw <= 0 || sh <= 0 || (scale != 2 && scale != 4))
			return false;

		// The set is immutable once loaded and the map only grows, so the pointer stays valid
		// after the lock is released; Reset() is the only eraser and it is never called while
		// the worker has a job in flight (the cache flush that triggers it drains the queue).
		const KernelSet* set;
		{
			std::lock_guard<std::mutex> lock(s_mutex);
			set = GetSetLocked(texture_class, scale);
		}
		if (!set)
			return false;

		Apply(*set, src, sw, sh, src_stride, dst, dst_stride);
		return true;
	}

	void Reset()
	{
		std::lock_guard<std::mutex> lock(s_mutex);
		s_sets.clear();
	}

	void RunSelfTestOnce()
	{
		if (s_self_test_done)
			return;
		s_self_test_done = true;

		// A diagonal edge over a gradient: every bucket family (flat, soft, hard, angled) is
		// represented, so a kernel-file mix-up shows up in the checksum.
		constexpr int W = 32;
		constexpr int H = 32;
		std::vector<u32> src(W * H);
		for (int y = 0; y < H; y++)
		{
			for (int x = 0; x < W; x++)
			{
				const u32 g = static_cast<u32>((x * 255) / (W - 1));
				const bool above = (x + y) < W;
				const u32 rr = above ? g : 255u - g;
				const u32 gg = above ? 200u : 40u;
				const u32 bb = static_cast<u32>((y * 255) / (H - 1));
				src[y * W + x] = rr | (gg << 8) | (bb << 16) | (0xFFu << 24);
			}
		}

		for (GSTextureUpscaler::TextureClass cls : {GSTextureUpscaler::TextureClass::World, GSTextureUpscaler::TextureClass::Ui})
		{
			constexpr u8 scale = 2;
			std::vector<u32> dst(static_cast<size_t>(W) * scale * H * scale);
			if (!Run(cls, src.data(), W, H, W, dst.data(), W * scale, scale))
			{
				Console.Warning("Texture upscaling: RAISR-HD self-test %s x%u did not run (no kernel file).", ClassName(cls),
					static_cast<u32>(scale));
				continue;
			}
			u32 checksum = 0;
			for (u32 px : dst)
				checksum = (checksum * 31u) + px;
			Console.WriteLn("Texture upscaling: RAISR-HD self-test %s x%u OK - %dx%d to %dx%d, checksum %08x.",
				ClassName(cls), static_cast<u32>(scale), W, H, W * scale, H * scale, checksum);
		}
	}
} // namespace GSTextureUpscalerRaisr
