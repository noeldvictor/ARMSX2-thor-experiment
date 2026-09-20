package com.armsx2.config

import androidx.compose.runtime.mutableIntStateOf
import com.armsx2.runtime.MainActivityRuntime
import kr.co.iefriends.pcsx2.NativeApp
import org.json.JSONObject

/**
 * The fork's texture-upscaling settings (EmuCore/GS/TextureUpscale*), kept OUT of [Settings].
 *
 * They used to be eight constructor parameters of Settings. That class's `copy$default` takes
 * every field plus one mask int per 32 fields, and dex encodes a range-invoke's register
 * count in 8 bits: the upstream refresh of 2026-09-20 pushed the total from 247 to 264, D8
 * silently wrapped it to 8, and ART rejected MainActivityRuntime at class load. Upstream's
 * own Settings sits at the limit, so anything the fork adds to that constructor breaks on
 * the next refresh. This store has the same global + per-game shape but its own two prefs
 * keys, and never touches the constructor.
 *
 * World/3D and UI/2D are independent on purpose: a filter that flatters a painted wall will
 * mangle a HUD font. Algorithm values are ordinals of the native GSTextureUpscaleAlgorithm
 * enum, which is append-only. Scale is 2 or 4. See docs/texture-upscaling-research.md.
 *
 * On by default with RAISR-HD (ordinal 24) at 2x: the kernels ship in the APK, cost a few ms
 * per new texture on the worker thread, and this is the "play a PS2 game and it just looks
 * HD" default. A stored preference still wins.
 */
data class TextureUpscaleSettings(
    val worldEnabled: Boolean = true,
    val uiEnabled: Boolean = true,
    val worldAlgorithm: Int = 24,
    val uiAlgorithm: Int = 24,
    val worldScale: Int = 2,
    val uiScale: Int = 2,
    val vramBudgetMb: Int = 512,
    /** Pre-pass that removes low-bit-depth banding before any filter runs. PS2 leans on
     *  PSMCT16 (5:5:5:1), so posterised gradients are the norm and a scaler would otherwise
     *  faithfully enlarge the banding. */
    val deposterize: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("worldEnabled", worldEnabled)
        .put("uiEnabled", uiEnabled)
        .put("worldAlgorithm", worldAlgorithm)
        .put("uiAlgorithm", uiAlgorithm)
        .put("worldScale", worldScale)
        .put("uiScale", uiScale)
        .put("vramBudgetMb", vramBudgetMb)
        .put("deposterize", deposterize)

    /** Only the keys where this differs from [base]: what a per-game override file holds. */
    fun diff(base: TextureUpscaleSettings): JSONObject {
        val mine = toJson()
        val theirs = base.toJson()
        val out = JSONObject()
        for (key in mine.keys()) if (mine.opt(key) != theirs.opt(key)) out.put(key, mine.opt(key))
        return out
    }

    /** Push into emucore. Additive setSetting calls, so it composes with Settings.applyTo(). */
    fun applyTo() {
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleWorldEnabled", "bool", worldEnabled.toString())
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleUiEnabled", "bool", uiEnabled.toString())
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleWorldAlgorithm", "int", worldAlgorithm.toString())
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleUiAlgorithm", "int", uiAlgorithm.toString())
        // Only 2 and 4 are meaningful; the native side clamps too, but sending a junk value
        // here would still round-trip through the UI as if it had been accepted.
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleWorldScale", "int", (if (worldScale >= 4) 4 else 2).toString())
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleUiScale", "int", (if (uiScale >= 4) 4 else 2).toString())
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleVramBudgetMB", "int", vramBudgetMb.coerceIn(64, 2048).toString())
        NativeApp.setSetting("EmuCore/GS", "TextureUpscaleDeposterize", "bool", deposterize.toString())
    }

    companion object {
        fun fromJson(json: JSONObject?, base: TextureUpscaleSettings = TextureUpscaleSettings()): TextureUpscaleSettings {
            if (json == null) return base
            return TextureUpscaleSettings(
                worldEnabled = json.optBoolean("worldEnabled", base.worldEnabled),
                uiEnabled = json.optBoolean("uiEnabled", base.uiEnabled),
                worldAlgorithm = json.optInt("worldAlgorithm", base.worldAlgorithm),
                uiAlgorithm = json.optInt("uiAlgorithm", base.uiAlgorithm),
                worldScale = json.optInt("worldScale", base.worldScale),
                uiScale = json.optInt("uiScale", base.uiScale),
                vramBudgetMb = json.optInt("vramBudgetMb", base.vramBudgetMb),
                deposterize = json.optBoolean("deposterize", base.deposterize),
            )
        }
    }
}

