# AGENTS.md

## Project Shape
- ARMSX2 is a cross-platform PCSX2-derived monorepo. Android lives under `platforms/android`.
- The Android frontend is Kotlin/Compose under `platforms/android/app/src/main/java/com/armsx2`.
- Java/JNI compatibility code is under `platforms/android/app/src/main/java/kr/co/iefriends/pcsx2`; the Android native bridge is `platforms/android/app/src/main/cpp/native-lib.cpp`.
- Shared emulator code lives at the repository root, especially `pcsx2`, `common`, and `3rdparty`.

## Build And Verify
- Run the Android Gradle wrapper from `platforms/android`, not the repository root.
- Use `.\gradlew.bat :app:compileGithubDebugKotlin` for focused Compose/Kotlin checks.
- Use `.\gradlew.bat :app:assembleGithubDebug` for a sideloadable debug APK.
- JDK 17 and the Android SDK/NDK are required. A known local JDK is `C:\Program Files\Microsoft\jdk-17.0.19.10-hotspot`.
- On a fresh checkout, run `python app\src\main\cpp\3rdparty\shaderc\utils\git-sync-deps` from `platforms/android` before the first native build.
- The native build also needs Cargo with the `aarch64-linux-android` Rust target. Keep the Windows `.cmd` NDK linker handling in librashader's CMake file.
- The `github` flavor includes the storage access used by the personal sideload build; the `play` flavor intentionally does not.
- Native changes trigger a much heavier CMake/NDK build. Always run `git diff --check`, even when a full native build is unavailable.
- Do not add signing keys, local SDK paths, generated `.cxx` content, APKs, or other ignored build output.

## Local Tooling
- Prefer `rg` for search.
- PowerShell may not accept Unix-style `&&`; run commands separately.
- Network access and SSH Git remotes are expected. `origin` is `git@github.com:noeldvictor/ARMSX2-thor-experiment.git` and canonical `upstream` is `https://github.com/ARMSX2/ARMSX2.git`.
- Use `adb devices` before deployment and install the newest `github/debug` APK with `adb install -r`.

## Git Workflow
- Use only the default/mainline branch. In this clone, user references to `main` mean `master`.
- Do not create or switch to feature branches unless explicitly requested.
- Commit and push completed work unless the user explicitly asks not to.
- Keep commit messages short and specific.
- Never run ultra review (`/code-review ultra` or the `/ultrareview` alias). Verify with the build steps above instead.

## Upstream Refresh
- "Get the latest ARMSX2 updates" means `git fetch upstream`, then merge `upstream/master` into `master` as a real merge commit. Do not rebase or squash; the history is a series of merge commits titled `Refresh Thor fork from upstream ARMSX2`.
- Three files conflict on nearly every refresh. Resolve them this way:
  - `AGENTS.md` - keep this fork's file. Upstream ships its own PCSX2 desktop-oriented `AGENTS.md`; discard that side.
  - `README.md` - keep the fork identity text. Upstream's "Current status" feature checklist does not belong here.
  - `platforms/android/app/src/main/java/com/armsx2/ui/patches/PatchManagerViewModel.kt` - take upstream's `refresh()` body (serial/CRC scoping, no `syncAllEnableLists`) and keep the fork's leading `CheatPresenceIndex.invalidate()` call.
- After resolving, run `.\gradlew.bat :app:compileGithubDebugKotlin` from `platforms/android` before pushing.

## Fork Identity
- Treat this as the personal AYN Thor experiment fork, not official ARMSX2.
- Keep the README explicit: vibe-coded with AI, personal use, unsupported, no stability guarantee, no issue/request queue, and fork-it-yourself friendly.
- Do not add an APK download/release section to `README.md` unless the user reverses that preference.
- Keep app-facing repository links pointed at `noeldvictor/ARMSX2-thor-experiment`; retain upstream and PCSX2 links only where attribution is clear.

## Android UI And Cheats
- Follow the existing Compose components and controller-focus patterns.
- Cover art defaults to xlenore's PS2/PS1 cover repositories. Preserve that hardcoded default.
- Cover `CHEATS` badges must come only from real `.pnach` files in `<DataRoot>/cheats`; never infer them from widescreen, 60 FPS, compatibility, or patch folders.
- `CheatPresenceIndex` owns cover-badge indexing. Invalidate it whenever PNACH files are imported, installed, or deleted.
- `platforms/android/app/src/main/assets/cheats/index.tsv` maps bundled CRC filenames to serials and titles so cover badges work before a game is booted. Keep it synchronized with the bundled PNACH set.
- Individual switches are named PNACH sections handled by `PatchManagerScreen`, `PatchManagerViewModel`, `PatchRepo`, and `NativeApp.setEnabledPatches`.
- Bundled exact cheats live at `platforms/android/app/src/main/assets/cheats`. Keep their `patch=` lines commented so every cheat starts off and is enabled deliberately with its own switch.
- Bundled assets copy into `<DataRoot>/cheats` only when missing. Never overwrite a user's edited PNACH.
- This fork's cheat work is about gameplay cheats. Do not use widescreen or 60 FPS patch metadata as cheat state.

## Native Core
- Treat shared `pcsx2`, `common`, `3rdparty`, and `platforms/android/app/src/main/cpp` changes as high blast radius.
- Prefer existing bridges in `NativeApp.java` and `native-lib.cpp` before adding JNI surface area.
- Preserve upstream behavior when the refreshed Compose patch manager already covers a fork feature.

## Style
- Match nearby Kotlin/Compose or C++ style.
- Use Android resources for app-visible text when practical.
- Keep changes scoped to the requested behavior; avoid unrelated monorepo refactors.
