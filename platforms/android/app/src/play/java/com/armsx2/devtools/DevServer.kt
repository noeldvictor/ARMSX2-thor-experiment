package com.armsx2.devtools

import android.content.Context
import androidx.compose.runtime.mutableStateOf

/**
 * Play-flavor stub. The on-device MCP server is github-only (docs/mcp-server.md); this keeps
 * the call sites compiling with no server code, no socket and no toggle in the AAB.
 */
object DevServer {
    const val DEFAULT_PORT = 0
    val enabled = mutableStateOf(false)
    val running = mutableStateOf(false)
    val port = mutableStateOf(DEFAULT_PORT)

    @Suppress("UNUSED_PARAMETER")
    fun load(context: Context) = Unit

    @Suppress("UNUSED_PARAMETER")
    fun setEnabled(context: Context, on: Boolean) = Unit

    @Suppress("UNUSED_PARAMETER")
    fun start(context: Context) = Unit

    fun stop() = Unit
}
