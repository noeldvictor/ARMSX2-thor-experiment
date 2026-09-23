# On-Device MCP Server

Design direction, now implemented in `platforms/android/app/src/github/java/com/armsx2/devtools/`. The tool list and usage live in AGENTS.md (Current Thinking); this file keeps the reasoning.

## Why

Evaluating twenty upscaling algorithms across a library of games by hand is not
realistic: boot game, reach a scene, open the pause menu, change algorithm,
screenshot, compare, repeat. That is the actual bottleneck for the work in
[texture-upscaling-research.md](texture-upscaling-research.md).

An MCP server inside the app turns that loop into something an agent can drive —
and more generally makes future dev automation possible without hand-poking a
handheld.

## Scope

Four capability groups:

- **Screenshots and framebuffer capture.** Let an agent see what is rendered. This
  is the one that makes automated A/B comparison possible; without it none of the
  rest is worth much.
- **Settings read/write.** Renderer, scale factor, per-texture-class algorithm,
  per-game config. Turns "try twenty algorithms on this game" into a loop.
- **Emulator control.** Boot a game, save/load state, pause, fast-forward, send
  input. Needed to reach a specific scene reproducibly before capturing.
- **Texture dump/replace control.** Start and stop dumping, query hash-cache stats,
  inspect which hashes are being upscaled, which were evicted, which were declined
  by the stability heuristic.

Save states are the key primitive for reproducibility: load a known state, apply a
setting, capture, repeat. Same frame, same scene, only the variable under test
changes.

## Access Model

**Localhost only, reached over `adb forward`.**

The server binds `127.0.0.1`. The dev machine reaches it by forwarding a port over
the USB debugging connection already used to deploy:

```powershell
adb forward tcp:PORT tcp:PORT
```

Nothing is exposed to the network, there is no auth surface to get wrong, and it
matches how this fork already deploys. LAN binding can be added later behind an
explicit toggle if wireless iteration becomes annoying — but not first.

## Requirements

- **Off by default**, with an explicit toggle in settings. A remote-control
  interface on a handheld should never be running because someone forgot.
- **Show it is running.** A visible indicator when the server is live. Silent
  remote control is the wrong default on a device that is also someone's console.
- **`github` flavor only**, compiled out of `play` entirely. This matches how the
  fork already treats storage access and LSFG.
- **Never a hard dependency.** The emulator must build, boot, and run identically
  with the server compiled out.

## Tools (implemented)

HTTP on `127.0.0.1:27183` (MCP Streamable HTTP at `POST /mcp`, or `POST /tool/<name>` with the
arguments as the JSON body; `adb forward tcp:27183 tcp:27183`). Code:
`platforms/android/app/src/github/java/com/armsx2/devtools/`.

| Tool | What it does |
| --- | --- |
| `status`, `library`, `boot`, `close`, `pause`, `resume` | Emulator state and game control |
| `save_state`, `load_state` | Slots 1-10; the checkpoint primitive for any A/B test |
| `screenshot` | Next rendered frame to a PNG on the device; returns once the PNG is complete (IEND) |
| `settings_get`, `settings_set` | Read / patch settings (global, per game, or live); `textureUpscale` object for the texture upscaler |
| `hotkey` | `fast_forward`, `reload_textures`, `texture_dump`, `quick_save`, `quick_load` |
| `texture_stats` | Texture upscaler counters, plus `discAtlasLoaded/Images/Matches/Misses/PaletteFreeMatches/TrueColourMatches` for disc packs |
| `texture_dump` | Texture dumping on/off (to `textures/<serial>/dumps`) |
| `gs_dump` | Capture a GS dump of the next `frames` frames to `snaps/` for `pcsx2-gsrunner` |
| `hd_test` | HD pack A/B: `pack` (load replacements) and `filters` (texture upscaler + bilinear; false = off and nearest), applied live, then textures reload |
| `log`, `logcat` | Tail `emulog.txt` / the process logcat, with a substring filter |

A typical HD-pack check: `load_state` -> `screenshot` with `hd_test {"pack":true}`, then again
with `{"pack":false}` after reloading the same state; `texture_stats` for the match counts.

## Open Questions

- (Decided) Transport: HTTP over the forwarded port, MCP Streamable HTTP plus plain `/tool/<name>`.
- (Decided) Capture writes a PNG on the device and returns its path; `GET /screenshot` streams it.
- How much of `GSConfig` to expose. Everything is tempting and probably wrong;
  start with what the upscaling work actually needs.
