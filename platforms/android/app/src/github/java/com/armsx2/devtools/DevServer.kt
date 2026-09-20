package com.armsx2.devtools

import android.content.Context
import android.util.Log
import androidx.compose.runtime.mutableStateOf
import com.armsx2.BuildConfig
import com.armsx2.runtime.MainActivityRuntime
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * The on-device MCP server (docs/mcp-server.md). Github flavor only; the play flavor has a
 * no-op stub with the same API.
 *
 * Binds 127.0.0.1 only and is reached over `adb forward tcp:PORT tcp:PORT`. Off by default;
 * started by the App-settings toggle or by launching the app with `--ez devserver true`,
 * and shown as a chip in the library and in-game overlays while it is up.
 *
 * Transport is MCP's Streamable HTTP in its simplest legal form: JSON-RPC 2.0 over
 * `POST /mcp` with `application/json` responses, no SSE. Every tool is also reachable as
 * `POST /tool/<name>` with the arguments as the body, so a plain curl works without an
 * MCP client, and `GET /screenshot` returns the PNG bytes directly.
 *
 * The HTTP parsing is deliberately hand-rolled: a few dozen lines against one trusted
 * peer on localhost is not worth a server dependency the play flavor cannot ship.
 */
object DevServer {
    private const val TAG = "DevServer"
    private const val PREF_ENABLED = "dev.mcp.enabled"
    const val DEFAULT_PORT = 27183

    /** Persisted toggle (App settings). */
    val enabled = mutableStateOf(false)

    /** Live state, for the indicator chips. */
    val running = mutableStateOf(false)
    val port = mutableStateOf(DEFAULT_PORT)

    private var server: ServerSocket? = null
    private var acceptThread: Thread? = null
    private val workers = Executors.newFixedThreadPool(2)
    @Volatile private var appContext: Context? = null

    fun load(context: Context) {
        appContext = context.applicationContext
        enabled.value = runCatching { MainActivityRuntime.prefs.getBoolean(PREF_ENABLED, false) }.getOrDefault(false)
        if (enabled.value) start(context)
    }

    fun setEnabled(context: Context, on: Boolean) {
        enabled.value = on
        runCatching { MainActivityRuntime.prefs.edit().putBoolean(PREF_ENABLED, on).apply() }
        if (on) start(context) else stop()
    }

    @Synchronized
    fun start(context: Context) {
        if (running.value) return
        appContext = context.applicationContext
        val socket = try {
            ServerSocket(port.value, 4, InetAddress.getLoopbackAddress())
        } catch (e: Exception) {
            Log.e(TAG, "bind 127.0.0.1:${port.value} failed: ${e.message}")
            return
        }
        server = socket
        running.value = true
        Log.i(TAG, "MCP server listening on 127.0.0.1:${port.value}")
        acceptThread = thread(name = "armsx2-devserver", isDaemon = true) {
            while (true) {
                val client = try { socket.accept() } catch (_: SocketException) { break } catch (_: Exception) { break }
                workers.execute { runCatching { handle(client) }.onFailure { Log.w(TAG, "request failed: ${it.message}") } }
            }
            running.value = false
        }
    }

    @Synchronized
    fun stop() {
        runCatching { server?.close() }
        server = null
        running.value = false
    }

    // ---- HTTP -------------------------------------------------------------------------------

    private class Request(val method: String, val path: String, val headers: Map<String, String>, val body: ByteArray)

    private fun handle(client: Socket) {
        client.soTimeout = 15_000
        client.use { c ->
            val input = BufferedInputStream(c.getInputStream())
            val request = readRequest(input) ?: return
            val output = c.getOutputStream()
            try {
                route(request, output)
            } catch (e: Exception) {
                Log.w(TAG, "${request.method} ${request.path}: ${e}")
                respond(output, 500, "application/json", JSONObject().put("error", e.toString()).toString().toByteArray())
            }
            output.flush()
        }
    }

