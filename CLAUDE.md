# CLAUDE.md

## Where The Rules Live

Working conventions for this repository live in a single file: [AGENTS.md](AGENTS.md).

Read `AGENTS.md` before making changes. It covers project shape, build and verify
commands, local tooling, the git and upstream-refresh workflow, fork identity rules,
Android UI and cheat conventions, rendering and upscaling conventions, native-core
caution, and style.

Do not duplicate those rules here — update `AGENTS.md` instead.

## Current Thinking

Active design directions. **None of this is implemented.** It is recorded so a new
session does not re-derive it or contradict decisions already made.

### Texture upscaling — in the emulator, not on the screen

Full notes: [docs/texture-upscaling-research.md](docs/texture-upscaling-research.md).

The core distinction, and the easiest thing to get wrong: this upscales **each
texture as the emulator uploads it**, so the game renders from better source art.
It does **not** upscale the finished frame. Present-time upscaling already exists
here (FSR1, the librashader slang chain, LSFG) and is not what this is.

Decided so far:

- **On-device and real time**, while the game is played. No offline baking step, no
  playthrough needed to prepare anything.
- **Works without HD texture packs.** Packs remain optional and complementary — a
  pack wins where one exists, this covers every other game.
- **Two independent texture classes**: world/3D and UI/font/2D. Separate menu
  entries, each with its own on/off *and* its own algorithm. They need different
  treatment; a neural model that flatters a painted wall will mangle a HUD font.
- **Explicit scale factor**: Off / 2x / 4x, user-chosen, not derived from the
  internal resolution multiplier. Thor's panel is 1080x1920, so 4x is the top of
  the useful range.
- **Per-game setting with a global default.**
- **~20 algorithms**, grouped by family (pixel-art / resample / neural), each with a
  `RECOMMENDED` badge where it applies and a short plain-language comment. The
  comment is what makes a twenty-item list usable on a handheld.
- **No per-game recommendation database.** Style presets instead — 2D Sprite,
  Cel-shaded, Painted, Photographic, Neutral — that set both texture classes at
  once. Nothing to curate, nothing to be silently wrong.
- **VRAM: warn once in the UI, then evict.** Batch-evict to ~80% of budget rather
  than one texture at a time at 100%, and mark evicted hashes "do not retry" so a
  busy scene cannot thrash.
- **Build order**: scaffold first with a cheap scaler, then the pixel-art and
  resample families, neural last as Vulkan compute. Not the other way round.

### On-device MCP server

Full notes: [docs/mcp-server.md](docs/mcp-server.md).

Exists mainly to make the upscaling work measurable — comparing twenty algorithms
by hand across a game library is not realistic.

- Drives **screenshots/framebuffer capture, settings read/write, emulator control,
  and texture dump/replace control**.
- **Localhost only, reached over `adb forward`.** No network exposure, no auth
  surface, and it matches how this fork already deploys.
- **Off by default**, explicit toggle, visible indicator while running.
- **`github` flavor only**, compiled out of `play`, never a hard dependency.
