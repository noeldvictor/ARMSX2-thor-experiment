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

## Open Questions

- Transport: stdio is the MCP norm but awkward for an Android service; an HTTP or
  WebSocket transport over the forwarded port is more natural. Not yet decided.
- Whether capture returns a framebuffer blob or writes a PNG to a path the agent
  reads back. The second is simpler and avoids large payloads over the transport.
- How much of `GSConfig` to expose. Everything is tempting and probably wrong;
  start with what the upscaling work actually needs.
