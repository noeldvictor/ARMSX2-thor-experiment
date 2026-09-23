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
#include <memory>
#include <optional>
#include <span>
#include <cstring>
#include <list>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <vector>

// disc-atlas.a2at, little-endian:
//   Header               32 bytes (v1, v2) / 40 (v3: + free_tile_count)
//   Image[image_count]   64 bytes each (v1, v2) / 72 (v3: + flags)
//   Tile[tile_count]     24 bytes each, sorted by (clut_hash, tile_hash)
//   FreeTile[free_tile_count]  16 bytes each, sorted by tile_hash (v3: palette-free images;
//                        v4: also true-colour images, whose blocks hash 16x16 RGB texels)
//   index data           each image's palette indices, one byte per texel (PSMT4 expanded), row-major;
//                        a PSMCT24 image's RGB, three bytes per texel; a PSMCT32 image's RGBA, four
// Version 5: the same layout; images may be ASTC 4x4 files (a 4x pack), cropped block by block.
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

	// The index file, memory-mapped: a big game's index is over a gigabyte (Tales of Destiny: 1.1 GB,
	// 12 million blocks), and mapped pages load on demand and can be dropped under memory pressure
	// instead of counting against the app. Unmapped when the atlas is cleared.
	struct MappedFile
	{
		std::span<const u8> span;
		MappedFile() = default;
		MappedFile(MappedFile&& other) noexcept : span(std::exchange(other.span, {})) {}
		MappedFile& operator=(MappedFile&& other) noexcept
		{
			if (this != &other)
			{
				Reset();
				span = std::exchange(other.span, {});
			}
			return *this;
		}
		~MappedFile() { Reset(); }
		void Reset()
		{
			if (!span.empty())
				FileSystem::UnmapFile(span);
			span = {};
		}
	};

	struct State
	{
		std::string dir;
		std::vector<Image> images;
		std::span<const Tile> tiles; // these three point into `mapped` (or `owned`)
		std::span<const FreeTile> free_tiles;
		std::span<const u8> index_data;
		MappedFile mapped;
		std::vector<u8> owned; // the whole file, where it cannot be mapped
	};

	State s_state;
	bool s_loaded = false;
	u32 s_tile_step = 16;
	u32 s_matches = 0;
	u32 s_misses = 0;
	u32 s_palette_free_matches = 0;
	u32 s_true_colour_matches = 0;
	u32 s_composite_matches = 0;

	// Composites matched on the GS thread, loaded on the worker (MatchComposite, LoadComposite).
	struct Composite
	{
		struct Piece
		{
			u32 image;
			int x; // the image's origin in texture coordinates (may be outside the texture)
			int y;
			std::vector<u32> native; // texels (y << 16 | x) where the texture differs from the image
		};
		u32 width;
		u32 height;
		std::vector<u8> indices; // the texture's own indices: what no piece covers keeps them
		std::vector<u32> clut;
		std::vector<Piece> pieces;
	};
	std::mutex s_composite_mutex;
	std::vector<std::shared_ptr<const Composite>> s_composites;
	constexpr std::string_view COMPOSITE_TAG = "c:";

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

	State st;
	st.dir = replacement_dir;
	st.mapped.span = FileSystem::MapBinaryFileForRead(path.c_str());
	std::span<const u8> file = st.mapped.span;
	if (file.empty())
	{
		std::optional<std::vector<u8>> read = FileSystem::ReadBinaryFile(path.c_str());
		if (read.has_value())
		{
			st.owned = std::move(*read);
			file = st.owned;
		}
	}
	if (file.size() < sizeof(Header))
	{
		Console.Error(fmt::format("Disc atlas: cannot read {}", path));
		return false;
	}

	Header hdr;
	std::memcpy(&hdr, file.data(), sizeof(hdr));
	HeaderV3Tail tail{};
	const bool v3 = hdr.version >= 3;
	if (v3 && file.size() >= sizeof(Header) + sizeof(HeaderV3Tail))
		std::memcpy(&tail, file.data() + sizeof(Header), sizeof(tail));
	const u64 header_size = sizeof(Header) + (v3 ? sizeof(HeaderV3Tail) : 0);
	const u64 image_size = v3 ? sizeof(ImageV3) : sizeof(ImageV2);
	const u64 images_end = header_size + static_cast<u64>(hdr.image_count) * image_size;
	const u64 tiles_end = images_end + static_cast<u64>(hdr.tile_count) * sizeof(Tile);
	const u64 free_end = tiles_end + static_cast<u64>(tail.free_tile_count) * sizeof(FreeTile);
	const u32 step = (hdr.version >= 2) ? hdr.tile_step : TILE;
	if (std::memcmp(hdr.magic, "A2AT", 4) != 0 || hdr.version < 1 || hdr.version > 5 || hdr.tile_size != TILE ||
		step == 0 || (TILE % step) != 0 ||
		free_end > file.size() || hdr.index_data_offset > file.size())
	{
		Console.Error(fmt::format("Disc atlas: {} is not a version 1-5 index", path));
		return false;
	}

	st.images.resize(hdr.image_count);
	for (u32 i = 0; i < hdr.image_count; i++)
	{
		const u8* rec = file.data() + header_size + i * image_size;
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
	// Packed structs (alignment 1), so the file's bytes are used where they are.
	st.tiles = {reinterpret_cast<const Tile*>(file.data() + images_end), hdr.tile_count};
	st.free_tiles = {reinterpret_cast<const FreeTile*>(file.data() + tiles_end), tail.free_tile_count};
	st.index_data = file.subspan(hdr.index_data_offset);

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
	Console.WriteLnFmt("Disc atlas: {} disc images, {} index blocks every {} px, {} palette-free/true-colour, {} MB {} ({})",
		s_state.images.size(), s_state.tiles.size(), step, s_state.free_tiles.size(), file.size() >> 20,
		s_state.mapped.span.empty() ? "read" : "mapped", path);
	return true;
}

