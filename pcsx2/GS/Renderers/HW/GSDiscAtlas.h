// SPDX-FileCopyrightText: 2026 ARMSX2 Contributors
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "common/Pcsx2Types.h"

#include <functional>
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
// Menus and fonts are different: the game declares one 1024x1024 texture over most of GS memory
// and picks each sprite by UV, so the stock key hashes the whole sheet, junk included. With a disc
// atlas loaded, GSRendererHW narrows such draws to the sprite's own UV rectangle (a tight region),
// which can start anywhere. The index therefore holds blocks every `tile_step` pixels (8 in version
// 2: the 8x8 block grid of PSMT8H/PSMT4HL sheets), and the probe is the first block of the region
// on that grid, with its offset from the region's corner.
//
// Some images have no palette of their own: the game colours them at runtime (Okage's fonts, sheets
// it uploads from .FNT files and paints with palettes it makes on the fly). Version 3 indexes carry
// those as palette-free images: their HD file is an HD *index map*, and their blocks sit in a second
// table keyed by the block alone. The key's TEX0 hash covers only indices, so the match is as exact
// as ever; the texture's palette at match time is kept, and the loader paints the map with it.
//
// True-colour images (version 4): PSMCT24 textures are hashed expanded too - RGB plus an alpha
// the GS makes from TEXA (TA0, or 0 for black under AEM). Their blocks are indexed by RGB alone,
// so the probe is the same whatever TEXA the game uses; the crop check rebuilds the alpha from the
// key's TEXA, and the loader applies it to the HD image.
//
// The check is the atlas's own. The replacement key's TEX0 hash is expanded texels for region
// textures, but raw swizzled GS blocks for a full-size texture of a 32-bit format (and some palette
// formats), which disc data cannot reproduce. So the crop is checked against the atlas's own
// *content hash* of the texture: XXH3 of its texels as the GS reads them, row by row - palette
// indices for palette formats, RGBA8 as expanded with TEXA for PSMCT24/32 - computed only once a
// probe block has a candidate. It is as exact as the key's hash (every texel compared), and the key
// stays PCSX2's, so standard packs and dumps are untouched: a texture with its own file never gets
// here.
//
// PSMCT32 images (index version 4, a third flag) keep RGBA, four bytes a texel; their blocks are
// indexed by RGB like PSMCT24's.
//
// Composites: many games assemble a texture in VRAM out of several disc images - a sprite sheet
// that frames are uploaded into, a line of text pasted together from glyphs - and draw a region
// that spans several of them, which no single crop matches. MatchComposite() takes the texture's
// indices, finds the disc images whose 16x16 blocks appear in it (each hit votes for one image at
// one position), and accepts a placement only if every texel it covers equals the texture. Texels
// no accepted image covers keep their native colour. So a composite is as exact as a crop, and the
// replacement is the HD images laid out the same way. Palette textures only.
//
// Limits: palette textures (PSMT8/PSMT4 and their H variants), PSMCT24 and PSMCT32; single-level
// keys; textures that hold a whole 16x16 block on the probe grid.
namespace GSDiscAtlas
{
	static constexpr u32 TILE = 16;

	struct Stats
	{
		u32 images;
		u32 matches;
		u32 misses;
		u32 palette_free_matches;
		u32 true_colour_matches;
		u32 composite_matches;
	};

	/// Loads `disc-atlas.a2at` from the replacement directory, if there is one. Returns whether
	/// an index is now loaded.
	bool Load(const std::string& replacement_dir);
	void Clear();
	bool IsLoaded();
	u32 GetImageCount();
	/// Grid the probe block has to sit on (16 in version 1 indexes, 8 in version 2).
	u32 GetTileStep();
	Stats GetStats();

	/// The texture's content hash (see above); called at most once, and only if a candidate turns up.
	using ContentHash = std::function<u64()>;

	/// Finds the disc image and position whose crop is this texture. `probe` is the XXH3 of the
	/// 16x16 palette indices, row by row, of the block that starts (probe_x, probe_y) into the
	/// region. `clut` / `clut_entries` are the texture's palette, used when the match is a
	/// palette-free image. Returns a pseudo filename for LoadCrop(), or an empty string.
	std::string Match(const ContentHash& content_hash, u64 clut_hash, u32 width, u32 height, u64 probe, u32 probe_x,
		u32 probe_y, const u32* clut, u32 clut_entries);

	/// The same for a PSMCT24 or PSMCT32 (`rgba32`) texture: `probe` is the XXH3 of the probe
	/// block's RGB, three bytes a texel, row by row; `ta0` / `aem` are the TEXA a PSMCT24 texture
	/// is expanded with.
	std::string MatchTrueColour(const ContentHash& content_hash, u32 width, u32 height, u64 probe, u32 probe_x,
		u32 probe_y, u8 ta0, bool aem, bool rgba32);

	/// The texture as several disc images side by side (see above). `indices` are its width x
	/// height palette indices as the GS reads them; `clut` / `clut_entries` its palette.
	std::string MatchComposite(const u8* indices, u32 width, u32 height, u64 clut_hash, const u32* clut,
		u32 clut_entries);

	bool IsCropFilename(std::string_view filename);

	/// Cuts the crop named by a Match() result out of its upscaled disc image. Thread-safe;
	/// called from the replacement loader's worker thread.
	bool LoadCrop(const std::string& filename, GSTextureReplacements::ReplacementTexture* tex);
} // namespace GSDiscAtlas
