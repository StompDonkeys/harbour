package au.matt.harbour.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

// ------------------------------------------------------------------ models

@Serializable
data class ServiceInfo(
    val kind: String,
    val inst: String,
    val name: String,
    val icon: String? = null,
) {
    val key: String get() = "$kind/$inst"
    val isDiscover: Boolean get() = kind == "seerr"
}

@Serializable
data class GatewayConfig(
    val name: String = "Harbour",
    val services: List<ServiceInfo> = emptyList(),
    val files: Boolean = false,
)

@Serializable
data class MediaItem(
    val key: String,
    val id: String,
    val title: String,
    val subtitle: String? = null,
    val poster: String? = null,
    val backdrop: String? = null,
    val overview: String? = null,
    val badge: String? = null,
    val badgeTone: String? = null,
    val progress: Float? = null,
)

@Serializable
data class MediaRow(
    val title: String,
    val style: String = "poster",
    val items: List<MediaItem> = emptyList(),
)

@Serializable
data class HomePage(
    val hero: MediaItem? = null,
    val rows: List<MediaRow> = emptyList(),
)

@Serializable
data class Fact(val label: String, val value: String)

@Serializable
data class ActionDef(
    val id: String,
    val label: String,
    val style: String? = null,
    val confirm: String? = null,
    val arg: String? = null,
)

@Serializable
data class Episode(
    val id: String,
    val label: String,
    val title: String,
    val subtitle: String? = null,
    val hasFile: Boolean = false,
    val action: ActionDef? = null,
)

@Serializable
data class Season(
    val number: Int,
    val label: String,
    val subtitle: String? = null,
    val action: ActionDef? = null,
    val episodes: List<Episode> = emptyList(),
)

@Serializable
data class Detail(
    val item: MediaItem,
    val facts: List<Fact> = emptyList(),
    val actions: List<ActionDef> = emptyList(),
    val seasons: List<Season> = emptyList(),
)

@Serializable
data class ActionResult(
    val message: String = "Done",
    val close: Boolean = false,
    val refresh: Boolean = true,
)

@Serializable
data class SearchResult(val items: List<MediaItem> = emptyList())

@Serializable
data class FileEntry(
    val name: String,
    val path: String,
    val isDir: Boolean = false,
    val size: Long = 0,
    val modified: Long = 0,
)

@Serializable
data class FileListing(
    val path: String = "",
    val parent: String? = null,
    val items: List<FileEntry> = emptyList(),
)

// ------------------------------------------------------------------ client

class GatewayException(message: String) : Exception(message)

class Gateway(baseUrl: String, val token: String) {
    val base: String = baseUrl.trim().trimEnd('/').let {
        if (it.startsWith("http://") || it.startsWith("https://")) it else "http://$it"
    }

    val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        isLenient = true
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(200, TimeUnit.SECONDS)
        .build()

    @PublishedApi
    internal suspend fun call(method: String, path: String, body: String? = null): String =
        withContext(Dispatchers.IO) {
            val media = "application/json".toMediaType()
            val rb = when {
                body != null -> body.toRequestBody(media)
                method == "POST" -> "{}".toRequestBody(media)
                else -> null
            }
            val req = Request.Builder()
                .url(base + path)
                .header("X-Token", token)
                .method(method, rb)
                .build()
            try {
                client.newCall(req).execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    if (!resp.isSuccessful) {
                        val msg = runCatching {
                            json.parseToJsonElement(text).jsonObject["error"]?.jsonPrimitive?.content
                        }.getOrNull()
                        throw GatewayException(msg ?: "Gateway returned HTTP ${resp.code}")
                    }
                    text
                }
            } catch (e: IOException) {
                throw GatewayException("Can't reach the gateway (${e.message ?: e.javaClass.simpleName})")
            }
        }

    suspend inline fun <reified T> get(path: String): T = json.decodeFromString(call("GET", path))

    suspend inline fun <reified T> post(path: String, body: JsonObject): T =
        json.decodeFromString(call("POST", path, body.toString()))

    /** Gateway-relative image paths need the base URL and token. */
    fun img(url: String?): String? = when {
        url.isNullOrBlank() -> null
        url.startsWith("http") -> url
        else -> base + url + (if (url.contains('?')) "&" else "?") + "t=" + enc(token)
    }

    companion object {
        fun enc(s: String): String = URLEncoder.encode(s, "UTF-8").replace("+", "%20")
    }
}

// ------------------------------------------------------------------ endpoints

suspend fun Gateway.config(): GatewayConfig = get("/api/config")

suspend fun Gateway.home(s: ServiceInfo): HomePage = get("/api/svc/${s.kind}/${s.inst}/home")

suspend fun Gateway.detail(s: ServiceInfo, id: String): Detail =
    get("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}")

suspend fun Gateway.search(s: ServiceInfo, q: String): SearchResult =
    get("/api/svc/${s.kind}/${s.inst}/search?q=${Gateway.enc(q)}")

suspend fun Gateway.runAction(s: ServiceInfo, id: String, a: ActionDef): ActionResult =
    post("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}/action", buildJsonObject {
        put("action", a.id)
        if (a.arg != null) put("arg", a.arg)
    })

suspend fun Gateway.listFiles(path: String): FileListing = get("/api/files/list?path=${Gateway.enc(path)}")

suspend fun Gateway.fileOp(
    op: String,
    path: String? = null,
    paths: List<String> = emptyList(),
    dest: String? = null,
    name: String? = null,
): ActionResult = post("/api/files/op", buildJsonObject {
    put("op", op)
    if (path != null) put("path", path)
    if (paths.isNotEmpty()) putJsonArray("paths") { paths.forEach { add(it) } }
    if (dest != null) put("dest", dest)
    if (name != null) put("name", name)
})

// ------------------------------------------------------------------ prefs

class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("harbour", Context.MODE_PRIVATE)
    var url: String
        get() = sp.getString("url", "").orEmpty()
        set(v) { sp.edit().putString("url", v).apply() }
    var token: String
        get() = sp.getString("token", "").orEmpty()
        set(v) { sp.edit().putString("token", v).apply() }
}
