package com.armsx2.config

import androidx.compose.runtime.mutableIntStateOf
import com.armsx2.runtime.MainActivityRuntime
import kr.co.iefriends.pcsx2.NativeApp
import org.json.JSONObject

/**
 * EmuCore/EnablePINE + EmuCore/PINESlot, kept OUT of [Settings].
 *
 * Two reasons. PINE is one IPC server for the whole process, so "this game runs with PINE
 * on" was never a thing that could be true: ConfigStore.save had to special-case these two
 * fields to force them global, and Settings.merge had to pin them to base. A process-wide
 * store says that directly. And the fork needs Settings' constructor at least one parameter
 * shorter than upstream's: `copy$default` takes every field plus mask ints, dex encodes a
 * range-invoke's register count in 8 bits, and upstream's constructor sits at that limit
 * (see TextureUpscaleSettings and the guard in SettingsSizeTest). These two were the
 * smallest, cleanest fields to lift.
 *
 * The server listens on loopback TCP, reachable from a workstation only after
 * `adb forward`; nothing outside the device can see it. Off by default: it is a debugging
 * tool, and a listening socket a player did not ask for should not exist. The port has no UI
 * row on purpose - the only reason to move it is two emulators at once, which does not happen
 * on a handheld - but it is stored so the toggle can state the real port.
 */
data class PineSettings(
    val enabled: Boolean = false,
    val slot: Int = 28011,
) {
    fun toJson(): JSONObject = JSONObject().put("enabled", enabled).put("slot", slot)

    fun applyTo() {
        NativeApp.setSetting("EmuCore", "EnablePINE", "bool", enabled.toString())
        NativeApp.setSetting("EmuCore", "PINESlot", "int", slot.toString())
    }

    companion object {
        fun fromJson(json: JSONObject?): PineSettings {
            val def = PineSettings()
            if (json == null) return def
            return PineSettings(json.optBoolean("enabled", def.enabled), json.optInt("slot", def.slot))
        }
    }
}

object PineStore {
    private const val KEY = "config.pine"
    private const val KEY_MIGRATED = "config.pine.migrated"

    /** Bumped on save so Compose readers refresh. */
    val revision = mutableIntStateOf(0)

    fun get(): PineSettings =
        PineSettings.fromJson(runCatching { MainActivityRuntime.prefs.getString(KEY, null)?.let { JSONObject(it) } }.getOrNull())

    fun save(updated: PineSettings) {
        runCatching { MainActivityRuntime.prefs.edit().putString(KEY, updated.toJson().toString()).apply() }
        revision.intValue++
        if (MainActivityRuntime.nativeReady.value) runCatching { updated.applyTo() }
    }

    /** One-shot: lift the two keys from the old global Settings JSON on an existing install. */
    fun migrateFromSettingsJson() {
        val prefs = MainActivityRuntime.prefs
        if (runCatching { prefs.getBoolean(KEY_MIGRATED, false) }.getOrDefault(false)) return
        runCatching {
            val globalJson = prefs.getString("config.global", null)?.let { JSONObject(it) }
            if (globalJson != null && globalJson.has("pineEnabled") && prefs.getString(KEY, null) == null) {
                save(PineSettings(globalJson.optBoolean("pineEnabled", false), globalJson.optInt("pineSlot", 28011)))
            }
        }
        runCatching { prefs.edit().putBoolean(KEY_MIGRATED, true).apply() }
    }
}
