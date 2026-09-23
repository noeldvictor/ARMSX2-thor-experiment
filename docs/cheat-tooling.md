# Cheat Tooling

Where the bundled cheat set actually stands, and what would close the gap.

Measured 2026-08-21 against the games on the test Thor.

## The gap is real and measurable

`tools/cheat_coverage.py` cross-references a game library against the bundled cheat
set. Run against the SD card on the test device:

```
Bundled cheat set: 590 files covering 534 serials
Games scanned:     119
  resolved to a serial: 100
  unresolved:           19

Of the 100 resolved:
  HAVE cheats:    54  (54%)
  MISSING cheats: 46  (46%)
```

So the impression that "a lot of games are missing cheats" is correct: on a real
library it is close to half.

### How it answers the question at all

Bundled cheats are keyed by **CRC**, and a CRC can only be known by reading the
disc — so a filename cannot be checked directly. The tool goes via the serial:

- `bin/resources/GameIndex.yaml` (12,830 serials) maps serial to title
- `assets/cheats/index.tsv` maps CRC to serial
- A filename that resolves to a serial can therefore be answered

Resolution is by explicit serial in the filename first, then by normalized title
match — stripping `(USA)`, `[NTSC-U]`, `[T-En by ...]` and similar scene tags.

**Unresolved is reported separately from missing, deliberately.** 19 of 119 files
resolved to no serial — mostly translation patches and undubs whose filenames do not
match any database title. Their cheat status is *unknown*, not *absent*, and folding
those together would overstate the gap by a fifth.

## Why coverage is partial

The bundled set comes from the NetherSX2 patch collection, which is itself a
snapshot of community work. It was never a complete index of the PS2 library — 534
serials against a library of thousands. Nothing is broken; the set is just partial.

Notable in the missing list: quite a lot of JP-only and PAL-only releases
(`SCAJ-`, `SLPM-`, `SLES-`), which is what you would expect from a collection built
mostly around NTSC-U.

## Two separate problems

The request covers two things that need different solutions.

### 1. Import cheats that already exist

The tool already produces the exact target list. The work is finding PNACH sources
for those 46 serials and importing them in the existing bundled format.

Constraints that already apply to the bundled set and should keep applying:

- Files are named by **CRC**, not serial, because that is what PCSX2 matches on.
  A serial is not enough to author a bundled file — the CRC has to come from the
  actual disc revision.
- `index.tsv` must stay in step with the PNACH set, since cover badges read it
  before a game is ever booted (AGENTS.md).
- Every `patch=` line stays commented, so each cheat starts off and is enabled
  deliberately through its own switch.

That CRC requirement is the real friction: importing a cheat for a game you do not
have means trusting someone else's CRC. Worth marking imported-unverified entries
as such rather than silently mixing them with ones confirmed against a local disc.

On provenance — community PNACH collections rarely carry an explicit licence. The
existing bundle credits NetherSX2-patch in the README, and anything imported should
be credited the same way.

### 2. Author cheats that do not exist

This is the harder half and it is not a scraping problem. A cheat is a memory
patch, so making one means finding the address — the classic loop being: search for
a value, change it in game, search again for what changed, repeat until one address
remains.

Since 2026-09-22 this exists as the `ps2-cheat` skill plus the `pcsx2` MCP server in
`tools/pcsx2_mcp/` (see the "Cheat tooling" item in `AGENTS.md` for the working notes).
It runs against desktop PCSX2 on the PC rather than the Thor, because desktop PCSX2
already has PINE, uncompressed save states and the same PNACH loader; the Thor gets the
finished `.pnach`. The first cheat authored this way is Okage: Shadow King's "No Enemy
Encounters" (`E0426FC6.pnach`). The original sketch of what the tool needed follows, and
it turned out to be right except that breakpoints were never needed:

- **Memory search over the running VM** — seed a search, filter by
  changed/unchanged/greater/less across iterations, and land on candidate addresses.
- **Watch and poke** — show live values for candidates, write a value to confirm the
  address does what you think before committing to it.
- **Emit PNACH** — turn a confirmed address into a correctly formatted `patch=` line
  with the right CRC, dropped into `<DataRoot>/cheats` and picked up by the existing
  patch manager.

The last step is small; the first two are the actual feature. It also wants the
emulator paused or frame-stepped to be usable, which the pause menu already does.

**This overlaps hard with the MCP server** ([mcp-server.md](mcp-server.md)) — which
already plans emulator control, save-state load, and settings access. A memory
search loop driven by an agent over MCP is plausibly a faster route to working
cheats than a hand-built on-device UI, and it does not need a controller-friendly
search interface designed first.

## Suggested order

1. Use the coverage tool's missing list as the import target. Highest value per
   effort by a wide margin — those cheats already exist somewhere.
2. Record CRC provenance while importing, so unverified entries stay identifiable.
3. Only then consider the authoring tool, and look at driving it through MCP before
   building an on-device search UI.

## Desktop PCSX2 lab: keys

`tools/pcsx2_mcp/setup.py` writes this ini for the portable desktop PCSX2 used for authoring
(`F:\Projects\pcsx2-desktop\pcsx2`); the `pcsx2` MCP server presses these by name.

| Key | Action | | Key | PS2 pad |
| --- | --- | --- | --- | --- |
| Tab | Turbo (fast forward) toggle | | Arrows | D-pad |
| F8 | Screenshot to `snaps/` | | W A S D | Left stick |
| F9 | Single-frame GS dump to `snaps/` (replay it on the Thor with `pcsx2-gsrunner`) | | T F G H | Right stick |
| F1 / F3 | Save / load state slot | | K L J I | Cross, Circle, Square, Triangle |
| F2 / Shift+F2 | Next / previous slot | | Enter / Backspace | Start / Select |
| Space | Pause | | Q E / 1 3 / 2 4 | L1 R1 / L2 R2 / L3 R3 |
| Esc | Pause menu | | | |
| Alt+Enter | Fullscreen | | | |
