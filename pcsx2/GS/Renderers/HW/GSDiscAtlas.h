// SPDX-FileCopyrightText: 2026 ARMSX2 Contributors
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "common/Pcsx2Types.h"

#include <string>
#include <string_view>

namespace GSTextureReplacements
{
	struct ReplacementTexture;
}

// Replacement textures cut from whole textures taken off the game disc (fork-only pack format).
//
// A texture pack normally holds one file per texture as the emulator sees it, named by the hash
// cache key - so it can only be made by playing the game with dumping on. Games keep their art on
// the disc, though, and what a draw samples is a rectangle of one of those disc images: Okage's
// drawn textures are all exact crops of its XIM files, at 16-pixel-aligned positions (the PS2's
// block grid), mostly out of 256x256 atlases. `tools/disc_textures` extracts the disc images,
// upscales them whole, and writes `disc-atlas.a2at` next to them: the images' palette-index data,
// their palette hashes, and a table of the hash of every 16x16 index block.
//
// When a texture has no ordinary replacement, the texture cache reads the top-left 16x16 indices
// of its region and asks Match(). Every disc image with that block, under that palette, is a
// candidate; the one whose crop hashes to the key's own TEX0 hash (the hash PCSX2 computed over
// the region's indices) is the texture. So a match is exact, never a guess, and the replacement is
// that crop of the upscaled disc image.
//
// Limits: palette textures (PSMT8/PSMT4) whose key hashes expanded indices, which is every region
// texture and every texture below a block; no mipmapped keys; regions at least 16x16.
namespace GSDiscAtlas
{
	static constexpr u32 TILE = 16;

	/// Loads `disc-atlas.a2at` from the replacement directory, if there is one. Returns whether
	/// an index is now loaded.
	bool Load(const std::string& replacement_dir);
	void Clear();
	bool IsLoaded();
	u32 GetImageCount();

	/// Finds the disc image and position whose crop has this key. `probe` is the XXH3 of the
	/// region's top-left 16x16 palette indices, row by row. Returns a pseudo filename for
	/// LoadCrop(), or an empty string.
	std::string Match(u64 tex0_hash, u64 clut_hash, u32 width, u32 height, u64 probe);

	bool IsCropFilename(std::string_view filename);

	/// Cuts the crop named by a Match() result out of its upscaled disc image. Thread-safe;
	/// called from the replacement loader's worker thread.
	bool LoadCrop(const std::string& filename, GSTextureReplacements::ReplacementTexture* tex);
} // namespace GSDiscAtlas
