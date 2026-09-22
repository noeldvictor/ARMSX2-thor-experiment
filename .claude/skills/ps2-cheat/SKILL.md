---
name: ps2-cheat
description: Author a PS2 gameplay cheat (PNACH) for a game on the Thor when none exists online - find the address or instruction on desktop PCSX2 with the pcsx2 MCP server (PINE memory, snapshot-diff search, disassembly), verify it there, then ship it to the Thor. Use for "make a cheat", "no encounters", "infinite HP", "find the address" requests.
---

# PS2 cheat authoring

The Thor build has no debugger; desktop PCSX2 on this PC does, and the `pcsx2` MCP
server (`tools/pcsx2_mcp/`, registered in `.mcp.json`) drives it. If its tools are not
listed, run `/mcp` to connect, or use the same tools from a shell:
`tools\pcsx2_mcp\.venv\Scripts\python tools\pcsx2_mcp\server.py cli <tool> ...`.

## 0. Check before authoring

1. `tools/cheat_coverage.py` and the SD-card collection first - a code that exists is
   cheaper than one you find. Community codes are often encrypted (AR MAX / CodeBreaker);
   an encrypted list with no matching entry is still a "no".
2. Bundled cheats are keyed by **CRC** (PCSX2 log: `Game CRC = XXXXXXXX`; `status` shows it).

## 1. Set up (once per game)

```
python tools\pcsx2_mcp\setup.py --dest F:\Projects\pcsx2-desktop --bios-from-thor --game "<title>" --save <file.max>
```
Saves come from GameFAQs (Cloudflare blocks fetches: use Playwright in
`F:\Projects\pcsx2-desktop\.venv`, or ask the user to download). One save directory per
memory card - a second save with the same directory name goes on card 2.

## 2. Reach the behaviour

`launch("<title>")`, then `press`/`hold` with PS2 button names (`cross`, `circle`,
`start`, `lup`...), `screenshot()` to look. Save a checkpoint with `save_state(1)` right
where the behaviour can be triggered repeatedly (a random-encounter area, a shop, a
battle). Slot 10 is reserved for snapshots.

## 3. Find the address

Data cheat (a counter, HP, money):
- `search_start(width=16)` unknown value, then trigger a change and `search_filter("decreased")`
  / `"increased"` / `"unchanged"`; known values: `search_start(width=32, value=1234)` then
  `search_filter("eq", 1200)` after spending. Reload the checkpoint between rounds so the
  set stays comparable. Stop under ~20 candidates and confirm each with `mem_write` + look.
- Random encounters are usually a step counter or a per-step RNG check. Walk a fixed
  distance between snapshots; a value that decreases with steps and resets after a battle
  is the counter.

Code cheat (skip a check - the Wizardry "No Enemy Encounters" is `201B9468 -> 00000000`):
- `find_refs(addr)` lists lui/lo pairs touching the variable; `disasm(addr, 32)` around a
  hit shows the compare + branch. Prefer NOP-ing a `jal`/`bnez` over freezing data: it is
  one line, no per-frame write, no flicker.
- `mem_write(code_addr, 0)` tests the NOP live (PINE writes invalidate the recompiler).

Visible-enemy games (Okage, Wizardry): there is no step counter. Burst-snapshot while
walking into an enemy (`F:\Projects\pcsx2-desktopurst2.py` is the template), find the
swirl frame in the screenshots, then diff "constant across the field snapshots, changed at
the swirl" **restricted to the ELF data band** (`compare_snapshots(..., region="1F0000-320000")`
style) - the battle overlay load changes hundreds of thousands of words above that. Look
for a 0->1 flag, a freshly written record (positions + an id) and a state field; then list
every `sw x, <state offset>(base)` in the code and read the one that stores the encounter
state. It sits next to the distance compare.

## 4. Verify and ship

1. `pnach_write({"Name": ["patch=1,EE,2XXXXXXX,extended,YYYYYYYY"]}, target="desktop")`
   writes `<pcsx2>/cheats/<CRC>.pnach` **and** the per-game `[Cheats] Enable = Cheats/Name`
   entry (named sections are off by default in PCSX2 2.x). Then `quit()` +
   `launch(game, state_slot=1)` - patches only reload on boot. Confirm in `log(contains=
   "cheat")`: "1 cheat patches are active", and `mem_read(addr)` shows the new word.
2. `target="thor"` pushes an active copy to `/sdcard/armsxdata/cheats/<CRC>.pnach`. On the
   device also: `settings_set {"enableCheats": true}` over the dev server if it is off, and
   `gamesettings/<SERIAL>_<CRC>.ini` with the same `[Cheats] Enable = Cheats/Name` line
   (the in-app cheat manager does both when the user flips the switch). Reboot the game;
   the log must say "Found 1 cheats" then "Enabled patch: Cheats/Name". Driving the game on
   the Thor from adb is unreliable (`input keyevent` does not reach the pad), so on-device
   play testing is the user's step.
3. `target="repo"` writes the bundle copy (lines commented, per AGENTS.md); add the CRC to
   `assets/cheats/index.tsv` (one row, keep the CRC order); commit and push.

Widths: `0`=8-bit, `1`=16-bit, `2`=32-bit prefix on the address. Comparison-branch NOPs
must keep the delay slot intact - NOP or force the branch, not the instruction after it.
Forcing `bc1f off` -> `b off` keeps the offset: `0x45000077` -> `0x10000077`.

## Gotchas met so far

- PCSX2 v2.8 reads `portable.txt`'s *contents* as the data root: keep the file empty.
- GameFAQs is behind Cloudflare: Playwright with headed Chromium in
  `F:\Projects\pcsx2-desktop\.venv` gets through; `/saves/<id>` is itself the download.
- Okage's load prompt defaults to "No"; check every screenshot before assuming a press
  did nothing. Circle is cancel, Cross confirm.
- Each `server.py cli` call is a new process: snapshots and the search do not persist
  between CLI calls. Use the MCP tools (one process) or a script importing `pcsx2ctl`.
- Standing still may attract nothing; walk in a loop while sampling.
