// SPDX-FileCopyrightText: 2002-2026 PCSX2 Dev Team
// SPDX-License-Identifier: GPL-3.0+

#pragma once

#include "GS/Renderers/HW/GSTextureCache.h"

#include <utility>

namespace GSTextureReplacements
{
	struct ReplacementTexture
	{
		u32 width;
		u32 height;
		GSTexture::Format format;
		std::pair<u8, u8> alpha_minmax;

		u32 pitch;
		std::vector<u8> data;

		struct MipData
		{
			u32 width;
			u32 height;
			u32 pitch;
			std::vector<u8> data;
		};
		std::vector<MipData> mips;
	};

	void Initialize();
	void GameChanged();
	void ReloadReplacementMap();
	void UpdateConfig(Pcsx2Config::GSOptions& old_config);
	void Shutdown();

	u32 CalcMipmapLevelsForReplacement(u32 width, u32 height);

	bool HasAnyReplacementTextures();
	/// A disc atlas (GSDiscAtlas.h) is loaded for the running game.
	bool HasDiscAtlas();
	/// Asks the disc atlas for a crop with this key; `probe` is the XXH3 of the 16x16 palette
	/// indices starting (probe_x, probe_y) into the texture. On a match the crop is registered under
	/// the key's name, so LookupReplacementTexture() finds it like any other replacement.
	bool LookupDiscAtlas(const GSTextureCache::HashCacheKey& hash, u64 probe, u32 probe_x, u32 probe_y,
		const u32* clut, u32 clut_entries);
	bool HasReplacementTextureWithOtherPalette(const GSTextureCache::HashCacheKey& hash);
	GSTexture* LookupReplacementTexture(const GSTextureCache::HashCacheKey& hash, bool mipmap, bool* pending, std::pair<u8, u8>* alpha_minmax);
	GSTexture* CreateReplacementTexture(const ReplacementTexture& rtex, bool mipmap);
	void ProcessAsyncLoadedTextures();

	void DumpTexture(const GSTextureCache::HashCacheKey& hash, const GIFRegTEX0& TEX0, const GIFRegTEXA& TEXA,
		GSTextureCache::SourceRegion region, GSLocalMemory& mem, u32 level);
	void ClearDumpedTextureList();

	/// Get the number of textures that have been dumped.
	u32 GetDumpedTextureCount();

	/// Get the number of replacement textures that have been loaded/cached.
	u32 GetLoadedTextureCount();

	/// Loader will take a filename and interpret the format (e.g. DDS, PNG, etc).
	using ReplacementTextureLoader = bool (*)(const std::string& filename, GSTextureReplacements::ReplacementTexture* tex, bool only_base_image);
	ReplacementTextureLoader GetLoader(const std::string_view filename);

	/// Load a validated KTX1 ASTC chain. Device capability is checked by the registered loader.
	bool LoadKTXTexture(const std::string& filename, ReplacementTexture* tex, u32 max_texture_size);

	/// Saves an image buffer to a PNG file (for dumping).
	bool SavePNGImage(const std::string& filename, u32 width, u32 height, const u8* buffer, u32 pitch);
} // namespace GSTextureReplacements
