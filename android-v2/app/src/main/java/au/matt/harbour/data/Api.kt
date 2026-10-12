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
    /** Discovery-style tabs (no A–Z library list). */
    val isDiscover: Boolean get() = kind == "seerr" || kind == "hub"
}

@Serializable
data class GatewayConfig(
    val name: String = "Harbour",
    val services: List<ServiceInfo> = emptyList(),
    val files: Boolean = false,
    val downloads: Boolean = false,
    val plex: Boolean = false,
    val repo: String = "StompDonkeys/harbour",
    val version: String = "",
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
    /** What the player plays for this card: plex:<key>, movie-<tmdb>, tv-<tmdb>. */
    val ref: String? = null,
)

@Serializable
data class MediaRow(
    val title: String,
    val style: String = "poster",
    val items: List<MediaItem> = emptyList(),
    /** Set on rows that can load more pages (Jellyseerr discovery). */
    val key: String? = null,
    val more: Boolean = false,
)

@Serializable
data class RowPage(
    val items: List<MediaItem> = emptyList(),
    val page: Int = 1,
    val hasMore: Boolean = false,
)

@Serializable
data class HomePage(
    val hero: MediaItem? = null,
    val rows: List<MediaRow> = emptyList(),
)

@Serializable
data class Fact(val label: String, val value: String)

@Serializable
data class ChoiceOption(val label: String, val value: String)

@Serializable
data class Choice(val title: String, val options: List<ChoiceOption> = emptyList())

@Serializable
data class ActionDef(
    val id: String,
    val label: String,
    val style: String? = null,
    val confirm: String? = null,
    val arg: String? = null,
    /** e.g. "releases": the app opens that screen instead of calling the gateway. */
    val opens: String? = null,
    /** Ask the user to pick one of these first; the pick is sent as `choice`. */
    val choose: Choice? = null,
)

@Serializable
data class Episode(
    val id: String,
    val label: String,
    val title: String,
    val subtitle: String? = null,
    val hasFile: Boolean = false,
    val actions: List<ActionDef> = emptyList(),
)

@Serializable
data class Season(
    val number: Int,
    val label: String,
    val subtitle: String? = null,
    val actions: List<ActionDef> = emptyList(),
    val episodes: List<Episode> = emptyList(),
)

@Serializable
data class Person(
    val id: String,
    val name: String,
    val role: String? = null,
    val photo: String? = null,
)

@Serializable
data class Detail(
    val item: MediaItem,
    val facts: List<Fact> = emptyList(),
    val actions: List<ActionDef> = emptyList(),
    val seasons: List<Season> = emptyList(),
    /** Cast, shown as round photos; each opens that person's page. */
    val people: List<Person> = emptyList(),
    /** Extra rows like "More like this" or a person's filmography. */
    val rows: List<MediaRow> = emptyList(),
)

@Serializable
data class NextUp(val ref: String, val title: String? = null, val subtitle: String? = null, val thumb: String? = null)

@Serializable
data class PlayInfo(
    val ref: String = "",
    val ratingKey: String,
    val url: String,
    val hls: Boolean = false,
    val offset: Long = 0,
    val duration: Long = 0,
    val title: String? = null,
    val subtitle: String? = null,
    val backdrop: String? = null,
    val badges: List<String> = emptyList(),
    val next: NextUp? = null,
)

@Serializable
data class PlexUser(val id: String, val title: String, val thumb: String? = null, val protected: Boolean = false)

@Serializable
data class PlexUsers(val users: List<PlexUser> = emptyList())

@Serializable
data class ActionResult(
    val message: String = "Done",
    val close: Boolean = false,
    val refresh: Boolean = true,
)

@Serializable
data class SearchResult(val items: List<MediaItem> = emptyList())

@Serializable
data class Release(
    val guid: String,
    val indexerId: Int = 0,
    val title: String,
    val quality: String? = null,
    val size: Long = 0,
    val indexer: String? = null,
    val age: String? = null,
    val peers: String? = null,
    val protocol: String? = null,
    val approved: Boolean = false,
    val rejections: List<String> = emptyList(),
    val score: Int? = null,
)

