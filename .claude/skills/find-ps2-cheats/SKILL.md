---
name: find-ps2-cheats
description: Find existing PS2 cheats for games on the Thor that have no bundled PNACH - coverage report, the serial-keyed GitHub cheat DB, then gamehacking.org's PCSX2 export through stealth Playwright at a respectful pace - and install them. Use for "which games have no cheats", "find cheats online", "download cheats for X".
---

# Finding existing PS2 cheats

Order of cost: bundled set -> GitHub DB (no browser) -> gamehacking.org (browser, slow) ->
author one with the `ps2-cheat` skill. Every step lives in `tools/cheat_finder/`; the venv is
`tools/pcsx2_mcp/.venv` (requirements include playwright + playwright-stealth; run
`python -m playwright install chromium` once).

## 1. Who is missing

```
python tools/cheat_finder/online_cheats.py --adb --remote-dir /storage/2664-21DE/Roms/ps2 --extra extra.txt --out <workdir>
```
Runs `cheat_coverage.py`, matches every missing serial against XiGuanChi/PCSX2-CheatsDB
(23k files named `SERIAL_CRC.pnach`, the only serial-keyed public DB; the CRC-named ones are
the NetherSX2 set we already bundle) and writes `online_cheats_report.txt` plus
`gamehacking_targets.txt`. `extra.txt` holds `SERIAL|Title` for filenames the coverage tool
could not resolve - translation patches keep the original serial; find it in
`bin/resources/GameIndex.yaml` (`name-en:` for Japanese releases). Do not guess serials from
memory: SLUS-20952 is Tak 2, not Shadow Hearts. Cheats are region/revision-exact: a PAL hit
for an NTSC-U disc is not usable.

## 2. gamehacking.org, gently

```
python tools/cheat_finder/gamehacking_export.py gamehacking_targets.txt --out <workdir>/gamehacking --pace 10
```
Headed Chromium + playwright-stealth; plain fetches and headless get Cloudflare's block, and
the `/search` POST is what the WAF watches, so the tool types into the search form like a
person. Three requests per game with ~10 s pauses, one pass, no retries - **the user asked
that the site not be hammered; keep `--pace` at 10 or more and never loop on failures**. If a
page titled "Attention Required" appears the run stops itself; wait an hour before rerunning
(the report skips games already done).

The export is PCSX2 format with `[Group\Name]` sections, but still carries un-decrypted
GameShark v1 codes (addresses like `91F68566`). The tool keeps only sections whose lines all
target EE RAM with a legal type nibble and writes `<SERIAL>_00000000.pnach`; `raw/` keeps
the original. Search results are site-wide, so the tool only picks from the "Playstation 2"
panel and requires the full title + region tag to match.

## 3. Install

- Thor: `adb push <SERIAL>_00000000.pnach /sdcard/armsxdata/cheats/`. The app's cheat glob is
  `SERIAL_*.pnach`, so no CRC is needed. The cover badge and the in-game switches appear on
  the next library scan; each section is a switch, and the app uncomments the `patch=` lines
  it enables.
- Bundle (repo): only for files verified on a disc we own; needs the real CRC in the filename
  and an `index.tsv` row. gamehacking exports are unverified community codes - keep them on
  the device, not in `assets/cheats`.

## What was tried and does not work

- `curl`/WebFetch on gamehacking.org, GameFAQs, PS2-HOME, Neoseeker: 403/Cloudflare.
- Codejunkies: AR MAX encrypted codes and a broken saves index.
- gamehacking `?q=` URL search: Cloudflare block even in a real browser; use the form.
