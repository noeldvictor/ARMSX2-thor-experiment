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
#include <optional>
#include <cstring>
#include <list>
#include <mutex>
#include <unordered_map>
#include <vector>

// disc-atlas.a2at, little-endian:
//   Header               32 bytes (v1, v2) / 40 (v3: + free_tile_count)
//   Image[image_count]   64 bytes each (v1, v2) / 72 (v3: + flags)
//   Tile[tile_count]     24 bytes each, sorted by (clut_hash, tile_hash)
//   FreeTile[free_tile_count]  16 bytes each, sorted by tile_hash (v3: palette-free images;
//                        v4: also true-colour images, whose blocks hash 16x16 RGB texels)
//   index data           each image's palette indices, one byte per texel (PSMT4 expanded), row-major;
//                        a PSMCT24 image's RGB, three bytes per texel; a PSMCT32 image's RGBA, four
namespace
{
#pragma pack(push, 1)
	struct Header
	{
		char magic[4]; // "A2AT"
		u32 version; // 1: blocks every 16 pixels; 2: every tile_step pixels
		u32 image_count;
		u32 tile_count;
		u32 tile_size; // 16
		u32 tile_step; // version 2 (8); zero in version 1, meaning 16
		u64 index_data_offset;
	};
	static_assert(sizeof(Header) == 32);

	struct HeaderV3Tail
	{
		u32 free_tile_count;
		u32 reserved;
	};
	static_assert(sizeof(HeaderV3Tail) == 8);

	struct ImageV2
	{
		u64 clut_hash; // XXH3 of the palette as the texture cache keys it
		u32 width;
		u32 height;
		u64 index_offset; // from Header::index_data_offset
		char file[40]; // upscaled image, relative to the replacement directory
	};
	static_assert(sizeof(ImageV2) == 64);

	struct ImageV3
	{
		u64 clut_hash;
		u32 width;
		u32 height;
		u64 index_offset;
		u32 flags; // FLAG_PALETTE_FREE, FLAG_TRUE_COLOUR, FLAG_RGBA32
		u32 reserved;
		char file[40];
	};
	static_assert(sizeof(ImageV3) == 72);

	constexpr u32 FLAG_PALETTE_FREE = 1;
	constexpr u32 FLAG_TRUE_COLOUR = 2; // PSMCT24: RGB
	constexpr u32 FLAG_RGBA32 = 4; // PSMCT32: RGBA

	u32 BytesPerTexel(u32 flags)
	{
		return (flags & FLAG_RGBA32) ? 4 : (flags & FLAG_TRUE_COLOUR) ? 3 : 1;
	}

	struct Image
	{
		u64 clut_hash;
		u32 width;
		u32 height;
		u64 index_offset;
		u32 flags;
		char file[40];
	};

