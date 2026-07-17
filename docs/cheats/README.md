# Bundled Cheats

The PNACH files bundled in `platforms/android/app/src/main/assets/cheats` were imported from
`https://github.com/noeldvictor/NetherSX2-patch`, `cheats/exact`.
The adjacent `index.tsv` maps CRC filenames to serials and titles for library cover badges.

That source repo is released under the Unlicense. ARMSX2 keeps only the exact
CRC cheat pack by default. Candidate PNACH files are intentionally excluded
because the source marks them as requiring in-game testing.

The source cheat pack stores gameplay cheat lines as commented `// patch=...`
entries. They stay default-off in the APK, and the installed-PNACH editor
enables or disables each named section independently. Existing files in the
user's data-root `cheats` folder are never overwritten by bundled copies.
