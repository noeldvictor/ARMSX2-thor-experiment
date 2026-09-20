package com.armsx2.devtools

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.armsx2.EmuState
import com.armsx2.GameInfo
import com.armsx2.config.ConfigStore
import com.armsx2.config.Settings
import com.armsx2.config.SettingsScope
import com.armsx2.data.library.GameLibraryRepository
import com.armsx2.runtime.MainActivityRuntime
import com.armsx2.ui.InGameOverlay
import kr.co.iefriends.pcsx2.NativeApp
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The tools behind [DevServer]. Each takes a JSON object and returns one; an `error` key
 * means failure. Kept to what the upscaling work needs (docs/mcp-server.md): boot, capture,
 * settings, upscaler stats, and the log. Input injection is deliberately absent - `adb shell
 * input` already does that from the dev machine.
 */
class DevTools(private val context: Context) {

    private data class Tool(val name: String, val description: String, val schema: JSONObject, val run: (JSONObject) -> JSONObject)

    private val tools: List<Tool> = listOf(
        Tool("status", "Emulator state: running game, pause state, native readiness, texture-upscaler counters.",
            schema(), ::status),
        Tool("library", "List the scanned game library. Optional substring `query` on title or serial.",
            schema("query" to "string"), ::library),
        Tool("boot", "Boot a game by `serial`, `title` (substring) or `uri`. Returns the resolved game.",
            schema("serial" to "string", "title" to "string", "uri" to "string"), ::boot),
        Tool("close", "Close the running game and return to the library.", schema(), ::close),
        Tool("pause", "Pause emulation.", schema()) { onMain { MainActivityRuntime.pause() }; status(it) },
        Tool("resume", "Resume emulation.", schema()) { onMain { MainActivityRuntime.resume() }; status(it) },
        Tool("save_state", "Save to state `slot` (1-10).", schema("slot" to "integer")) { a ->
            requireGame()?.let { return@Tool it }
            val slot = a.optInt("slot", 1)
            JSONObject().put("ok", NativeApp.saveStateToSlot(slot)).put("slot", slot)
        },
        Tool("load_state", "Load state `slot` (1-10).", schema("slot" to "integer")) { a ->
            requireGame()?.let { return@Tool it }
            val slot = a.optInt("slot", 1)
            JSONObject().put("ok", NativeApp.loadStateFromSlot(slot)).put("slot", slot)
        },
        Tool("screenshot", "Capture the next rendered frame to a PNG on the device (optional `path`) and return its path and size. Pull it with adb, or GET /screenshot for the bytes.",
            schema("path" to "string"), ::screenshot),
        Tool("settings_get", "Read settings as JSON. `scope`: global (default) or game (with `serial`). While a game runs and no scope is given, returns the live resolved settings.",
            schema("scope" to "string", "serial" to "string"), ::settingsGet),
        Tool("settings_set", "Merge `patch` (JSON of Settings fields; texture upscaling goes under a `textureUpscale` object, e.g. {\"textureUpscale\": {\"worldAlgorithm\": 24}}) into settings and apply live if a game is running. `scope`: global or game (+`serial`). Returns the fields that changed.",
            schema("patch" to "object", "scope" to "string", "serial" to "string"), ::settingsSet),
        Tool("hotkey", "Fire a hotkey: reload_textures, fast_forward, texture_dump, quick_save, quick_load.",
            schema("name" to "string"), ::hotkey),
        Tool("texture_stats", "Texture-upscaler counters and the active configuration.", schema()) { textureStats() },
        Tool("texture_dump", "Texture dumping on/off (`on`: boolean; omit to toggle). Dumps land in textures/<serial>/dumps.",
            schema("on" to "boolean"), ::textureDump),
        Tool("log", "Tail the emulator log (emulog.txt). `lines` (default 200), optional `filter` substring.",
            schema("lines" to "integer", "filter" to "string"), ::log),
        Tool("logcat", "Tail this process's logcat. `lines` (default 200), optional `filter` substring.",
            schema("lines" to "integer", "filter" to "string"), ::logcat),
    )

