// SPDX-FileCopyrightText: 2002-2025 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#include "GS/Renderers/HW/GSTextureUpscalerNN.h"

#include "common/Console.h"
#include "common/FileSystem.h"
#include "common/Path.h"

#include <algorithm>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace GSTextureUpscalerNN
{
	namespace
	{
		/// `.a2nn` file layout, little-endian throughout:
		///
		///   char   magic[4]    "A2NN"
		///   u32    version     1
		///   u32    scale       2 or 4
		///   u32    layers
		///   per layer:
		///     u32  in_channels
		///     u32  out_channels
		///     u32  kernel      odd, 1..7
		///     u32  activation  0 = none, 1 = ReLU
		///     f32  weights[out][in][k][k]
		///     f32  biases[out]
		///
		/// Input is 3-channel RGB in 0..1. The last layer must emit scale*scale*3 channels,
		/// which are pixel-shuffled into the output — the standard sub-pixel arrangement, so
		/// a converted ESPCN/FSRCNN/SESR graph drops in without reordering.
		constexpr u32 MAGIC = 0x4E4E3241u; // 'A2NN' little-endian
		constexpr u32 VERSION = 1;

		/// Refuse networks heavier than this. A 256x256 texture is 65k pixels, so 4000
		/// MACs/pixel is around 260 MMAC per texture — a fraction of a second on one core.
		/// Ten times that would stall the worker long enough that textures visibly pop in
		/// minutes after a scene loads, which is worse than not upscaling at all.
		constexpr u64 MAX_MACS_PER_PIXEL = 4000;

		/// Above this the activation buffers stop being reasonable and the latency stops
		/// being hideable. PS2 textures are almost never this large anyway.
		constexpr int MAX_DIMENSION = 512;

		struct Layer
		{
			u32 in_channels = 0;
			u32 out_channels = 0;
			u32 kernel = 3;
			u32 activation = 0;
			std::vector<float> weights;
			std::vector<float> biases;
		};

		struct Model
		{
			bool valid = false;
			u32 scale = 2;
			std::vector<Layer> layers;
			u64 macs_per_pixel = 0;
		};

		std::mutex s_mutex;
		std::unordered_map<std::string, Model> s_models;

		const char* AlgorithmBaseName(GSTextureUpscaleAlgorithm algorithm)
		{
			switch (algorithm)
			{
				case GSTextureUpscaleAlgorithm::Anime4K: return "anime4k";
				case GSTextureUpscaleAlgorithm::FSRCNN: return "fsrcnn";
				case GSTextureUpscaleAlgorithm::SESR: return "sesr";
				case GSTextureUpscaleAlgorithm::ESPCN: return "espcn";
				default: return nullptr;
			}
		}

		template <typename T>
		bool ReadPod(const u8*& p, const u8* end, T* out)
		{
			if (static_cast<size_t>(end - p) < sizeof(T))
				return false;
			std::memcpy(out, p, sizeof(T));
			p += sizeof(T);
			return true;
		}

		Model LoadModel(const std::string& path, u8 wanted_scale)
		{
			Model model;

			std::optional<std::vector<u8>> data = FileSystem::ReadBinaryFile(path.c_str());
			if (!data.has_value())
				return model; // absent is the normal case, not an error worth logging

			const u8* p = data->data();
			const u8* end = p + data->size();

			u32 magic = 0, version = 0, scale = 0, layer_count = 0;
			if (!ReadPod(p, end, &magic) || !ReadPod(p, end, &version) || !ReadPod(p, end, &scale) ||
				!ReadPod(p, end, &layer_count))
			{
				Console.Error("Texture upscaling: %s is too short to be a model.", path.c_str());
				return model;
			}

			if (magic != MAGIC || version != VERSION)
			{
				Console.Error("Texture upscaling: %s is not an A2NN v%u model.", path.c_str(), VERSION);
				return model;
			}

			if (scale != static_cast<u32>(wanted_scale))
			{
				Console.Error("Texture upscaling: %s is a x%u model but x%u was requested.", path.c_str(),
					scale, static_cast<u32>(wanted_scale));
				return model;
			}

			if (layer_count == 0 || layer_count > 32)
			{
				Console.Error("Texture upscaling: %s declares %u layers.", path.c_str(), layer_count);
				return model;
			}

			model.scale = scale;
			model.layers.resize(layer_count);

			u32 expected_in = 3; // RGB
			for (u32 i = 0; i < layer_count; i++)
			{
				Layer& layer = model.layers[i];
				if (!ReadPod(p, end, &layer.in_channels) || !ReadPod(p, end, &layer.out_channels) ||
					!ReadPod(p, end, &layer.kernel) || !ReadPod(p, end, &layer.activation))
				{
					Console.Error("Texture upscaling: %s ended inside layer %u.", path.c_str(), i);
					return model;
				}

				if (layer.in_channels != expected_in)
				{
					Console.Error("Texture upscaling: %s layer %u takes %u channels but the previous layer "
								  "produces %u.",
						path.c_str(), i, layer.in_channels, expected_in);
					return model;
				}

				if (layer.out_channels == 0 || layer.out_channels > 512 || layer.kernel == 0 ||
					layer.kernel > 7 || (layer.kernel % 2) == 0)
				{
					Console.Error("Texture upscaling: %s layer %u has unusable dimensions.", path.c_str(), i);
					return model;
				}

				const size_t weight_count = static_cast<size_t>(layer.out_channels) * layer.in_channels *
											layer.kernel * layer.kernel;
				layer.weights.resize(weight_count);
				layer.biases.resize(layer.out_channels);
				if (static_cast<size_t>(end - p) < (weight_count + layer.out_channels) * sizeof(float))
				{
					Console.Error("Texture upscaling: %s ended inside layer %u's weights.", path.c_str(), i);
					return model;
				}
				std::memcpy(layer.weights.data(), p, weight_count * sizeof(float));
				p += weight_count * sizeof(float);
				std::memcpy(layer.biases.data(), p, layer.out_channels * sizeof(float));
				p += layer.out_channels * sizeof(float);

				model.macs_per_pixel += static_cast<u64>(layer.in_channels) * layer.out_channels *
										layer.kernel * layer.kernel;
				expected_in = layer.out_channels;
			}

			const u32 needed = scale * scale * 3;
			if (expected_in != needed)
			{
				Console.Error("Texture upscaling: %s ends with %u channels; a x%u model must end with %u "
							  "for pixel shuffle.",
					path.c_str(), expected_in, scale, needed);
				return model;
			}

			if (model.macs_per_pixel > MAX_MACS_PER_PIXEL)
			{
				Console.Error("Texture upscaling: %s needs %llu MACs/pixel, over the %llu limit. It would "
							  "run, but textures would arrive long after the scene they belong to.",
					path.c_str(), static_cast<unsigned long long>(model.macs_per_pixel),
					static_cast<unsigned long long>(MAX_MACS_PER_PIXEL));
				return model;
			}

			model.valid = true;
			Console.WriteLn("Texture upscaling: loaded %s (%u layers, %llu MACs/pixel).", path.c_str(),
				static_cast<u32>(model.layers.size()),
				static_cast<unsigned long long>(model.macs_per_pixel));
			return model;
		}

		/// Returns nullptr when there is no usable model. Caller must hold s_mutex.
		const Model* GetModelLocked(GSTextureUpscaleAlgorithm algorithm, u8 scale)
		{
			const char* base = AlgorithmBaseName(algorithm);
			if (!base)
				return nullptr;

			const std::string key = std::string(base) + "_x" + std::to_string(static_cast<int>(scale));
			const auto it = s_models.find(key);
			if (it != s_models.end())
				return it->second.valid ? &it->second : nullptr;

			const std::string path =
				Path::Combine(Path::Combine(EmuFolders::Textures, "models"), key + ".a2nn");
			const Model& stored = (s_models[key] = LoadModel(path, scale));
			return stored.valid ? &stored : nullptr;
		}

		inline float Srgb8ToFloat(u32 pixel, int channel)
		{
			return static_cast<float>((pixel >> (channel * 8)) & 0xFF) * (1.0f / 255.0f);
		}

		/// Direct convolution with clamp-to-edge padding, planar float in and out.
		void Convolve(const Layer& layer, const std::vector<float>& in, std::vector<float>& out, int w, int h)
		{
			const int k = static_cast<int>(layer.kernel);
			const int half = k / 2;
			const size_t plane = static_cast<size_t>(w) * static_cast<size_t>(h);
			out.assign(plane * layer.out_channels, 0.0f);

			for (u32 oc = 0; oc < layer.out_channels; oc++)
			{
				float* out_plane = out.data() + plane * oc;
				const float bias = layer.biases[oc];

				for (int y = 0; y < h; y++)
				{
					for (int x = 0; x < w; x++)
					{
						float acc = bias;
						for (u32 ic = 0; ic < layer.in_channels; ic++)
						{
							const float* in_plane = in.data() + plane * ic;
							const float* wp = layer.weights.data() +
											  (((static_cast<size_t>(oc) * layer.in_channels) + ic) * k * k);
							for (int ky = 0; ky < k; ky++)
							{
								const int sy = std::clamp(y + ky - half, 0, h - 1);
								for (int kx = 0; kx < k; kx++)
								{
									const int sx = std::clamp(x + kx - half, 0, w - 1);
									acc += in_plane[static_cast<size_t>(sy) * w + sx] *
										   wp[static_cast<size_t>(ky) * k + kx];
								}
							}
						}
						// ReLU is the only activation defined; anything else is identity, which
						// is what a final layer wants.
						out_plane[static_cast<size_t>(y) * w + x] =
							(layer.activation == 1) ? std::max(0.0f, acc) : acc;
					}
				}
			}
		}
	} // namespace

	bool IsAvailable(GSTextureUpscaleAlgorithm algorithm, u8 scale)
	{
		std::unique_lock<std::mutex> lock(s_mutex);
		return GetModelLocked(algorithm, scale) != nullptr;
	}

	bool Run(GSTextureUpscaleAlgorithm algorithm, const u32* src, int sw, int sh, u32 src_stride,
		u32* dst, u32 dst_stride, u8 scale)
	{
		if (sw <= 0 || sh <= 0 || sw > MAX_DIMENSION || sh > MAX_DIMENSION)
			return false;

		// The model is only touched under the lock for lookup; inference reads it afterwards
		// without holding it, which is safe because a loaded model is never mutated.
		const Model* model = nullptr;
		{
			std::unique_lock<std::mutex> lock(s_mutex);
			model = GetModelLocked(algorithm, scale);
		}
		if (!model)
			return false;

		const size_t plane = static_cast<size_t>(sw) * static_cast<size_t>(sh);

		std::vector<float> a(plane * 3);
		for (int y = 0; y < sh; y++)
		{
			for (int x = 0; x < sw; x++)
			{
				const u32 px = src[static_cast<size_t>(y) * src_stride + x];
				const size_t o = static_cast<size_t>(y) * sw + x;
				a[o] = Srgb8ToFloat(px, 0);
				a[plane + o] = Srgb8ToFloat(px, 1);
				a[plane * 2 + o] = Srgb8ToFloat(px, 2);
			}
		}

		std::vector<float> b;
		for (const Layer& layer : model->layers)
		{
			Convolve(layer, a, b, sw, sh);
			a.swap(b);
		}

		// Pixel shuffle. Channel (sy * scale + sx) * 3 + c lands at output pixel
		// (x * scale + sx, y * scale + sy) — the standard sub-pixel layout.
		const int s = static_cast<int>(scale);
		for (int y = 0; y < sh; y++)
		{
			for (int x = 0; x < sw; x++)
			{
				const size_t o = static_cast<size_t>(y) * sw + x;
				const u32 src_px = src[static_cast<size_t>(y) * src_stride + x];
				const u32 alpha = src_px & 0xFF000000u;

				for (int sy = 0; sy < s; sy++)
				{
					for (int sx = 0; sx < s; sx++)
					{
						const int base_channel = ((sy * s) + sx) * 3;
						u32 out_px = alpha;
						for (int c = 0; c < 3; c++)
						{
							const float v = a[plane * static_cast<size_t>(base_channel + c) + o];
							const int iv = static_cast<int>(v * 255.0f + 0.5f);
							out_px |= static_cast<u32>(std::clamp(iv, 0, 255)) << (c * 8);
						}
						dst[static_cast<size_t>(y * s + sy) * dst_stride + (x * s + sx)] = out_px;
					}
				}
			}
		}

		// Alpha is carried through from the source rather than predicted. These networks are
		// trained on RGB; asking one to invent alpha produces soft edges on exactly the
		// cutout textures where a hard edge matters most.
		return true;
	}

	void Reset()
	{
		std::unique_lock<std::mutex> lock(s_mutex);
		s_models.clear();
	}

	void RunSelfTestOnce()
	{
		static bool tested = false;
		if (tested)
			return;
		tested = true;

		static constexpr GSTextureUpscaleAlgorithm ALGORITHMS[] = {
			GSTextureUpscaleAlgorithm::Anime4K, GSTextureUpscaleAlgorithm::FSRCNN,
			GSTextureUpscaleAlgorithm::SESR, GSTextureUpscaleAlgorithm::ESPCN};

		// A gradient with a hard edge down the middle: enough structure that a checksum
		// changes if the convolution or the pixel shuffle is wrong, small enough to be free.
		constexpr int W = 8;
		constexpr int H = 8;
		u32 src[W * H];
		for (int y = 0; y < H; y++)
		{
			for (int x = 0; x < W; x++)
			{
				const u32 r = static_cast<u32>(x * 32);
				const u32 g = static_cast<u32>(y * 32);
				const u32 b = (x < W / 2) ? 0u : 255u;
				src[y * W + x] = 0xFF000000u | (b << 16) | (g << 8) | r;
			}
		}

		for (const GSTextureUpscaleAlgorithm algorithm : ALGORITHMS)
		{
			const char* name = AlgorithmBaseName(algorithm);
			for (const u8 scale : {u8(2), u8(4)})
			{
				if (!IsAvailable(algorithm, scale))
					continue;

				const int dw = W * scale;
				const int dh = H * scale;
				std::vector<u32> dst(static_cast<size_t>(dw) * static_cast<size_t>(dh), 0u);
				if (!Run(algorithm, src, W, H, W, dst.data(), static_cast<u32>(dw), scale))
				{
					Console.Error("Texture upscaling: model self-test %s x%u FAILED to run.", name,
						static_cast<u32>(scale));
					continue;
				}

				u32 checksum = 0;
				for (const u32 px : dst)
					checksum = (checksum * 31u) + px;

				Console.WriteLn("Texture upscaling: model self-test %s x%u OK - %dx%d to %dx%d, "
								"checksum %08x. The neural path ran.",
					name, static_cast<u32>(scale), W, H, dw, dh, checksum);
			}
		}
	}
} // namespace GSTextureUpscalerNN
