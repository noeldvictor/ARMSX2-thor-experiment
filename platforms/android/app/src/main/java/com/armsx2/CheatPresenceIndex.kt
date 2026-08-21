package com.armsx2

import android.content.Context
import com.armsx2.runtime.MainActivityRuntime
import java.io.File
import java.util.Locale

/** Indexes only real PNACH files in <DataRoot>/cheats for library cover badges. */
object CheatPresenceIndex {
    private val lock = Any()
    private val serialPattern = Regex(
        "\\b([A-Z]{4})[-_ .]?([0-9]{3,5})(?:\\.?([0-9]{2}))?\\b",
        RegexOption.IGNORE_CASE,
    )

    @Volatile
    private var cached: Index? = null

    private data class Index(
        val rootPath: String,
        val serials: Set<String>,
        val titles: Set<String>,
    )

    private data class BundledCheat(
        val serial: String,
        val title: String,
    )

    fun invalidate() {
        synchronized(lock) { cached = null }
    }

    fun hasCheats(context: Context, game: GameInfo): Boolean {
        if (game.platform != GamePlatform.PS2) return false
        val index = index(context.applicationContext)
        normalize(game.serial).takeIf(String::isNotEmpty)?.let { serial ->
            if (serial in index.serials) return true
        }

        val candidates = buildSet {
            addTitleTokens(game.title, this)
            addTitleTokens(game.uri.lastPathSegment?.substringBeforeLast('.'), this)
        }
        return candidates.any(index.titles::contains)
    }

    private fun index(context: Context): Index {
        val root = File(MainActivityRuntime.assetCopyRoot(context), "cheats")
        val rootPath = root.absolutePath
        cached?.takeIf { it.rootPath == rootPath }?.let { return it }

        synchronized(lock) {
            cached?.takeIf { it.rootPath == rootPath }?.let { return it }
            return buildIndex(context, root).also { cached = it }
        }
    }

    private fun buildIndex(context: Context, root: File): Index {
        val serials = mutableSetOf<String>()
        val titles = mutableSetOf<String>()
        val bundledCheats = loadBundledCheats(context)
        if (root.isDirectory) {
            root.walkTopDown()
                .filter(::isRealCheatPnach)
                .forEach { file ->
                    addSerialTokens(file.nameWithoutExtension, serials)
                    addTitleTokens(file.nameWithoutExtension, titles)
                    bundledCheats[file.nameWithoutExtension.uppercase(Locale.US)]?.let { cheat ->
                        addSerialTokens(cheat.serial, serials)
                        addTitleTokens(cheat.title, titles)
                    }
                    runCatching {
                        file.bufferedReader().useLines { lines ->
                            lines.take(80).forEach { line ->
                                addSerialTokens(line, serials)
                                if (line.trimStart().startsWith("gametitle", ignoreCase = true)) {
                                    addTitleTokens(line.substringAfter('=', ""), titles)
                                }
                            }
                        }
                    }
                }
        }
        return Index(root.absolutePath, serials, titles)
    }

    private fun loadBundledCheats(context: Context): Map<String, BundledCheat> = runCatching {
        context.assets.open("cheats/index.tsv").bufferedReader().useLines { lines ->
            lines.drop(1).mapNotNull { line ->
                val fields = line.split('\t', limit = 3)
                if (fields.size < 3) null
                else fields[0].uppercase(Locale.US) to BundledCheat(fields[1], fields[2])
            }.toMap()
        }
    }.getOrDefault(emptyMap())

    private fun isRealCheatPnach(file: File): Boolean {
        if (!file.isFile || file.length() <= 0L || !file.extension.equals("pnach", ignoreCase = true)) return false
        val name = file.name.lowercase(Locale.US)
        return listOf("widescreen", "wide-screen", "wide_screen", "60fps", "60-fps", "60_fps")
            .none(name::contains)
    }

    private fun addSerialTokens(value: String?, target: MutableSet<String>) {
        if (value.isNullOrBlank()) return
        serialPattern.findAll(value).forEach { match ->
            normalize(match.value).takeIf(String::isNotEmpty)?.let(target::add)
        }
    }

    private fun addTitleTokens(value: String?, target: MutableSet<String>) {
        if (value.isNullOrBlank()) return
        val variants = listOf(
            value,
            value.replace(Regex("\\[[^\\]]*\\]|\\([^)]*\\)|\\{[^}]*\\}"), " "),
            value.substringBefore('[').substringBefore('('),
        )
        variants.forEach { candidate ->
            normalize(candidate).takeIf { it.length >= 8 }?.let(target::add)
        }
    }

    private fun normalize(value: String?): String = value.orEmpty()
        .uppercase(Locale.US)
        .replace(Regex("[^A-Z0-9]"), "")
}
