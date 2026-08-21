# Texture Pack Getter

A browse-and-install flow for HD texture packs, scoped to games you actually own.

Design direction. Not implemented.

## The requirement that shapes it

**Only offer packs for games in the library.** This is not a cosmetic filter — it is
the whole reason the feature is worth building rather than sending people to a
forum thread. A global list of every pack that exists is a catalogue; a list of the
eleven packs that apply to your shelf is a tool.

It also sidesteps the thing that makes pack hunting miserable: pack names, folder
names and game titles all disagree, and the user is left matching them by eye.

## Matching library to packs

The emulator already knows what it needs. When a game is scanned it has a **serial**
and a **CRC**, and texture replacement folders are named by serial
(`textures/SLUS-XXXXX`) — so the join is exact, not fuzzy, provided the catalogue is
keyed by serial.

That is the critical design constraint: **the pack catalogue must be keyed by serial
and CRC**, not by title. Title matching is what `tools/cheat_coverage.py` has to do
for filenames, and it leaves 16% unresolved on a real library. A catalogue defined
up front can simply carry the right key and skip the problem entirely.

For games not yet scanned, `assets/cheats/index.tsv` is precedent for the same
pattern: a bundled table mapping CRC to serial and title so the UI can show
something useful before a game is ever booted.

## Catalogue shape

A small manifest, fetched or bundled:

```
serial, crc, name, author, scale, size_bytes, url, coverage_note
```

`coverage_note` matters more than it looks. Packs are almost never complete — they
cover whatever the author's playthrough touched. Saying so up front is the
difference between "this pack is broken" and "this pack covers the first three
areas".

## Install target

`<DataRoot>/textures/<SERIAL>/`, which is the folder PCSX2's replacement loader
already scans — visible on the test device at `/sdcard/armsxdata/textures`. Nothing
new to invent, and a pack installed this way works in stock PCSX2 too.

Existing constraints that apply:

- Texture packs load **when the game boots**, so an install mid-session needs the
  same restart hint the texture pack settings already show
  (`renderer.texturePacks.restartHint`).
- A pack folder whose name does not exactly match the serial silently does not load
  — there is already a `serialMismatch` warning string for exactly this trap.

## Relationship to the other two texture features

Three things now touch the same texture path, and they are complementary:

| | Source | Coverage | Cost |
| --- | --- | --- | --- |
| Texture pack | Hand-made or offline-upscaled | Only what the author covered | Download size |
| Texture upscaling | Generated live on device | Everything | Per-texture, once |
| Static extraction | Parsed from the ISO | Format-dependent | Offline |

The intended resolution order at runtime is pack, then upscaler, then native — a
pack wins where it exists, the upscaler covers everything else. That ordering is
what makes partial pack coverage acceptable rather than annoying, and it is worth
building the getter *after* the upscaler for exactly that reason.

See [texture-upscaling-research.md](texture-upscaling-research.md) for the runtime
side and the static-extraction research.

## Open questions

- Where the catalogue lives. Bundled in the APK is simplest and works offline, but
  goes stale and needs an app update to add a pack. Fetched is the opposite.
- Whether to host packs at all, or only link them. Hosting extracted game textures
  carries obvious copyright exposure that linking does not.
- Resume and integrity for large downloads — packs run to hundreds of MB, and a
  half-extracted pack folder fails in the confusing silent way described above.