/**
 * Global value plus per-game overrides, mirroring ConfigStore's scope rules in miniature: a
 * Game-scope save pins only the fields that changed in that save, so a per-game value that
 * happens to equal global still sticks.
 */
object TextureUpscaleStore {
    private const val KEY_GLOBAL = "config.texupscale.global"
    private const val KEY_GAME_PREFIX = "config.texupscale.game."
    private const val KEY_MIGRATED = "config.texupscale.migrated"

    /** Bumped on every save so Compose readers re-resolve. */
    val revision = mutableIntStateOf(0)

    private val prefs get() = MainActivityRuntime.prefs

    fun global(): TextureUpscaleSettings =
        TextureUpscaleSettings.fromJson(readJson(KEY_GLOBAL))

    fun overrides(serial: String?): JSONObject? =
        serial?.takeIf { it.isNotBlank() }?.let { readJson(KEY_GAME_PREFIX + it.uppercase()) }

    /** Global with the game's pinned fields on top. */
    fun resolve(serial: String?): TextureUpscaleSettings =
        TextureUpscaleSettings.fromJson(overrides(serial), global())

    fun forScope(scope: SettingsScope, serial: String?): TextureUpscaleSettings =
        if (scope == SettingsScope.Game && !serial.isNullOrBlank()) resolve(serial) else global()

    fun save(scope: SettingsScope, serial: String?, updated: TextureUpscaleSettings, previous: TextureUpscaleSettings) {
        if (scope == SettingsScope.Game && !serial.isNullOrBlank()) {
            val key = KEY_GAME_PREFIX + serial.uppercase()
            val pinned = readJson(key) ?: JSONObject()
            val changed = updated.diff(previous)
            for (k in changed.keys()) pinned.put(k, changed.opt(k))
            writeJson(key, pinned)
        } else {
            writeJson(KEY_GLOBAL, updated.toJson())
        }
        revision.intValue++
    }

    fun clearOverrides(serial: String) {
        runCatching { prefs.edit().remove(KEY_GAME_PREFIX + serial.uppercase()).apply() }
        revision.intValue++
    }

    fun resetGlobal() {
        runCatching { prefs.edit().remove(KEY_GLOBAL).apply() }
        revision.intValue++
    }

    /** Save and, when a VM is running, apply live: the keys are EmuCore/GS, so the generic
     *  live GS reconfigure picks them up; the upscaler reads GSConfig per texture. */
    fun saveAndApply(scope: SettingsScope, serial: String?, updated: TextureUpscaleSettings, previous: TextureUpscaleSettings) {
        save(scope, serial, updated, previous)
        if (MainActivityRuntime.nativeReady.value && updated != previous) {
            runCatching {
                updated.applyTo()
                NativeApp.applyGSSettingsLive()
            }
        }
    }

    /**
     * One-shot: the fields used to live inside the global Settings JSON (config.global) and
     * inside each per-game override file. Lift whatever an existing install had, so a user
     * who had picked a filter keeps it after the update.
     */
    fun migrateFromSettingsJson() {
        if (runCatching { prefs.getBoolean(KEY_MIGRATED, false) }.getOrDefault(false)) return
        runCatching {
            val globalJson = prefs.getString("config.global", null)?.let { JSONObject(it) }
            if (globalJson != null && globalJson.has("textureUpscaleWorldEnabled") && readJson(KEY_GLOBAL) == null) {
                val lifted = TextureUpscaleSettings(
                    worldEnabled = globalJson.optBoolean("textureUpscaleWorldEnabled", true),
                    uiEnabled = globalJson.optBoolean("textureUpscaleUiEnabled", true),
                    worldAlgorithm = globalJson.optInt("textureUpscaleWorldAlgorithm", 24),
                    uiAlgorithm = globalJson.optInt("textureUpscaleUiAlgorithm", 24),
                    worldScale = globalJson.optInt("textureUpscaleWorldScale", 2),
                    uiScale = globalJson.optInt("textureUpscaleUiScale", 2),
                    vramBudgetMb = globalJson.optInt("textureUpscaleVramBudgetMb", 512),
                    deposterize = globalJson.optBoolean("textureUpscaleDeposterize", false),
                )
                writeJson(KEY_GLOBAL, lifted.toJson())
            }
        }
        runCatching { prefs.edit().putBoolean(KEY_MIGRATED, true).apply() }
    }

    private fun readJson(key: String): JSONObject? =
        runCatching { prefs.getString(key, null)?.let { JSONObject(it) } }.getOrNull()

    private fun writeJson(key: String, json: JSONObject) {
        runCatching { prefs.edit().putString(key, json.toString()).apply() }
    }
}
