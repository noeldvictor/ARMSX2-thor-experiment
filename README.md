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
- Bundled real cheat PNACHs from the NetherSX2 patch collection are included under `platforms/android/app/src/main/assets/cheats` and copied only when missing.
- Cheat controls are split by PNACH section so each cheat section can be toggled on its own.
- Widescreen and 60 FPS patch metadata are intentionally not used for cheat badges or cheat switches.
- The Compose pause menu has Thor-friendly shortcuts for renderer changes, fast forward, game state, disc changes, imports, and individual cheat toggles.
- The refreshed upstream Kotlin/Compose frontend and PCSX2-derived native core are the active path.
- Texture upscaling runs per texture as the emulator uploads it, not on the finished frame, with separate settings for world and UI textures.
- Texture upscaling scale and algorithm are changeable from the in-game menu, so filters can actually be compared side by side.
- On-screen touch controls default to off. The Thor has physical sticks and buttons, so the overlay was covering the game to duplicate controls already under your thumbs.

## Exploration Notes

Notes on what I am poking at. Some of it is built, most of it is not. No promises, no dates.

- [Texture upscaling on AYN Thor](docs/texture-upscaling-research.md) — upscaling each texture inside the emulator as you play, rather than upscaling the screen. Ten filters plus a neural path, all on a worker thread.
- [Neural models](docs/neural-models.md) — the `.a2nn` format, why no weights ship, and a tool that proves the path works before you have any.
- [ARM64 optimization review](docs/arm64-optimization-review.md) — the Thor is four different CPU cores, and local debug builds were quietly testing different codegen than every release.
- [Cheat tooling](docs/cheat-tooling.md) — measured: 46% of the games on my card have no bundled cheats. What it would take to close that.
- [Texture pack getter](docs/texture-pack-getter.md) — browse and install HD packs, scoped to games actually in the library.
- [On-device MCP server](docs/mcp-server.md) — a localhost control surface over `adb forward`, so comparing twenty upscalers is a loop instead of an afternoon of menu-poking.

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

Licensed under GPLv3. See [COPYING.GPLv3](COPYING.GPLv3).
