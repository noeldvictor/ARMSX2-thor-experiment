# Games with disc HD packs

Every game this fork can build a disc HD texture pack for, and every game looked at. Each name
links to that game's README: before/after screenshots, the one command, measured build times,
what the pack covers and what it doesn't, and notes on how the game stores its textures.

How to build a pack from a recipe, and how to add a game: [README.md](README.md). How disc packs
work: [docs/hd-texture-packs.md](../docs/hd-texture-packs.md). Disc packs only work in this fork.

Build times are for the upscale PC used so far: Core i7-11700, 32 GB RAM, RTX 3060 12 GB.

## Recipes

| | Game | Status | Pack | Build time | Coverage |
| --- | --- | --- | --- | --- | --- |
| [<img src="SCUS-97129-okage/media/menu-fonts.jpg" width="220" alt="Okage status menu, original vs HD pack">](SCUS-97129-okage/README.md) | [Okage: Shadow King](SCUS-97129-okage/README.md)<br>SCUS-97129, NTSC-U | **Completed** | 295 MB zip<br>310 MB installed | ~17 min | All 2,800 disc textures: world, characters, menus, portraits, fonts, true-colour night art. Not yet seen on screen: the IQ24 font, one 32-bit texture. |
| [<img src="SLPS-25842-tales-of-destiny-dc/media/ship-dialogue.jpg" width="220" alt="Tales of Destiny ship's deck, original vs HD pack">](SLPS-25842-tales-of-destiny-dc/README.md) | [Tales of Destiny: Director's Cut](SLPS-25842-tales-of-destiny-dc/README.md)<br>SLPS-25842, English patch v1.6 | **Completed** | 7.3 GB zip<br>8 GB installed | ~5 h 15 min (not a clean run) | Maps, map atlases, sprites, font, title screen: 4x pack runs on the Thor (ship 47/47, title 16/16, 59.9 fps at 3x). Towns, battles and menus not tested yet. |
| [<img src="SLUS-21275-river-king/media/house.jpg" width="220" alt="River King house, original vs HD pack">](SLUS-21275-river-king/README.md) | [River King: A Wonderful Journey](SLUS-21275-river-king/README.md)<br>SLUS-21275, NTSC-U | **Completed** | 1.3 GB zip<br>1.4 GB installed | ~49 min | Every texture of the five opening scenes (title, menus, name entry, dialogue, the house), text included; 59.5 fps at 3x in the app. Fishing, river and town not tested yet. |
| [<img src="SLUS-21201-tales-of-legendia/media/portraits.jpg" width="220" alt="Tales of Legendia portrait conversation, original vs HD pack">](SLUS-21201-tales-of-legendia/README.md) | [Tales of Legendia](SLUS-21201-tales-of-legendia/README.md)<br>SLUS-21201, NTSC-U (ReUndub v1.4) | **Completed** | 5.9 GB zip<br>6.4 GB installed | ~2 h 55 min (not a clean run) | Maps, models, skit portraits, the dialogue font and the title: four scenes on the Thor, everything matched but render-target reads and two small UI pieces; 59.9 fps at 3x. Towns and battles not tested yet. |
| [<img src="SLPS-25450-tales-of-rebirth/media/field.jpg" width="220" alt="Tales of Rebirth field, original vs HD pack">](SLPS-25450-tales-of-rebirth/README.md) | [Tales of Rebirth](SLPS-25450-tales-of-rebirth/README.md)<br>SLPS-25450, English patch v1.0 | **Completed** | 11.1 GB zip<br>12 GB installed | ~10 h | Map sheets (split per palette), sprites, UI and the font: four scenes on the Thor, every composite loads; 59.9 fps at 3x. Translucent ice gets harder edges; runtime palettes stay native. Too big to keep on the Thor. |

**Status**: *Completed* - the pack is built, runs on the Thor at 3x, and the README has its build
times and checksums; the coverage column says which scenes were checked. *In progress* - the
recipe exists but there is no working pack yet.

## Wishlist

Games wanted next, not started. Nothing here yet.

| Game | Serial | Why | Known so far |
| --- | --- | --- | --- |

## Checked, not a candidate

Games whose textures turned out not to be buildable from the disc (textures made at runtime,
formats nobody could decode, 16-bit textures the tools do not handle yet), with the reason, so
nobody repeats the work. Nothing here yet.

| Game | Serial | Why not | Checked |
| --- | --- | --- | --- |

## Adding a row

A game gets a row here when its recipe folder exists (`hd-packs/<SERIAL>-<name>/`, README from
[TEMPLATE.md](TEMPLATE.md)). Put a before/after image from the game's `media/` in the first
column, the zip and installed sizes from `make_pack.py`'s output, and the total of its step
times. A game checked and dropped goes in the last table instead, with what was found.
