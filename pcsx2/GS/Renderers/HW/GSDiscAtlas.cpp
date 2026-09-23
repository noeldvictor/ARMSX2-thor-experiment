// SPDX-FileCopyrightText: 2026 ARMSX2 Contributors
// SPDX-License-Identifier: GPL-3.0+

#include "GS/Renderers/HW/GSDiscAtlas.h"
#include "GS/Renderers/HW/GSTextureReplacements.h"
#include "GS/GSXXH.h"

#include "common/Console.h"
#include "common/FileSystem.h"
#include "common/Path.h"
#include "common/StringUtil.h"

#include "fmt/format.h"

#include <algorithm>
#include <cstring>
#include <list>
#include <mutex>
#include <unordered_map>
#include <vector>

// disc-atlas.a2at, little-endian:
//   Header
//   Image[image_count]   64 bytes each
//   Tile[tile_count]     24 bytes each, sorted by (clut_hash, tile_hash)
//   index data           each image's palette indices, one byte per texel (PSMT4 expanded), row-major
namespace
{
#pragma pack(push, 1)
	struct Header
	{
		char magic[4]; // "A2AT"
		u32 version; // 1
		u32 image_count;
		u32 tile_count;
		u32 tile_size; // 16
		u32 reserved;
		u64 index_data_offset;
	};
	static_assert(sizeof(Header) == 32);

	struct Image
	{
		u64 clut_hash; // XXH3 of the palette as the texture cache keys it
		u32 width;
		u32 height;
		u64 index_offset; // from Header::index_data_offset
		char file[40]; // upscaled image, relative to the replacement directory
	};
	static_assert(sizeof(Image) == 64);

	struct Tile
	{
		u64 clut_hash;
		u64 tile_hash; // XXH3 of the block's 16x16 indices, row by row
		u32 image;
		u16 x;
		u16 y;
	};
	static_assert(sizeof(Tile) == 24);
#pragma pack(pop)

	struct State
	{
		std::string dir;
		std::vector<Image> images;
		std::vector<Tile> tiles;
		std::vector<u8> index_data;
	};

	State s_state;
	bool s_loaded = false;

	// Decoded upscaled disc images, shared by every crop cut from them. Worker-thread side.
	std::mutex s_image_mutex;
	std::unordered_map<u32, GSTextureReplacements::ReplacementTexture> s_image_cache;
	std::list<u32> s_image_lru; // front = least recently used
	size_t s_image_cache_bytes = 0;
	constexpr size_t IMAGE_CACHE_BUDGET = 192u * 1024u * 1024u;

	// Candidates are confirmed by hashing their crop; a block that appears everywhere (a flat
	// colour) could make that expensive, so stop after this many.
	constexpr u32 MAX_CANDIDATES = 4096;

	constexpr std::string_view CROP_PREFIX = "a2at:";
} // namespace

bool GSDiscAtlas::Load(const std::string& replacement_dir)
{
	Clear();

	const std::string path = Path::Combine(replacement_dir, "disc-atlas.a2at");
	if (!FileSystem::FileExists(path.c_str()))
		return false;

	std::optional<std::vector<u8>> data = FileSystem::ReadBinaryFile(path.c_str());
	if (!data.has_value() || data->size() < sizeof(Header))
	{
		Console.Error(fmt::format("Disc atlas: cannot read {}", path));
		return false;
	}

	Header hdr;
	std::memcpy(&hdr, data->data(), sizeof(hdr));
	const u64 images_end = sizeof(Header) + static_cast<u64>(hdr.image_count) * sizeof(Image);
	const u64 tiles_end = images_end + static_cast<u64>(hdr.tile_count) * sizeof(Tile);
	if (std::memcmp(hdr.magic, "A2AT", 4) != 0 || hdr.version != 1 || hdr.tile_size != TILE ||
		tiles_end > data->size() || hdr.index_data_offset > data->size())
	{
		Console.Error(fmt::format("Disc atlas: {} is not a version 1 index", path));
		return false;
	}

	State st;
	st.dir = replacement_dir;
	st.images.resize(hdr.image_count);
	std::memcpy(st.images.data(), data->data() + sizeof(Header), hdr.image_count * sizeof(Image));
	st.tiles.resize(hdr.tile_count);
	std::memcpy(st.tiles.data(), data->data() + images_end, hdr.tile_count * sizeof(Tile));
	st.index_data.assign(data->data() + hdr.index_data_offset, data->data() + data->size());

	for (const Image& img : st.images)
	{
		if (img.index_offset + static_cast<u64>(img.width) * img.height > st.index_data.size())
		{
			Console.Error(fmt::format("Disc atlas: {} has an image past its index data", path));
			return false;
		}
	}

	s_state = std::move(st);
	s_loaded = true;
	Console.WriteLnFmt("Disc atlas: {} disc images, {} index blocks ({})", s_state.images.size(),
		s_state.tiles.size(), path);
	return true;
}

