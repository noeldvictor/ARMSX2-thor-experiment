package com.armsx2.update

import androidx.compose.runtime.Composable

/**
 * No-op stub of upstream's in-app updater, for the github flavor too.
 *
 * Upstream's implementation checks https://api.github.com/repos/ARMSX2/ARMSX2/releases and
 * downloads + installs the latest official APK. In this fork that is a foot-gun: it would replace
 * the Thor build with official ARMSX2. The fork publishes no releases, so there is nothing to
 * point it at instead; IN_APP_UPDATER is false in build.gradle.kts and REQUEST_INSTALL_PACKAGES
 * plus the update FileProvider are gone from src/github/AndroidManifest.xml. On an upstream
 * refresh keep this stub (see AGENTS.md, "Upstream Refresh").
 */
@Composable
fun UpdaterEntry() {
    // intentionally empty
}

/** No-op stub of the boot-time auto-update check. */
@Composable
fun AutoUpdateGate() {
    // intentionally empty
}