	struct FreeTile
	{
		u64 tile_hash;
		u32 image;
		u16 x;
		u16 y;
	};
	static_assert(sizeof(FreeTile) == 16);

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
		std::vector<FreeTile> free_tiles;
		std::vector<u8> index_data;
	};

	State s_state;
	bool s_loaded = false;
	u32 s_tile_step = 16;
	u32 s_matches = 0;
	u32 s_misses = 0;
	u32 s_palette_free_matches = 0;
	u32 s_true_colour_matches = 0;

	// Palettes a palette-free match was made under, by their hash, for LoadCrop on the worker.
	std::mutex s_palette_mutex;
	std::unordered_map<u64, std::vector<u32>> s_palettes;

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
	HeaderV3Tail tail{};
	const bool v3 = hdr.version >= 3;
	if (v3 && data->size() >= sizeof(Header) + sizeof(HeaderV3Tail))
		std::memcpy(&tail, data->data() + sizeof(Header), sizeof(tail));
	const u64 header_size = sizeof(Header) + (v3 ? sizeof(HeaderV3Tail) : 0);
	const u64 image_size = v3 ? sizeof(ImageV3) : sizeof(ImageV2);
	const u64 images_end = header_size + static_cast<u64>(hdr.image_count) * image_size;
	const u64 tiles_end = images_end + static_cast<u64>(hdr.tile_count) * sizeof(Tile);
	const u64 free_end = tiles_end + static_cast<u64>(tail.free_tile_count) * sizeof(FreeTile);
	const u32 step = (hdr.version >= 2) ? hdr.tile_step : TILE;
	if (std::memcmp(hdr.magic, "A2AT", 4) != 0 || hdr.version < 1 || hdr.version > 4 || hdr.tile_size != TILE ||
		step == 0 || (TILE % step) != 0 ||
		free_end > data->size() || hdr.index_data_offset > data->size())
	{
		Console.Error(fmt::format("Disc atlas: {} is not a version 1-4 index", path));
		return false;
	}

	State st;
	st.dir = replacement_dir;
	st.images.resize(hdr.image_count);
	for (u32 i = 0; i < hdr.image_count; i++)
	{
		const u8* rec = data->data() + header_size + i * image_size;
		Image& img = st.images[i];
		if (v3)
		{
			ImageV3 r;
			std::memcpy(&r, rec, sizeof(r));
			img = {r.clut_hash, r.width, r.height, r.index_offset, r.flags, {}};
			std::memcpy(img.file, r.file, sizeof(img.file));
		}
		else
		{
			ImageV2 r;
			std::memcpy(&r, rec, sizeof(r));
			img = {r.clut_hash, r.width, r.height, r.index_offset, 0, {}};
			std::memcpy(img.file, r.file, sizeof(img.file));
		}
	}
	st.tiles.resize(hdr.tile_count);
	std::memcpy(st.tiles.data(), data->data() + images_end, hdr.tile_count * sizeof(Tile));
	st.free_tiles.resize(tail.free_tile_count);
	std::memcpy(st.free_tiles.data(), data->data() + tiles_end, tail.free_tile_count * sizeof(FreeTile));
	st.index_data.assign(data->data() + hdr.index_data_offset, data->data() + data->size());

	for (const Image& img : st.images)
	{
		if (img.index_offset + static_cast<u64>(img.width) * img.height * BytesPerTexel(img.flags) > st.index_data.size())
		{
			Console.Error(fmt::format("Disc atlas: {} has an image past its index data", path));
			return false;
		}
	}

	s_state = std::move(st);
	s_loaded = true;
	s_tile_step = step;
	Console.WriteLnFmt("Disc atlas: {} disc images, {} index blocks every {} px, {} palette-free/true-colour ({})",
		s_state.images.size(), s_state.tiles.size(), step, s_state.free_tiles.size(), path);
	return true;
}