void GSDiscAtlas::Clear()
{
	if (s_loaded && (s_matches || s_misses))
	{
		Console.WriteLnFmt("Disc atlas: {} matches ({} palette-free, {} true-colour, {} composites), {} misses", s_matches,
			s_palette_free_matches, s_true_colour_matches, s_composite_matches, s_misses);
	}
	s_loaded = false;
	s_state = {};
	s_tile_step = 16;
	s_matches = 0;
	s_misses = 0;
	s_palette_free_matches = 0;
	s_true_colour_matches = 0;
	s_composite_matches = 0;
	{
		std::unique_lock lock(s_palette_mutex);
		s_palettes.clear();
	}
	{
		std::unique_lock lock(s_composite_mutex);
		s_composites.clear();
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
	return {static_cast<u32>(s_state.images.size()), s_matches, s_misses, s_palette_free_matches, s_true_colour_matches,
		s_composite_matches};
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

std::string GSDiscAtlas::MatchComposite(const u8* indices, u32 width, u32 height, u64 clut_hash, const u32* clut,
	u32 clut_entries)
{
	if (!s_loaded || width < TILE || height < TILE || !clut || clut_entries == 0)
		return {};

	// Vote: every block of the texture on the probe grid that some disc image holds suggests that
	// image at one position. Blocks of one flat value are everywhere and prove nothing.
	std::unordered_map<u64, u32> votes;
	const auto vote = [&](u32 image, int ox, int oy) {
		votes[(static_cast<u64>(image) << 32) | (static_cast<u64>(static_cast<u16>(ox + 0x8000)) << 16) |
			  static_cast<u16>(oy + 0x8000)]++;
	};
	const u32 step = s_tile_step;
	u8 block[TILE * TILE];
	for (u32 by = 0; by + TILE <= height; by += step)
	{
		for (u32 bx = 0; bx + TILE <= width; bx += step)
		{
			for (u32 r = 0; r < TILE; r++)
				std::memcpy(&block[r * TILE], &indices[(by + r) * width + bx], TILE);
			if (std::all_of(block, block + sizeof(block), [&](u8 v) { return v == block[0]; }))
				continue;
			const u64 h = GSXXH3_64bits(block, sizeof(block));
			const std::pair<u64, u64> key{clut_hash, h};
			auto it = std::lower_bound(s_state.tiles.begin(), s_state.tiles.end(), key,
				[](const Tile& t, const std::pair<u64, u64>& k) {
					return t.clut_hash < k.first || (t.clut_hash == k.first && t.tile_hash < k.second);
				});
			for (u32 n = 0; it != s_state.tiles.end() && it->clut_hash == clut_hash && it->tile_hash == h && n < 64; ++it, n++)
				vote(it->image, static_cast<int>(bx) - it->x, static_cast<int>(by) - it->y);
			auto fit = std::lower_bound(s_state.free_tiles.begin(), s_state.free_tiles.end(), h,
				[](const FreeTile& t, u64 v) { return t.tile_hash < v; });
			for (u32 n = 0; fit != s_state.free_tiles.end() && fit->tile_hash == h && n < 64; ++fit, n++)
			{
				if (!(s_state.images[fit->image].flags & (FLAG_TRUE_COLOUR | FLAG_RGBA32)))
					vote(fit->image, static_cast<int>(bx) - fit->x, static_cast<int>(by) - fit->y);
			}
		}
	}
	if (votes.empty())
		return {};

	std::vector<std::pair<u32, u64>> order;
	order.reserve(votes.size());
	for (const auto& [k, v] : votes)
		order.emplace_back(v, k);
	std::sort(order.begin(), order.end(), std::greater<>());

	// Accept placements whose covered texels equal the texture, allowing up to 1/256 of them to
	// differ: those keep their native colour. Games park a few bytes inside a texture's memory
	// (Tales of Destiny's deck maps differ from the disc in 5-22 texels), and one stray texel must
	// not cost the whole image. Every texel shown is still either an exact match or native.
	std::vector<u8> covered(static_cast<size_t>(width) * height, 0);
	auto comp = std::make_shared<Composite>();
	u32 checked = 0;
	size_t patched = 0;
	for (const auto& [n, k] : order)
	{
		if (++checked > 256)
			break;
		const u32 image = static_cast<u32>(k >> 32);
		const int ox = static_cast<int>((k >> 16) & 0xFFFF) - 0x8000;
		const int oy = static_cast<int>(k & 0xFFFF) - 0x8000;
		const Image& img = s_state.images[image];
		const int x0 = std::max(ox, 0), y0 = std::max(oy, 0);
		const int x1 = std::min(ox + static_cast<int>(img.width), static_cast<int>(width));
		const int y1 = std::min(oy + static_cast<int>(img.height), static_cast<int>(height));
		if (x1 - x0 < static_cast<int>(TILE) || y1 - y0 < static_cast<int>(TILE))
			continue;
		const size_t allowed = static_cast<size_t>(x1 - x0) * (y1 - y0) / 256;
		std::vector<u32> native;
		bool equal = true;
		for (int y = y0; y < y1 && equal; y++)
		{
			const u8* disc = s_state.index_data.data() + img.index_offset + static_cast<size_t>(y - oy) * img.width + (x0 - ox);
			const u8* tex = &indices[static_cast<size_t>(y) * width + x0];
			if (std::memcmp(disc, tex, x1 - x0) == 0)
				continue;
			for (int x = 0; x < x1 - x0 && equal; x++)
			{
				if (disc[x] == tex[x])
					continue;
				equal = native.size() < allowed;
				native.push_back((static_cast<u32>(y) << 16) | static_cast<u32>(x0 + x));
			}
		}
		if (!equal)
			continue;
		for (int y = y0; y < y1; y++)
			std::memset(&covered[static_cast<size_t>(y) * width + x0], 1, x1 - x0);
		for (u32 t : native)
			covered[static_cast<size_t>(t >> 16) * width + (t & 0xFFFF)] = 0;
		patched += native.size();
		comp->pieces.push_back({image, ox, oy, std::move(native)});
	}
	if (comp->pieces.empty())
		return {};

	// Worth it only if the pieces carry most of what is drawn (index 0 is usually background).
	size_t drawn = 0, drawn_covered = 0;
	for (size_t i = 0; i < covered.size(); i++)
	{
		if (indices[i] != 0)
		{
			drawn++;
			drawn_covered += covered[i];
		}
	}
	if (drawn == 0 || drawn_covered * 2 < drawn)
		return {};

	comp->width = width;
	comp->height = height;
	comp->indices.assign(indices, indices + static_cast<size_t>(width) * height);
	comp->clut.assign(clut, clut + clut_entries);
	u32 id;
	{
		std::unique_lock lock(s_composite_mutex);
		id = static_cast<u32>(s_composites.size());
		s_composites.push_back(std::move(comp));
	}
	s_matches++;
	s_misses--; // Match() counted this texture as a miss first
	if (++s_composite_matches <= 8)
	{
		Console.WriteLnFmt("Disc atlas: composite #{} {}x{} from {} disc images, {}% of drawn texels, {} texels native",
			s_composite_matches, width, height, s_composites[id]->pieces.size(), drawn_covered * 100 / drawn, patched);
	}
	return fmt::format("{}{}{}", CROP_PREFIX, COMPOSITE_TAG, id);
}

// An upscaled disc image from the cache, loading it if needed: RGBA8 (PNG) or ASTC 4x4 (as the
// GPU will sample it - the loader refuses it on a device without ASTC). Call with s_image_mutex held.
static const GSTextureReplacements::ReplacementTexture* CachedImage(u32 image)
{
	auto cached = s_image_cache.find(image);
	if (cached != s_image_cache.end())
	{
		s_image_lru.remove(image);
		s_image_lru.push_back(image);
		return &cached->second;
	}
	const Image& info = s_state.images[image];
	const std::string path = Path::Combine(s_state.dir, std::string_view(info.file, strnlen(info.file, sizeof(info.file))));
	const GSTextureReplacements::ReplacementTextureLoader loader = GSTextureReplacements::GetLoader(path);
	GSTextureReplacements::ReplacementTexture full;
	if (!loader || !loader(path, &full, true) ||
		(full.format != GSTexture::Format::Color && full.format != GSTexture::Format::ASTC4x4))
	{
		Console.Warning(fmt::format("Disc atlas: cannot load {} as RGBA8 or ASTC 4x4", path));
		return nullptr;
	}
	const size_t bytes = full.data.size();
	while (s_image_cache_bytes + bytes > IMAGE_CACHE_BUDGET && !s_image_lru.empty())
	{
		auto victim = s_image_cache.find(s_image_lru.front());
		s_image_cache_bytes -= victim->second.data.size();
		s_image_cache.erase(victim);
		s_image_lru.pop_front();
	}
	s_image_cache_bytes += bytes;
	s_image_lru.push_back(image);
	return &s_image_cache.emplace(image, std::move(full)).first->second;
}

// The scale of an upscaled disc image, or 0 if it is not a whole multiple of the native size.
static u32 ImageScale(const Image& info, const GSTextureReplacements::ReplacementTexture& full)
{
	const u32 scale = full.width / info.width;
	return (scale && full.width == info.width * scale && full.height == info.height * scale) ? scale : 0;
}

static constexpr u32 ASTC_BLOCK = 4;
static constexpr u32 ASTC_BLOCK_BYTES = 16;

// An ASTC "void-extent" block: one exact colour for all 16 texels (8-bit channels as UNORM16).
static void ConstantASTCBlock(u8* dst, u32 rgba)
{
	const u64 lo = 0xFFFFFFFFFFFFFDFCull; // void-extent marker, LDR, no extent coordinates
	u64 hi = 0;
	for (u32 c = 0; c < 4; c++)
		hi |= static_cast<u64>(((rgba >> (c * 8)) & 0xFF) * 257) << (c * 16);
	std::memcpy(dst, &lo, 8);
	std::memcpy(dst + 8, &hi, 8);
}

// Copies a rectangle of 4x4 blocks: (bx, by, bw, bh) in block units of `full` into `dst`.
static void CopyASTCBlocks(const GSTextureReplacements::ReplacementTexture& full, u32 bx, u32 by, u32 bw, u32 bh,
	u8* dst, u32 dst_pitch)
{
	for (u32 row = 0; row < bh; row++)
	{
		std::memcpy(dst + static_cast<size_t>(row) * dst_pitch,
			full.data.data() + static_cast<size_t>(by + row) * full.pitch + static_cast<size_t>(bx) * ASTC_BLOCK_BYTES,
			static_cast<size_t>(bw) * ASTC_BLOCK_BYTES);
	}
}

static bool LoadComposite(u32 id, GSTextureReplacements::ReplacementTexture* tex)
{
	std::shared_ptr<const Composite> comp;
	{
		std::unique_lock lock(s_composite_mutex);
		if (id >= s_composites.size())
			return false;
		comp = s_composites[id];
	}
	const auto colour = [&](u8 index) { return index < comp->clut.size() ? comp->clut[index] : 0u; };

	std::unique_lock lock(s_image_mutex);

	// ASTC when any piece is ASTC at 4x: one native texel is one 4x4 block, so the composite is
	// assembled block by block - each piece's blocks copied, every other texel a constant block of
	// its native colour. Palette-free pieces (index maps) cannot be ASTC and stay native here.
	bool astc = false;
	u32 scale = 0;
	for (const Composite::Piece& piece : comp->pieces)
	{
		if (s_state.images[piece.image].flags & FLAG_PALETTE_FREE)
			continue;
		const GSTextureReplacements::ReplacementTexture* full = CachedImage(piece.image);
		if (!full)
			return false;
		astc = full->format == GSTexture::Format::ASTC4x4;
		scale = ImageScale(s_state.images[piece.image], *full);
		break;
	}
	if (astc)
	{
		if (scale != ASTC_BLOCK)
			return false;
		tex->width = comp->width * ASTC_BLOCK;
		tex->height = comp->height * ASTC_BLOCK;
		tex->format = GSTexture::Format::ASTC4x4;
		tex->pitch = comp->width * ASTC_BLOCK_BYTES;
		tex->data.resize(static_cast<size_t>(tex->pitch) * comp->height);
		tex->mips.clear();
		for (u32 y = 0; y < comp->height; y++)
		{
			for (u32 x = 0; x < comp->width; x++)
			{
				ConstantASTCBlock(tex->data.data() + static_cast<size_t>(y) * tex->pitch + x * ASTC_BLOCK_BYTES,
					colour(comp->indices[static_cast<size_t>(y) * comp->width + x]));
			}
		}
		for (const Composite::Piece& piece : comp->pieces)
		{
			const Image& info = s_state.images[piece.image];
			if (info.flags & FLAG_PALETTE_FREE)
				continue;
			const GSTextureReplacements::ReplacementTexture* full = CachedImage(piece.image);
			if (!full || full->format != GSTexture::Format::ASTC4x4 || ImageScale(info, *full) != ASTC_BLOCK)
				return false;
			const int x0 = std::max(piece.x, 0), y0 = std::max(piece.y, 0);
			const int x1 = std::min(piece.x + static_cast<int>(info.width), static_cast<int>(comp->width));
			const int y1 = std::min(piece.y + static_cast<int>(info.height), static_cast<int>(comp->height));
			CopyASTCBlocks(*full, x0 - piece.x, y0 - piece.y, x1 - x0, y1 - y0,
				tex->data.data() + static_cast<size_t>(y0) * tex->pitch + static_cast<size_t>(x0) * ASTC_BLOCK_BYTES,
				tex->pitch);
			for (u32 t : piece.native)
			{
				const u32 x = t & 0xFFFF, y = t >> 16;
				ConstantASTCBlock(tex->data.data() + static_cast<size_t>(y) * tex->pitch + x * ASTC_BLOCK_BYTES,
					colour(comp->indices[static_cast<size_t>(y) * comp->width + x]));
			}
		}
		return true;
	}

	// RGBA8: PNG pieces (a PNG pack, or a composite of palette-free images only).
	for (const Composite::Piece& piece : comp->pieces)
	{
		const GSTextureReplacements::ReplacementTexture* full = CachedImage(piece.image);
		if (!full || full->format != GSTexture::Format::Color)
			return false;
		scale = ImageScale(s_state.images[piece.image], *full);
		break;
	}
	if (scale == 0)
		return false;
	tex->width = comp->width * scale;
	tex->height = comp->height * scale;
	tex->format = GSTexture::Format::Color;
	tex->pitch = tex->width * 4;
	tex->data.resize(static_cast<size_t>(tex->pitch) * tex->height);
	tex->mips.clear();

	// What no piece covers keeps its native colour, scaled up.
	for (u32 y = 0; y < tex->height; y++)
	{
		u32* dst = reinterpret_cast<u32*>(tex->data.data() + static_cast<size_t>(y) * tex->pitch);
		const u8* src = &comp->indices[static_cast<size_t>(y / scale) * comp->width];
		for (u32 x = 0; x < tex->width; x++)
			dst[x] = colour(src[x / scale]);
	}

	for (const Composite::Piece& piece : comp->pieces)
	{
		const Image& info = s_state.images[piece.image];
		const GSTextureReplacements::ReplacementTexture* full = CachedImage(piece.image);
		if (!full || full->format != GSTexture::Format::Color || ImageScale(info, *full) != scale)
			return false; // mixed scales or formats: leave the texture native rather than half-built
		const bool index_map = (info.flags & FLAG_PALETTE_FREE) != 0;
		const int x0 = std::max(piece.x, 0), y0 = std::max(piece.y, 0);
		const int x1 = std::min(piece.x + static_cast<int>(info.width), static_cast<int>(comp->width));
		const int y1 = std::min(piece.y + static_cast<int>(info.height), static_cast<int>(comp->height));
		for (int y = y0 * static_cast<int>(scale); y < y1 * static_cast<int>(scale); y++)
		{
			u8* dst = tex->data.data() + static_cast<size_t>(y) * tex->pitch + static_cast<size_t>(x0) * scale * 4;
			const u8* src = full->data.data() + static_cast<size_t>(y - piece.y * static_cast<int>(scale)) * full->pitch +
			                static_cast<size_t>(x0 - piece.x) * scale * 4;
			const size_t n = static_cast<size_t>(x1 - x0) * scale;
			if (!index_map)
			{
				std::memcpy(dst, src, n * 4);
				continue;
			}
			for (size_t i = 0; i < n; i++)
			{
				const u32 c = colour(src[i * 4]);
				std::memcpy(dst + i * 4, &c, 4);
			}
		}
		for (u32 t : piece.native)
		{
			const u32 x = t & 0xFFFF, y = t >> 16;
			const u32 c = colour(comp->indices[static_cast<size_t>(y) * comp->width + x]);
			for (u32 sy = 0; sy < scale; sy++)
			{
				u32* dst = reinterpret_cast<u32*>(tex->data.data() + static_cast<size_t>(y * scale + sy) * tex->pitch) + x * scale;
				std::fill(dst, dst + scale, c);
			}
		}
	}
	return true;
}

bool GSDiscAtlas::IsCropFilename(std::string_view filename)
{
	return filename.starts_with(CROP_PREFIX);
}

bool GSDiscAtlas::LoadCrop(const std::string& filename, GSTextureReplacements::ReplacementTexture* tex)
{
	if (std::string_view(filename).substr(CROP_PREFIX.size()).starts_with(COMPOSITE_TAG))
	{
		u32 id = 0;
		return std::sscanf(filename.c_str() + CROP_PREFIX.size() + COMPOSITE_TAG.size(), "%u", &id) == 1 &&
		       LoadComposite(id, tex);
	}

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
	const GSTextureReplacements::ReplacementTexture* full = CachedImage(image);
	if (!full)
		return false;
	const u32 scale = ImageScale(info, *full);
	if (scale == 0)
	{
		Console.Warning(fmt::format("Disc atlas: {}x{} is not a whole multiple of {}x{}", full->width,
			full->height, info.width, info.height));
		return false;
	}

	if (full->format == GSTexture::Format::ASTC4x4)
	{
		// Cut by blocks. The pack only stores ASTC at 4x, where every crop edge is on the block
		// grid. A true-colour crop needs its alpha rebuilt from TEXA unless the stored alpha is
		// already it (AEM with TA0 0x80, what the pack assumed) - ASTC cannot be edited, so any
		// other TEXA leaves the texture native.
		if ((x * scale) % ASTC_BLOCK || (y * scale) % ASTC_BLOCK || (w * scale) % ASTC_BLOCK ||
			(h * scale) % ASTC_BLOCK || (info.flags & FLAG_PALETTE_FREE) || (ta0 >= 0 && !(aem && ta0 == 0x80)))
		{
			return false;
		}
		tex->width = w * scale;
		tex->height = h * scale;
		tex->format = GSTexture::Format::ASTC4x4;
		tex->pitch = (tex->width / ASTC_BLOCK) * ASTC_BLOCK_BYTES;
		tex->data.resize(static_cast<size_t>(tex->pitch) * (tex->height / ASTC_BLOCK));
		tex->mips.clear();
		CopyASTCBlocks(*full, x * scale / ASTC_BLOCK, y * scale / ASTC_BLOCK, tex->width / ASTC_BLOCK,
			tex->height / ASTC_BLOCK, tex->data.data(), tex->pitch);
		return true;
	}

	tex->width = w * scale;
	tex->height = h * scale;
	tex->format = GSTexture::Format::Color;
	tex->pitch = tex->width * 4;
	tex->data.resize(static_cast<size_t>(tex->pitch) * tex->height);
	tex->mips.clear();
	const u8* src = full->data.data() + static_cast<size_t>(y * scale) * full->pitch + static_cast<size_t>(x * scale) * 4;
	for (u32 row = 0; row < tex->height; row++)
	{
		u8* dst = tex->data.data() + static_cast<size_t>(row) * tex->pitch;
		const u8* srow = src + static_cast<size_t>(row) * full->pitch;
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