    fun list(): List<JSONObject> = tools.map {
        JSONObject().put("name", it.name).put("description", it.description).put("inputSchema", it.schema)
    }

    fun call(name: String, args: JSONObject): JSONObject {
        val tool = tools.firstOrNull { it.name == name }
            ?: return JSONObject().put("error", "unknown tool: $name").put("tools", JSONArray(tools.map { it.name }))
        return try {
            tool.run(args)
        } catch (e: Throwable) {
            JSONObject().put("error", e.toString())
        }
    }

    // ---- helpers ----------------------------------------------------------------------------

    private fun schema(vararg props: Pair<String, String>): JSONObject {
        val properties = JSONObject()
        for ((k, t) in props) properties.put(k, JSONObject().put("type", t))
        return JSONObject().put("type", "object").put("properties", properties)
    }

    /** Run on the main thread and wait; the runtime's state is main-thread owned. */
    private fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val latch = CountDownLatch(1)
        var result: Result<T>? = null
        Handler(Looper.getMainLooper()).post {
            result = runCatching(block)
            latch.countDown()
        }
        if (!latch.await(15, TimeUnit.SECONDS)) throw IllegalStateException("main thread did not answer in 15s")
        return result!!.getOrThrow()
    }

    private fun requireGame(): JSONObject? =
        if (MainActivityRuntime.eState.value == EmuState.STOPPED || !MainActivityRuntime.nativeReady.value)
            JSONObject().put("error", "no game running") else null

    private fun gameJson(g: GameInfo?): JSONObject? = g?.let {
        JSONObject().put("title", it.title).put("serial", it.serial ?: JSONObject.NULL)
            .put("uri", it.uri.toString()).put("platform", it.platform.name)
    }

    private fun dataRoot(): File = File(MainActivityRuntime.assetCopyRoot(context))

    // ---- tools ------------------------------------------------------------------------------

    private fun status(@Suppress("UNUSED_PARAMETER") args: JSONObject): JSONObject = onMain {
        JSONObject()
            .put("state", MainActivityRuntime.eState.value.name)
            .put("nativeReady", MainActivityRuntime.nativeReady.value)
            .put("game", gameJson(MainActivityRuntime.currentGame.value) ?: JSONObject.NULL)
            .put("contextGame", gameJson(MainActivityRuntime.contextGame.value) ?: JSONObject.NULL)
            .put("liveSerial", runCatching { NativeApp.getGameSerial() }.getOrNull() ?: JSONObject.NULL)
            .put("dataRoot", dataRoot().absolutePath)
            .put("server", JSONObject().put("port", DevServer.port.value))
            .put("textures", textureStats())
    }

    private fun libraryGames(): List<GameInfo> =
        runCatching { GameLibraryRepository(context).loadCached().games }.getOrDefault(emptyList())

    private fun library(args: JSONObject): JSONObject {
        val q = args.optString("query").trim().lowercase()
        val games = libraryGames().filter {
            q.isEmpty() || it.title.lowercase().contains(q) || (it.serial ?: "").lowercase().contains(q)
        }
        return JSONObject().put("count", games.size).put("games", JSONArray(games.map { gameJson(it) }))
    }

    private fun boot(args: JSONObject): JSONObject {
        val serial = args.optString("serial").trim()
        val title = args.optString("title").trim().lowercase()
        val uri = args.optString("uri").trim()
        val games = libraryGames()
        val game = when {
            uri.isNotEmpty() -> games.firstOrNull { it.uri.toString() == uri }
            serial.isNotEmpty() -> games.firstOrNull { it.serial.equals(serial, ignoreCase = true) }
            title.isNotEmpty() -> games.firstOrNull { it.title.lowercase() == title }
                ?: games.firstOrNull { it.title.lowercase().contains(title) }
            else -> null
        }
        if (game == null && uri.isEmpty()) return JSONObject().put("error", "no library game matches")
        // Same conversion HomeViewModel.launch makes: a file: URI is handed to the core as a
        // plain path (the core opens paths, not URIs); content: URIs go through as-is.
        val parsed = game?.uri ?: android.net.Uri.parse(uri)
        val target = if (parsed.scheme == "file") parsed.path ?: parsed.toString() else parsed.toString()
        onMain { MainActivityRuntime.launchGame(target, game) }
        return JSONObject().put("booting", gameJson(game) ?: JSONObject().put("uri", target))
    }

    private fun close(@Suppress("UNUSED_PARAMETER") args: JSONObject): JSONObject {
        onMain { MainActivityRuntime.closeGame() }
        return JSONObject().put("ok", true)
    }

    private fun screenshot(args: JSONObject): JSONObject {
        requireGame()?.let { return it }
        val path = args.optString("path").ifBlank {
            File(dataRoot(), "snaps").apply { mkdirs() }.let { File(it, "dev-${System.currentTimeMillis()}.png").absolutePath }
        }
        val file = File(path)
        file.delete()
        // The core writes the NEXT frame it draws; a paused VM draws none, so unpause briefly.
        val wasPaused = MainActivityRuntime.eState.value == EmuState.PAUSED
        if (wasPaused) onMain { MainActivityRuntime.resume() }
        NativeApp.saveScreenshot(path)
        val deadline = System.currentTimeMillis() + 5000
        var lastSize = -1L
        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(100)
            if (file.isFile && file.length() > 0 && file.length() == lastSize) break
            lastSize = if (file.isFile) file.length() else -1L
        }
        if (wasPaused) onMain { MainActivityRuntime.pause() }
        if (!file.isFile || file.length() == 0L) return JSONObject().put("error", "screenshot did not appear at $path")
        return JSONObject().put("path", path).put("bytes", file.length())
    }

    private fun scopeOf(args: JSONObject): Pair<SettingsScope, String?> {
        val serial = args.optString("serial").trim().ifEmpty { null }
        val scope = when (args.optString("scope").lowercase()) {
            "game" -> SettingsScope.Game
            "global" -> SettingsScope.Global
            else -> if (serial != null) SettingsScope.Game else SettingsScope.Global
        }
        return scope to serial
    }

    private fun running(): Boolean = MainActivityRuntime.eState.value != EmuState.STOPPED

    private fun settingsGet(args: JSONObject): JSONObject {
        val (scope, serial) = scopeOf(args)
        val live = !args.has("scope") && !args.has("serial") && running()
        val settings = when {
            live -> InGameOverlay.settingsState.value
            scope == SettingsScope.Game && serial != null -> ConfigStore.resolveForGame(serial)
            else -> ConfigStore.loadGlobal()
        }
        val tu = if (live) InGameOverlay.currentSerial.value.let { com.armsx2.config.TextureUpscaleStore.resolve(it) }
                 else com.armsx2.config.TextureUpscaleStore.forScope(scope, serial)
        return JSONObject().put("scope", scope.name).put("serial", serial ?: JSONObject.NULL)
            .put("settings", settings.toJson()).put("textureUpscale", tu.toJson())
    }

    private fun settingsSet(args: JSONObject): JSONObject {
        val patch = args.optJSONObject("patch") ?: return JSONObject().put("error", "patch object required")
        val (scope, serial) = scopeOf(args)
        val live = running() && !args.has("scope") && !args.has("serial")
        // Texture-time upscaling has its own store; a "textureUpscale" object in the patch
        // goes there, in the same scope, and is applied live through the GS reconfigure.
        val tuChanged = JSONObject()
        patch.optJSONObject("textureUpscale")?.let { tuPatch ->
            val tuScope = if (live) InGameOverlay.settingsScope.value else scope
            val tuSerial = if (live) InGameOverlay.currentSerial.value else serial
            val current = com.armsx2.config.TextureUpscaleStore.forScope(tuScope, tuSerial)
            val merged = current.toJson()
            for (k in tuPatch.keys()) if (merged.has(k)) merged.put(k, tuPatch.get(k))
            val updated = com.armsx2.config.TextureUpscaleSettings.fromJson(merged)
            val d = updated.diff(current)
            for (k in d.keys()) tuChanged.put(k, d.opt(k))
            onMain { com.armsx2.config.TextureUpscaleStore.saveAndApply(tuScope, tuSerial, updated, current) }
            patch.remove("textureUpscale")
        }
        val current = when {
            live -> InGameOverlay.settingsState.value
            scope == SettingsScope.Game && serial != null -> ConfigStore.resolveForGame(serial)
            else -> ConfigStore.loadGlobal()
        }
        val merged = current.toJson()
        val unknown = JSONArray()
        for (key in patch.keys()) {
            if (merged.has(key)) merged.put(key, patch.get(key)) else unknown.put(key)
        }
        val updated = Settings.fromJson(merged)
        val changed = JSONObject()
        val before = current.toJson()
        val after = updated.toJson()
        for (key in after.keys()) if (before.opt(key).toString() != after.opt(key).toString()) changed.put(key, after.opt(key))
        onMain {
            if (live) InGameOverlay.saveSettings(updated)
            else ConfigStore.save(scope, serial, updated, current)
        }
        return JSONObject().put("applied", if (live) "live" else scope.name).put("changed", changed)
            .put("textureUpscaleChanged", tuChanged).put("unknownKeys", unknown)
    }

    private fun hotkey(args: JSONObject): JSONObject {
        val name = args.optString("name").lowercase()
        val rt = MainActivityRuntime.instance ?: return JSONObject().put("error", "activity not running")
        when (name) {
            "reload_textures" -> onMain { rt.reloadTextures() }
            "fast_forward" -> onMain { rt.toggleFastForward() }
            "texture_dump" -> return textureDump(JSONObject())
            "quick_save" -> onMain { rt.saveState() }
            "quick_load" -> onMain { rt.loadState() }
            else -> return JSONObject().put("error", "unknown hotkey $name")
        }
        return JSONObject().put("ok", true).put("hotkey", name)
    }

    private fun textureStats(): JSONObject {
        val raw = runCatching { NativeApp.getTextureUpscaleStats() }.getOrNull()
        return runCatching { JSONObject(raw ?: "") }.getOrElse { JSONObject().put("unavailable", true) }
    }

    private fun textureDump(args: JSONObject): JSONObject {
        requireGame()?.let { return it }
        var state = NativeApp.toggleTextureDumping()
        if (args.has("on") && args.optBoolean("on") != state) state = NativeApp.toggleTextureDumping()
        return JSONObject().put("dumping", state)
    }

    private fun tail(lines: List<String>, args: JSONObject): JSONObject {
        val n = args.optInt("lines", 200).coerceIn(1, 5000)
        val filter = args.optString("filter")
        val picked = lines.filter { filter.isEmpty() || it.contains(filter, ignoreCase = true) }.takeLast(n)
        return JSONObject().put("count", picked.size).put("lines", JSONArray(picked))
    }

    private fun log(args: JSONObject): JSONObject {
        val file = File(dataRoot(), "logs/emulog.txt")
        if (!file.isFile) return JSONObject().put("error", "no ${file.absolutePath}")
        return tail(file.readLines(), args)
    }

    private fun logcat(args: JSONObject): JSONObject {
        val n = args.optInt("lines", 200).coerceIn(1, 5000)
        val process = ProcessBuilder("logcat", "-d", "-t", n.toString(), "--pid=${android.os.Process.myPid()}")
            .redirectErrorStream(true).start()
        val text = process.inputStream.bufferedReader().readText()
        process.waitFor()
        return tail(text.lines(), args)
    }
}