@Serializable
data class ReleaseList(val items: List<Release> = emptyList())

@Serializable
data class DlClient(
    val id: String,
    val name: String,
    val kind: String = "",
    val speed: Long = 0,
    val paused: Boolean = false,
    val count: Int = 0,
    val seeding: Int = 0,
    val error: String? = null,
)

@Serializable
data class DlItem(
    val client: String,
    val clientName: String = "",
    val id: String,
    val name: String,
    val status: String = "",
    val progress: Float = 0f,
    val size: Long = 0,
    val left: Long = 0,
    val speed: Long = 0,
    val eta: Long = -1,
    val category: String? = null,
    val paused: Boolean = false,
    val canTop: Boolean = false,
    val canDeleteFiles: Boolean = false,
)

@Serializable
data class Downloads(
    val clients: List<DlClient> = emptyList(),
    val items: List<DlItem> = emptyList(),
)

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

class Gateway(baseUrl: String, val token: String, val plexUser: String = "", val plexPin: String = "") {
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
                .header("X-Harbour-App", "2")
                .apply {
                    if (plexUser.isNotBlank()) header("X-Plex-User", plexUser)
                    if (plexPin.isNotBlank()) header("X-Plex-Pin", plexPin)
                }
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

suspend fun Gateway.rowPage(s: ServiceInfo, key: String, page: Int): RowPage =
    get("/api/svc/${s.kind}/${s.inst}/row?key=${Gateway.enc(key)}&page=$page")

suspend fun Gateway.detail(s: ServiceInfo, id: String): Detail =
    get("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}")

suspend fun Gateway.search(s: ServiceInfo, q: String): SearchResult =
    get("/api/svc/${s.kind}/${s.inst}/search?q=${Gateway.enc(q)}")

suspend fun Gateway.runAction(s: ServiceInfo, id: String, a: ActionDef, choice: String? = null): ActionResult =
    post("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}/action", buildJsonObject {
        put("action", a.id)
        if (a.arg != null) put("arg", a.arg)
        if (choice != null) put("choice", choice)
    })

suspend fun Gateway.releases(s: ServiceInfo, id: String, arg: String?): ReleaseList =
    get("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}/releases?arg=${Gateway.enc(arg ?: "")}")

suspend fun Gateway.grab(s: ServiceInfo, id: String, r: Release): ActionResult =
    post("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}/grab", buildJsonObject {
        put("guid", r.guid)
        put("indexerId", r.indexerId)
    })

suspend fun Gateway.downloads(): Downloads = get("/api/downloads")

suspend fun Gateway.plexUsers(): PlexUsers = get("/api/plex/users")

suspend fun Gateway.play(ref: String, transcode: Boolean = false): PlayInfo =
    get("/api/play?ref=${Gateway.enc(ref)}" + if (transcode) "&transcode=1" else "")

suspend fun Gateway.plexProgress(ratingKey: String, state: String, timeMs: Long, durationMs: Long) {
    call("POST", "/api/plex/progress", buildJsonObject {
        put("ratingKey", ratingKey)
        put("state", state)
        put("time", timeMs)
        put("duration", durationMs)
    }.toString())
}

suspend fun Gateway.downloadAction(client: String, id: String?, action: String): ActionResult =
    post("/api/downloads/action", buildJsonObject {
        put("client", client)
        if (id != null) put("id", id)
        put("action", action)
    })

suspend fun Gateway.report(text: String, version: String): Unit {
    call("POST", "/api/log", buildJsonObject {
        put("text", text)
        put("version", version)
    }.toString())
}

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
    var plexUser: String
        get() = sp.getString("plexUser", "").orEmpty()
        set(v) { sp.edit().putString("plexUser", v).apply() }
    var plexUserName: String
        get() = sp.getString("plexUserName", "").orEmpty()
        set(v) { sp.edit().putString("plexUserName", v).apply() }
    var plexPin: String
        get() = sp.getString("plexPin", "").orEmpty()
        set(v) { sp.edit().putString("plexPin", v).apply() }
}