void GSDiscAtlas::Clear()
{
	s_loaded = false;
	s_state = {};

	std::unique_lock lock(s_image_mutex);
	s_image_cache.clear();
	s_image_lru.clear();
	s_image_cache_bytes = 0;
}

bool GSDiscAtlas::IsLoaded()
{
	return s_loaded;
}

u32 GSDiscAtlas::GetImageCount()
{
	return static_cast<u32>(s_state.images.size());
}

std::string GSDiscAtlas::Match(u64 tex0_hash, u64 clut_hash, u32 width, u32 height, u64 probe)
{
	if (!s_loaded || width < TILE || height < TILE)
		return {};

	const auto key_less = [](const Tile& t, const std::pair<u64, u64>& k) {
		return t.clut_hash < k.first || (t.clut_hash == k.first && t.tile_hash < k.second);
	};
	const std::pair<u64, u64> key{clut_hash, probe};
	auto it = std::lower_bound(s_state.tiles.begin(), s_state.tiles.end(), key, key_less);

	u32 checked = 0;
	for (; it != s_state.tiles.end() && it->clut_hash == clut_hash && it->tile_hash == probe; ++it)
	{
		const Image& img = s_state.images[it->image];
		if (it->x + width > img.width || it->y + height > img.height)
			continue;
		if (++checked > MAX_CANDIDATES)
			break;

		// The texture cache's TEX0 hash of a region texture is XXH3 over its expanded indices,
		// row by row - the same bytes as this crop of the disc image.
		XXH3_state_t st;
		XXH3_64bits_reset(&st);
		const u8* row = s_state.index_data.data() + img.index_offset + static_cast<size_t>(it->y) * img.width + it->x;
		for (u32 y = 0; y < height; y++, row += img.width)
			GSXXH3_64bits_update(&st, row, width);
		if (GSXXH3_64bits_digest(&st) != tex0_hash)
			continue;

		return fmt::format("{}{}:{}:{}:{}:{}", CROP_PREFIX, it->image, it->x, it->y, width, height);
	}
	return {};
}

bool GSDiscAtlas::IsCropFilename(std::string_view filename)
{
	return filename.starts_with(CROP_PREFIX);
}

bool GSDiscAtlas::LoadCrop(const std::string& filename, GSTextureReplacements::ReplacementTexture* tex)
{
	u32 image = 0, x = 0, y = 0, w = 0, h = 0;
	if (std::sscanf(filename.c_str() + CROP_PREFIX.size(), "%u:%u:%u:%u:%u", &image, &x, &y, &w, &h) != 5 ||
		image >= s_state.images.size())
	{
		return false;
	}
	const Image& info = s_state.images[image];

	std::unique_lock lock(s_image_mutex);
	auto cached = s_image_cache.find(image);
	if (cached == s_image_cache.end())
	{
		const std::string path = Path::Combine(s_state.dir, std::string_view(info.file, strnlen(info.file, sizeof(info.file))));
		const GSTextureReplacements::ReplacementTextureLoader loader = GSTextureReplacements::GetLoader(path);
		GSTextureReplacements::ReplacementTexture full;
		if (!loader || !loader(path, &full, true) || full.format != GSTexture::Format::Color)
		{
			Console.Warning(fmt::format("Disc atlas: cannot load {} as RGBA8", path));
			return false;
		}

		// Evict whole images, least recently used first, to stay under the budget.
		const size_t bytes = full.data.size();
		while (s_image_cache_bytes + bytes > IMAGE_CACHE_BUDGET && !s_image_lru.empty())
		{
			auto victim = s_image_cache.find(s_image_lru.front());
			s_image_cache_bytes -= victim->second.data.size();
			s_image_cache.erase(victim);
			s_image_lru.pop_front();
		}
		cached = s_image_cache.emplace(image, std::move(full)).first;
		s_image_cache_bytes += bytes;
		s_image_lru.push_back(image);
	}
	else
	{
		s_image_lru.remove(image);
		s_image_lru.push_back(image);
	}

	const GSTextureReplacements::ReplacementTexture& full = cached->second;
	const u32 scale = full.width / info.width;
	if (scale == 0 || full.width != info.width * scale || full.height != info.height * scale)
	{
		Console.Warning(fmt::format("Disc atlas: {}x{} is not a whole multiple of {}x{}", full.width,
			full.height, info.width, info.height));
		return false;
	}

	tex->width = w * scale;
	tex->height = h * scale;
	tex->format = GSTexture::Format::Color;
	tex->pitch = tex->width * 4;
	tex->data.resize(static_cast<size_t>(tex->pitch) * tex->height);
	tex->mips.clear();
	const u8* src = full.data.data() + static_cast<size_t>(y * scale) * full.pitch + static_cast<size_t>(x * scale) * 4;
	for (u32 row = 0; row < tex->height; row++)
		std::memcpy(tex->data.data() + static_cast<size_t>(row) * tex->pitch, src + static_cast<size_t>(row) * full.pitch, tex->pitch);
	return true;
}