    private fun readRequest(input: InputStream): Request? {
        val line = readLine(input) ?: return null
        val parts = line.trim().split(' ')
        if (parts.size < 2) return null
        val headers = HashMap<String, String>()
        while (true) {
            val h = readLine(input) ?: return null
            if (h.isEmpty()) break
            val idx = h.indexOf(':')
            if (idx > 0) headers[h.substring(0, idx).trim().lowercase()] = h.substring(idx + 1).trim()
        }
        val length = headers["content-length"]?.toIntOrNull() ?: 0
        val body = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = input.read(body, read, length - read)
            if (n < 0) break
            read += n
        }
        return Request(parts[0].uppercase(), parts[1], headers, body)
    }

    private fun readLine(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b < 0) return if (buf.size() == 0) null else buf.toString("UTF-8")
            if (b == '\n'.code) break
            if (b != '\r'.code) buf.write(b)
            if (buf.size() > 16384) return null
        }
        return buf.toString("UTF-8")
    }

    private fun respond(out: OutputStream, status: Int, type: String, body: ByteArray) {
        val reason = when (status) { 200 -> "OK"; 202 -> "Accepted"; 400 -> "Bad Request"; 404 -> "Not Found"; else -> "Error" }
        val head = "HTTP/1.1 $status $reason\r\nContent-Type: $type\r\nContent-Length: ${body.size}\r\nConnection: close\r\nAccess-Control-Allow-Origin: *\r\n\r\n"
        out.write(head.toByteArray())
        out.write(body)
    }

    private fun json(out: OutputStream, status: Int, obj: Any) =
        respond(out, status, "application/json", obj.toString().toByteArray())

    private fun route(req: Request, out: OutputStream) {
        val context = appContext ?: return json(out, 500, JSONObject().put("error", "no context"))
        val tools = DevTools(context)
        when {
            req.method == "GET" && (req.path == "/" || req.path == "/info") ->
                json(out, 200, JSONObject()
                    .put("name", "armsx2-thor")
                    .put("version", BuildConfig.VERSION_NAME)
                    .put("mcp", "/mcp")
                    .put("tools", JSONArray(tools.list().map { it.getString("name") })))

            req.method == "GET" && req.path.startsWith("/screenshot") -> {
                val result = tools.call("screenshot", JSONObject())
                val path = result.optString("path")
                val file = java.io.File(path)
                if (path.isEmpty() || !file.isFile) json(out, 500, result)
                else respond(out, 200, "image/png", file.readBytes())
            }

            req.method == "POST" && req.path.startsWith("/tool/") -> {
                val name = req.path.removePrefix("/tool/").substringBefore('?')
                val args = runCatching { JSONObject(String(req.body, Charsets.UTF_8).ifBlank { "{}" }) }.getOrElse { JSONObject() }
                val result = tools.call(name, args)
                json(out, if (result.has("error")) 400 else 200, result)
            }

            req.method == "POST" && req.path.startsWith("/mcp") -> {
                val text = String(req.body, Charsets.UTF_8)
                val message = runCatching { JSONObject(text) }.getOrNull()
                    ?: return json(out, 400, rpcError(JSONObject.NULL, -32700, "parse error"))
                val reply = rpc(message, tools)
                if (reply == null) respond(out, 202, "text/plain", ByteArray(0)) else json(out, 200, reply)
            }

            else -> json(out, 404, JSONObject().put("error", "unknown route ${req.method} ${req.path}"))
        }
    }

    // ---- MCP / JSON-RPC ---------------------------------------------------------------------

    private fun rpcError(id: Any, code: Int, message: String): JSONObject =
        JSONObject().put("jsonrpc", "2.0").put("id", id).put("error", JSONObject().put("code", code).put("message", message))

    private fun rpcResult(id: Any, result: JSONObject): JSONObject =
        JSONObject().put("jsonrpc", "2.0").put("id", id).put("result", result)

    /** Returns null for notifications (no reply). */
    private fun rpc(message: JSONObject, tools: DevTools): JSONObject? {
        val method = message.optString("method")
        val id: Any = message.opt("id") ?: JSONObject.NULL
        val params = message.optJSONObject("params") ?: JSONObject()
        if (!message.has("id")) return null // notification: notifications/initialized, cancelled, ...
        return when (method) {
            "initialize" -> rpcResult(id, JSONObject()
                .put("protocolVersion", params.optString("protocolVersion").ifEmpty { "2025-06-18" })
                .put("capabilities", JSONObject().put("tools", JSONObject().put("listChanged", false)))
                .put("serverInfo", JSONObject().put("name", "armsx2-thor").put("version", BuildConfig.VERSION_NAME))
                .put("instructions", "ARMSX2 Thor dev server. Boot games, read/write settings, capture screenshots and read upscaler stats. Screenshots are written on the device; pull them with adb."))
            "ping" -> rpcResult(id, JSONObject())
            "tools/list" -> rpcResult(id, JSONObject().put("tools", JSONArray(tools.list())))
            "tools/call" -> {
                val name = params.optString("name")
                val args = params.optJSONObject("arguments") ?: JSONObject()
                val result = tools.call(name, args)
                rpcResult(id, JSONObject()
                    .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", result.toString(1))))
                    .put("isError", result.has("error")))
            }
            else -> rpcError(id, -32601, "method not found: $method")
        }
    }
}