void GSDiscAtlas::Clear()
{
	if (s_loaded && (s_matches || s_misses))
	{
		Console.WriteLnFmt("Disc atlas: {} matches ({} palette-free, {} true-colour), {} misses", s_matches,
			s_palette_free_matches, s_true_colour_matches, s_misses);
	}
	s_loaded = false;
	s_state = {};
	s_tile_step = 16;
	s_matches = 0;
	s_misses = 0;
	s_palette_free_matches = 0;
	s_true_colour_matches = 0;
	{
		std::unique_lock lock(s_palette_mutex);
		s_palettes.clear();
	}

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

u32 GSDiscAtlas::GetTileStep()
{
	return s_tile_step;
}

GSDiscAtlas::Stats GSDiscAtlas::GetStats()
{
	return {static_cast<u32>(s_state.images.size()), s_matches, s_misses, s_palette_free_matches, s_true_colour_matches};
}

// The crop of image `img` at (x, y) with this size hashes like the texture cache hashes a region.
static bool CropHashes(const Image& img, u32 x, u32 y, u32 width, u32 height, u64 tex0_hash)
{
	XXH3_state_t st;
	XXH3_64bits_reset(&st);
	const u8* row = s_state.index_data.data() + img.index_offset + static_cast<size_t>(y) * img.width + x;
	for (u32 r = 0; r < height; r++, row += img.width)
		GSXXH3_64bits_update(&st, row, width);
	return GSXXH3_64bits_digest(&st) == tex0_hash;
}

std::string GSDiscAtlas::Match(const ContentHash& content_hash, u64 clut_hash, u32 width, u32 height, u64 probe,
	u32 probe_x, u32 probe_y, const u32* clut, u32 clut_entries)
{
	if (!s_loaded || probe_x + TILE > width || probe_y + TILE > height)
		return {};

	std::optional<u64> content;
	const auto tex0_hash = [&]() {
		if (!content.has_value())
			content = content_hash();
		return *content;
	};

	const auto key_less = [](const Tile& t, const std::pair<u64, u64>& k) {
		return t.clut_hash < k.first || (t.clut_hash == k.first && t.tile_hash < k.second);
	};
	const std::pair<u64, u64> key{clut_hash, probe};
	auto it = std::lower_bound(s_state.tiles.begin(), s_state.tiles.end(), key, key_less);

	u32 checked = 0;
	for (; it != s_state.tiles.end() && it->clut_hash == clut_hash && it->tile_hash == probe; ++it)
	{
		// The candidate block is (probe_x, probe_y) into the region, so the crop starts that far
		// before it.
		const Image& img = s_state.images[it->image];
		if (it->x < probe_x || it->y < probe_y)
			continue;
		const u32 x = it->x - probe_x;
		const u32 y = it->y - probe_y;
		if (x + width > img.width || y + height > img.height)
			continue;
		if (++checked > MAX_CANDIDATES)
			break;

		// The content hash is XXH3 over the texture's indices, row by row - the same bytes as this
		// crop of the disc image.
		if (!CropHashes(img, x, y, width, height, tex0_hash()))
			continue;

		s_matches++;
		return fmt::format("{}{}:{}:{}:{}:{}", CROP_PREFIX, it->image, x, y, width, height);
	}

	// No image with this palette: a palette-free image (one the game colours at runtime) whose
	// indices hash the same. Keep the palette for the loader, which paints the HD index map with it.
	if (clut && clut_entries > 0)
	{
		auto fit = std::lower_bound(s_state.free_tiles.begin(), s_state.free_tiles.end(), probe,
			[](const FreeTile& t, u64 h) { return t.tile_hash < h; });
		u32 free_checked = 0;
		for (; fit != s_state.free_tiles.end() && fit->tile_hash == probe; ++fit)
		{
			const Image& img = s_state.images[fit->image];
			if ((img.flags & (FLAG_TRUE_COLOUR | FLAG_RGBA32)) || fit->x < probe_x || fit->y < probe_y)
				continue;
			const u32 x = fit->x - probe_x;
			const u32 y = fit->y - probe_y;
			if (x + width > img.width || y + height > img.height)
				continue;
			if (++free_checked > MAX_CANDIDATES)
				break;
			if (!CropHashes(img, x, y, width, height, tex0_hash()))
				continue;

			{
				std::unique_lock lock(s_palette_mutex);
				s_palettes.try_emplace(clut_hash, clut, clut + clut_entries);
			}
			s_matches++;
			s_palette_free_matches++;
			return fmt::format("{}{}:{}:{}:{}:{}:{:016x}", CROP_PREFIX, fit->image, x, y, width, height, clut_hash);
		}
	}

	s_misses++;
	return {};
}

// A PSMCT24 texture's content hash is XXH3 over its texels expanded to 32 bits the way
// ReadTexture24 does it: the RGB, and alpha TA0 - or 0 for black when TEXA.AEM is set.
static bool CropHashesTrueColour(const Image& img, u32 x, u32 y, u32 width, u32 height, u64 tex0_hash, u8 ta0, bool aem)
{
	XXH3_state_t st;
	XXH3_64bits_reset(&st);
	std::vector<u8> row(static_cast<size_t>(width) * 4);
	const u8* src = s_state.index_data.data() + img.index_offset + (static_cast<size_t>(y) * img.width + x) * 3;
	for (u32 r = 0; r < height; r++, src += static_cast<size_t>(img.width) * 3)
	{
		for (u32 c = 0; c < width; c++)
		{
			const u8* p = src + c * 3;
			row[c * 4 + 0] = p[0];
			row[c * 4 + 1] = p[1];
			row[c * 4 + 2] = p[2];
			row[c * 4 + 3] = (aem && (p[0] | p[1] | p[2]) == 0) ? 0 : ta0;
		}
		GSXXH3_64bits_update(&st, row.data(), row.size());
	}
	return GSXXH3_64bits_digest(&st) == tex0_hash;
}

// A PSMCT32 texture's is XXH3 over its RGBA, as stored.
static bool CropHashesRGBA32(const Image& img, u32 x, u32 y, u32 width, u32 height, u64 tex0_hash)
{
	XXH3_state_t st;
	XXH3_64bits_reset(&st);
	const u8* row = s_state.index_data.data() + img.index_offset + (static_cast<size_t>(y) * img.width + x) * 4;
	for (u32 r = 0; r < height; r++, row += static_cast<size_t>(img.width) * 4)
		GSXXH3_64bits_update(&st, row, static_cast<size_t>(width) * 4);
	return GSXXH3_64bits_digest(&st) == tex0_hash;
}

std::string GSDiscAtlas::MatchTrueColour(const ContentHash& content_hash, u32 width, u32 height, u64 probe,
	u32 probe_x, u32 probe_y, u8 ta0, bool aem, bool rgba32)
{
	if (!s_loaded || probe_x + TILE > width || probe_y + TILE > height)
		return {};

	std::optional<u64> content;
	const auto tex0_hash = [&]() {
		if (!content.has_value())
			content = content_hash();
		return *content;
	};
	const u32 flag = rgba32 ? FLAG_RGBA32 : FLAG_TRUE_COLOUR;

	auto fit = std::lower_bound(s_state.free_tiles.begin(), s_state.free_tiles.end(), probe,
		[](const FreeTile& t, u64 h) { return t.tile_hash < h; });
	u32 checked = 0;
	for (; fit != s_state.free_tiles.end() && fit->tile_hash == probe; ++fit)
	{
		const Image& img = s_state.images[fit->image];
		if (!(img.flags & flag) || fit->x < probe_x || fit->y < probe_y)
			continue;
		const u32 x = fit->x - probe_x;
		const u32 y = fit->y - probe_y;
		if (x + width > img.width || y + height > img.height)
			continue;
		if (++checked > MAX_CANDIDATES)
			break;
		if (rgba32 ? !CropHashesRGBA32(img, x, y, width, height, tex0_hash()) :
		             !CropHashesTrueColour(img, x, y, width, height, tex0_hash(), ta0, aem))
			continue;

		s_matches++;
		s_true_colour_matches++;
		if (rgba32)
			return fmt::format("{}{}:{}:{}:{}:{}", CROP_PREFIX, fit->image, x, y, width, height);
		// TEXA rides along: the loader sets alpha the way the GS expands PSMCT24.
		return fmt::format("{}{}:{}:{}:{}:{}:t{:02x}{}", CROP_PREFIX, fit->image, x, y, width, height, ta0, aem ? 1 : 0);
	}

	s_misses++;
	return {};
}

bool GSDiscAtlas::IsCropFilename(std::string_view filename)
{
	return filename.starts_with(CROP_PREFIX);
}

bool GSDiscAtlas::LoadCrop(const std::string& filename, GSTextureReplacements::ReplacementTexture* tex)
{
	u32 image = 0, x = 0, y = 0, w = 0, h = 0;
	unsigned long long palette_hash = 0;
	const int fields = std::sscanf(filename.c_str() + CROP_PREFIX.size(), "%u:%u:%u:%u:%u:%llx", &image, &x, &y, &w,
		&h, &palette_hash);
	if (fields < 5 || image >= s_state.images.size())
		return false;
	const Image& info = s_state.images[image];

	// A true-colour crop's name ends in `:t<TA0 hex><AEM>` (MatchTrueColour()).
	int ta0 = -1;
	bool aem = false;
	if (info.flags & FLAG_TRUE_COLOUR)
	{
		const size_t t = filename.rfind(":t");
		unsigned int ta0_value = 0, aem_value = 0;
		if (t == std::string::npos || std::sscanf(filename.c_str() + t + 2, "%2x%1u", &ta0_value, &aem_value) != 2)
			return false;
		ta0 = static_cast<int>(ta0_value);
		aem = (aem_value != 0);
	}

	// A palette-free image's file is an HD index map (index in R): paint it with the palette the
	// match was made under.
	std::vector<u32> palette;
	if (info.flags & FLAG_PALETTE_FREE)
	{
		if (fields < 6)
			return false;
		std::unique_lock lock(s_palette_mutex);
		const auto pit = s_palettes.find(palette_hash);
		if (pit == s_palettes.end())
			return false;
		palette = pit->second;
	}

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
	{
		u8* dst = tex->data.data() + static_cast<size_t>(row) * tex->pitch;
		const u8* srow = src + static_cast<size_t>(row) * full.pitch;
		if (ta0 >= 0)
		{
			// The pack keeps the upscaled image's alpha on the PS2 scale: 0 where black was
			// transparent under AEM. Without AEM the GS makes every texel TA0.
			std::memcpy(dst, srow, tex->pitch);
			for (u32 col = 0; col < tex->width; col++)
			{
				u8& a = dst[col * 4 + 3];
				a = aem ? static_cast<u8>(std::min<u32>(a, 0x80) * static_cast<u32>(ta0) / 0x80) : static_cast<u8>(ta0);
			}
			continue;
		}
		if (palette.empty())
		{
			std::memcpy(dst, srow, tex->pitch);
			continue;
		}
		for (u32 col = 0; col < tex->width; col++)
		{
			const u32 index = srow[col * 4];
			const u32 color = (index < palette.size()) ? palette[index] : 0u;
			std::memcpy(dst + col * 4, &color, 4);
		}
	}
	return true;
}
