package com.armsx2

import android.content.Context
import androidx.compose.runtime.mutableIntStateOf
import com.armsx2.runtime.MainActivityRuntime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File
import java.util.Locale

/**
 * Answers "does this game have an HD texture pack" for the library cover badge: installed under
 * `<DataRoot>/textures/<SERIAL>/replacements`, or offered by the online catalog.
 *
 * Installed is decided by the folder, not the install record, because the folder is what
 * GSTextureReplacements actually scans: a pack copied in by hand works, so it counts. Available
 * is keyed by serial only, the way the catalog itself is; no title matching, since a wrong "HD"
 * on a cover is worse than a missing one.
 *
 * Same shape as [CheatPresenceIndex]: a cached set, invalidated by whoever writes the folder.
 */
object TexturePackPresenceIndex {
    enum class State { NONE, AVAILABLE, INSTALLED }

    /** Bumped when either set changes so cover cards recompose. Read it in the composable. */
    val generation = mutableIntStateOf(0)

    private val lock = Any()
    @Volatile private var available: Set<String> = emptySet()
    @Volatile private var installed: Installed? = null

    /** [revision] is the [TexturePackInstallState.revision] this was built under; an in-app
     *  install or delete bumps it, which is what retires the set without anyone calling
     *  [invalidateInstalled]. */
    private data class Installed(val rootPath: String, val revision: Int, val serials: Set<String>)

    fun stateFor(context: Context, game: GameInfo): State {
        if (game.platform != GamePlatform.PS2) return State.NONE
        val serial = normalize(game.serial) ?: return State.NONE
        if (serial in installedSerials(context.applicationContext)) return State.INSTALLED
        if (serial in available) return State.AVAILABLE
        return State.NONE
    }

    /** Rebuild the installed set on the next lookup. For writes that bypass the install record,
     *  such as a folder import or a delete from a file manager followed by a rescan. */
    fun invalidateInstalled() {
        synchronized(lock) { installed = null }
        generation.intValue++
    }

    /**
     * App start. Publishes whatever catalog is on disk right away, then refreshes over the
     * network when the cache is stale. [TextureCatalog.fetch] owns the TTL and the mirror order,
     * so this only decides WHEN, not HOW, and a miss on every mirror is simply no badge until the
     * Texture Packs screen next fetches. Nothing here touches the main thread.
     */
    fun warm(context: Context, scope: CoroutineScope) {
        val app = context.applicationContext
        scope.launch(Dispatchers.IO) {
            val result = runCatching { TextureCatalog.fetch(app) }.getOrNull() ?: return@launch
            publish(result.packs)
        }
    }

    /** A catalog just arrived, from [warm] or from the Texture Packs screen. Safe off-main. */
    fun publish(packs: List<TextureCatalog.Pack>) {
        val serials = packs.flatMapTo(HashSet()) { pack -> pack.serials.mapNotNull(::normalize) }
        if (serials == available) return
        available = serials
        generation.intValue++
    }

    private fun installedSerials(context: Context): Set<String> {
        val root = File(MainActivityRuntime.assetCopyRoot(context), "textures")
        val rootPath = root.absolutePath
        // Read inside the composable's call, so the card is subscribed to install-record changes.
        val revision = TexturePackInstallState.revision.value
        installed?.takeIf { it.rootPath == rootPath && it.revision == revision }?.let { return it.serials }

        synchronized(lock) {
            installed?.takeIf { it.rootPath == rootPath && it.revision == revision }?.let { return it.serials }
            // One listFiles plus a stat per serial folder. Deliberately NOT a walk: the Texture
            // Packs screen learned the hard way that walking a real pack's tree is thousands of
            // stat() calls (DBZ BT3: 4277 files), and this runs from the cover grid.
            val serials = root.listFiles().orEmpty()
                .filter { it.isDirectory && File(it, "replacements").isDirectory }
                .mapNotNullTo(HashSet()) { normalize(it.name) }
            installed = Installed(rootPath, revision, serials)
            return serials
        }
    }

    private fun normalize(serial: String?): String? =
        serial?.trim()?.uppercase(Locale.US)?.takeIf { it.isNotEmpty() }
}
