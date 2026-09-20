<div align="center">

![ARMSX2 Thor Experiment](docs/media/armsx2-thor-experiment-hero.png)

# ARMSX2 Thor Experiment

Personal, unsupported AYN Thor experiment fork of ARMSX2.

![License](https://img.shields.io/badge/license-GPLv3-42e0ff)
![Status](https://img.shields.io/badge/status-experiment-ffb039)
![AI](https://img.shields.io/badge/vibe--coded_with-AI-6ee7b7)

</div>

This is a fork I am using to make ARMSX2 feel better on the AYN Thor. It is vibe-coded with AI, changed quickly, and intentionally opinionated. No stability guarantee, no support promise, no polish promise.

If that bothers you, look elsewhere. Fork it, break it, fix it, make it yours.

Please do not open issues expecting support or a roadmap. This is not a product queue. It is a personal experiment that happens to be public.

Bring your own legally dumped PS2 BIOS and games. Use this only for personal experimentation.

## Screenshots

Screenshots are from a personal AYN Thor test device.

| Game selector | Texture upscaling, in-game |
| --- | --- |
| ![ARMSX2 Thor game selector with cover art and cheat badges](docs/media/armsx2-library-device.png) | ![Texture upscaling section in the in-game menu, showing the world-texture toggle, the Bilinear/Scale2x/Eagle algorithm picker and the 2x/4x scale selector](docs/media/armsx2-texture-upscaling-device.png) |

| Running, no touch overlay |
| --- |
| ![7 Blades running on the Thor with no on-screen controls drawn over the game](docs/media/armsx2-ingame-device.png) |

## Where This Fork Diverges

- AYN Thor is the target device for layout, workflow, and sanity checks.
- The game selector favors cover-first browsing and hardcoded xlenore PS2 cover defaults.
- Cover cards show `CHEATS` only when real `.pnach` files exist in the selected ARMSX2 data root `cheats` folder.
- Cover cards also show an `HD` badge: solid when a texture pack is installed for that game, hollow when the online catalogue has one to download. Tapping it, or the long-press menu row, opens Texture Packs with that game selected. Installed means the pack folder exists, so hand-copied packs count too.
- Bundled real cheat PNACHs from the NetherSX2 patch collection are included under `platforms/android/app/src/main/assets/cheats` and copied only when missing.
- Cheat controls are split by PNACH section so each cheat section can be toggled on its own.
- Widescreen and 60 FPS patch metadata are intentionally not used for cheat badges or cheat switches.
- The Compose pause menu has Thor-friendly shortcuts for renderer changes, fast forward, game state, disc changes, imports, and individual cheat toggles.
- The refreshed upstream Kotlin/Compose frontend and PCSX2-derived native core are the active path.
- Texture upscaling runs per texture as the emulator uploads it, not on the finished frame, with separate settings for world and UI textures. Because the result is cached, a texture is scaled once rather than every frame.
- Seventeen filters grouped by how they decide — resample, pixel art, edge-directed, neural — plus a Deposterize pre-pass for the banding 16-bit PS2 textures leave in gradients.
- Scale and algorithm are changeable from the in-game menu, so filters can be compared without leaving the game.
- Scaling runs on a worker thread; the native texture is drawn immediately and the upscaled one swaps in when it is ready.
- A texture covered by an HD pack is never upscaled, even while the pack texture is still loading; the upscaler only fills in what the pack leaves native.
- **RAISR-HD**: learned upscaling kernels fit on the HD texture packs, bundled in the APK and on by default, so a game with no pack still gets a sharper, edge-aware 2x on the fly. Not a neural net; a few hundred operations per pixel on the existing worker thread. On held-out pack textures it beats Lanczos by +0.1 to +3.6 dB depending on the art. `tools/raisr_train.py` fits the kernels from any pack.
- A **Reload textures** button in the pause menu and a matching hotkey re-run every visible texture through the current filter, so switching filters mid-game is instantly visible.
- An on-device **MCP dev server** (github flavor only, off by default, localhost over `adb forward`) can boot games, read and write settings, save and load states, capture screenshots and read upscaler counters, so the filters can be compared by a script rather than by hand.
- On-screen touch controls default to off, and the top-right pause glyph goes with them. The Thor has physical sticks and buttons, so the overlay was covering the game to duplicate controls already under your thumbs. That corner stays tappable either way.

## Exploration Notes

Notes on what I am poking at. The texture filters are built and running; most of the rest is not. No promises, no dates.

- [Texture upscaling on AYN Thor](docs/texture-upscaling-research.md) — upscaling each texture inside the emulator as you play, rather than upscaling the screen. Why that cost model works on a handheld and what would sink it.
- [Third-party ports](docs/third-party.md) — where the filters come from, their licences, and the two places I deliberately match a reference's quirk rather than "fixing" it.
- [Neural models](docs/neural-models.md) — the `.a2nn` format, why no weights ship, and a tool that proves the path works before you have any.
- [ARM64 optimization review](docs/arm64-optimization-review.md) — the Thor is four different CPU cores, and local debug builds were quietly testing different codegen than every release.
- [Cheat tooling](docs/cheat-tooling.md) — measured: 46% of the games on my card have no bundled cheats, and the public collections turn out to be the same set we already bundle. Closing that gap means authoring, not importing.
- [RAISR kernel trainer](tools/raisr_train.py) — fits the RAISR-HD kernels from HD packs and reports PSNR/SSIM against Lanczos on held-out textures. Kernels are general, not per game.
- [Texture pack getter](docs/texture-pack-getter.md) — written before upstream shipped its own catalogue and one-tap installer, which this fork now inherits. Kept for the reasoning; the fork's part is the cover badge.
- [On-device MCP server](docs/mcp-server.md) — now built: a localhost control surface over `adb forward`, so comparing twenty upscalers is a loop instead of an afternoon of menu-poking.

## What This Is Not

- Not official ARMSX2.
- Not PCSX2.
- Not a general support fork.
- Not a compatibility promise.
- Not a place to request features.

## Build From Source

From the repo root on Windows:

```powershell
Set-Location platforms\android
.\gradlew.bat :app:assembleGithubDebug
```

For a quicker Kotlin/Compose check:

```powershell
Set-Location platforms\android
.\gradlew.bat :app:compileGithubDebugKotlin
```

## Credits

This fork stands on the work of:

- [ARMSX2](https://github.com/ARMSX2/ARMSX2)
- [PCSX2](https://github.com/PCSX2/pcsx2)
- [PCSX2_ARM64](https://github.com/pontos2024/PCSX2_ARM64)
- [xlenore/ps2-covers](https://github.com/xlenore/ps2-covers)
- [NetherSX2-patch](https://github.com/noeldvictor/NetherSX2-patch)
- [sashkinbro/EmuCoreX-Textures](https://github.com/sashkinbro/EmuCoreX-Textures) — the texture pack catalogue ARMSX2 mirrors, which the `HD` badges read

Licensed under GPLv3. See [COPYING.GPLv3](COPYING.GPLv3).
