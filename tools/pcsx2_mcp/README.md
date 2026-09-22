# pcsx2 MCP server

Drives desktop PCSX2 on the PC for cheat authoring. Registered in the repo's `.mcp.json`
(`run.cmd` creates `.venv` on first use). The workflow is the `ps2-cheat` skill in
`.claude/skills/ps2-cheat/SKILL.md`; the working notes are the "Cheat tooling" item in
`AGENTS.md`.

- `setup.py` - portable PCSX2 install with a PINE-ready ini, BIOS/game from the Thor, memcard saves.
- `pine.py` - PINE client (TCP, works against desktop PCSX2 and ARMSX2 over `adb forward`).
- `pcsx2ctl.py` - window/keys/screenshots, save-state RAM dumps, search, disassembly, PNACH.
- `server.py` - the MCP tools; `server.py cli <tool> ...` runs any of them from a shell.

`config.local.json` (gitignored) overrides `pcsx2_dir` / `games_dir`.
