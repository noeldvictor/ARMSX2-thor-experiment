package com.armsx2

import com.armsx2.config.Settings
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Guards a dex limit that the toolchain does not.
 *
 * A data class's `copy$default` is a static method taking the instance, every constructor
 * parameter, one Int mask per 32 parameters and a trailing Object. A dex range-invoke
 * encodes its register count in 8 bits. Past 255 registers D8 wraps the count instead of
 * failing, the build succeeds, and ART rejects every class that calls copy() at load time -
 * which on 2026-09-20 was MainActivityRuntime, so the app crashed on launch.
 *
 * Upstream's Settings sits at that limit, so the fork keeps its own texture-upscaling and
 * PINE settings in separate stores (TextureUpscaleSettings, PineSettings) and this test
 * fails the build before the next refresh can ship a crashing APK.
 */
class SettingsSizeTest {
    @Test
    fun settingsCopyDefaultFitsInDexRangeInvoke() {
        // The primary constructor, not the synthetic one Kotlin adds for default arguments
        // (that one already carries the mask ints and a DefaultConstructorMarker).
        val ctor = Settings::class.java.constructors
            .filter { c -> c.parameterTypes.none { it.simpleName == "DefaultConstructorMarker" } }
            .maxByOrNull { it.parameterCount }!!
        val registers = ctor.parameterTypes.sumOf { if (it == java.lang.Long.TYPE || it == java.lang.Double.TYPE) 2L else 1L }
        val params = ctor.parameterCount
        val masks = (params + 31) / 32
        val copyDefault = 1 + registers + masks + 1
        assertTrue(
            "Settings has $params constructor parameters; copy\$default would need $copyDefault registers " +
                "(limit 255). Move fields out of the constructor - see TextureUpscaleSettings / PineSettings.",
            copyDefault <= 255,
        )
    }
}
