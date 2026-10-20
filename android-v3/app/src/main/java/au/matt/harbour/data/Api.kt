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
    val calendar: Boolean = false,
    /** Harbour v2: merged Movies / TV libraries, the Home dashboard and in-app trailers. */
    val libraries: List<ServiceInfo> = emptyList(),
    val dashboard: Boolean = false,
    val trailers: Boolean = false,
    val indexers: Boolean = false,
    /** Tautulli is set up: Plex activity (now playing, history, top). */
    val activity: Boolean = false,
    /** Trakt is set up: Discussion on title and episode pages. */
    val discuss: Boolean = false,
    /** A newer gateway image is published (version), if any. */
    val gatewayUpdate: String? = null,
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
    /** A little menu of further actions (opens == "menu"). */
    val items: List<ActionDef> = emptyList(),
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
    // ---- v2 title page
    val tagline: String? = null,
    val meta: List<String> = emptyList(),
    val ratings: List<Fact> = emptyList(),
    val status: List<Chip> = emptyList(),
    val videos: List<Video> = emptyList(),
    val providers: List<Provider> = emptyList(),
    val seasonCards: List<MediaItem> = emptyList(),
    val logo: String? = null,
)

@Serializable
data class Chip(val text: String, val tone: String? = null)

@Serializable
data class Video(val key: String, val name: String = "Video", val type: String? = null, val thumb: String? = null)

@Serializable
data class Provider(val name: String = "", val logo: String? = null)

// ------------------------------------------------------------------ v2 season page

@Serializable
data class SeasonShow(val id: String, val title: String = "", val backdrop: String? = null, val poster: String? = null)

@Serializable
data class SeasonInfo(
    val number: Int,
    val label: String,
    val overview: String? = null,
    val poster: String? = null,
    val meta: String? = null,
)

@Serializable
data class SeasonRef(val number: Int, val label: String)

@Serializable
data class EpisodeInfo(
    val key: String,
    val number: Int,
    val label: String,
    val title: String,
    val overview: String? = null,
    val still: String? = null,
    val airDate: String? = null,
    val runtime: String? = null,
    val rating: String? = null,
    val status: String = "none",
    val statusLabel: String = "",
    val watched: Boolean = false,
    val progress: Float? = null,
    val actions: List<ActionDef> = emptyList(),
)

@Serializable
data class SeasonPage(
    val show: SeasonShow,
    val season: SeasonInfo,
    val seasons: List<SeasonRef> = emptyList(),
    val actions: List<ActionDef> = emptyList(),
    val episodes: List<EpisodeInfo> = emptyList(),
    val videos: List<Video> = emptyList(),
)

// ------------------------------------------------------------------ v2 dashboard

@Serializable
data class Stat(val label: String, val value: String, val sub: String? = null, val tone: String? = null)

@Serializable
data class Marker(val kind: String, val status: String)

@Serializable
data class WeekDay(
    val date: String,
    val dow: String,
    val day: Int,
    val count: Int = 0,
    val markers: List<Marker> = emptyList(),
    val entries: List<CalEntry> = emptyList(),
)

@Serializable
data class Dash(
    val greeting: String = "",
    val date: String = "",
    val hero: String? = null,
    val stats: List<Stat> = emptyList(),
    val today: List<CalEntry> = emptyList(),
    val week: List<WeekDay> = emptyList(),
    val rows: List<MediaRow> = emptyList(),
    val alerts: List<Chip> = emptyList(),
)

@Serializable
data class TrailerInfo(
    val title: String? = null,
    val duration: Double? = null,
    val height: Int? = null,
    val video: String,
    val audio: String? = null,
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

    /**
     * The same artwork at full-screen quality, for backdrops: TMDB's original instead of a
     * thumbnail size, and 1080p from Plex's transcoder. Cards keep the small, fast versions.
     */
    fun hero(url: String?): String? {
        if (url.isNullOrBlank()) return null
        val big = when {
            url.contains("image.tmdb.org/t/p/") -> url.replace(Regex("/t/p/[a-z0-9]+/"), "/t/p/original/")
            url.startsWith("/plexart?") -> url.replace(Regex("w=\\d+&h=\\d+"), "w=1920&h=1080")
            else -> url
        }
        return img(big)
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

suspend fun Gateway.grab(s: ServiceInfo, id: String, r: Release, arg: String? = null): ActionResult =
    post("/api/svc/${s.kind}/${s.inst}/item/${Gateway.enc(id)}/grab", buildJsonObject {
        put("guid", r.guid)
        put("indexerId", r.indexerId)
        if (arg != null) put("arg", arg)
    })

suspend fun Gateway.dash(): Dash = get("/api/dash")

suspend fun Gateway.season(s: ServiceInfo, showId: String, n: Int): SeasonPage =
    get("/api/svc/${s.kind}/${s.inst}/season/${Gateway.enc(showId)}/$n")

suspend fun Gateway.trailer(key: String): TrailerInfo = get("/api/trailer/${Gateway.enc(key)}")

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

// ------------------------------------------------------------------ calendar

@Serializable
data class CalSource(val svc: String, val kind: String, val name: String)

@Serializable
data class CalEntry(
    val key: String,
    val date: String,
    val time: String? = null,
    val kind: String,              // tv | movie
    val svc: String,               // kind/inst
    val source: String = "",
    val id: String,
    val open: String? = null,      // hub id: tv-<tmdb> / movie-<tmdb>
    val title: String,
    val subtitle: String? = null,
    val poster: String? = null,
    val backdrop: String? = null,
    val overview: String? = null,
    val status: String,            // downloaded | downloading | airing | missing | upcoming | cinema | unmonitored
    val label: String = "",
    val tag: String? = null,
    val network: String? = null,
    val quality: String? = null,
    val season: Int? = null,
    val episode: Int? = null,
    /** Where tapping it goes: tv-<tmdb>:s1e2 (an episode), movie-<tmdb>, or kind/inst/id. */
    val openId: String? = null,
)

@Serializable
data class CalendarData(
    val start: String,
    val end: String,
    val sources: List<CalSource> = emptyList(),
    val entries: List<CalEntry> = emptyList(),
)

suspend fun Gateway.calendar(start: String, end: String, unmonitored: Boolean): CalendarData =
    get("/api/calendar?start=$start&end=$end&unmonitored=${if (unmonitored) 1 else 0}")

// ------------------------------------------------------------------ indexer search

@Serializable
data class IdxResult(
    val id: String,
    val title: String,
    val indexer: String = "",
    val protocol: String = "usenet",   // usenet | torrent
    val size: Long = 0,
    val age: String? = null,
    val seeders: Int? = null,
    val leechers: Int? = null,
    val grabs: Int? = null,
    val category: String? = null,
)

@Serializable
data class IdxSearch(val results: List<IdxResult> = emptyList(), val errors: List<String> = emptyList())

@Serializable
data class IdxTargets(val torrent: List<ChoiceOption> = emptyList(), val usenet: List<ChoiceOption> = emptyList())

suspend fun Gateway.indexerSearch(q: String, kind: String, protocol: String): IdxSearch =
    get("/api/indexers/search?q=${Gateway.enc(q)}&kind=$kind&protocol=$protocol")

suspend fun Gateway.indexerTargets(): IdxTargets = get("/api/indexers/targets")

suspend fun Gateway.indexerGrab(id: String, target: String): ActionResult =
    post("/api/indexers/grab", buildJsonObject {
        put("id", id)
        put("target", target)
    })

// ------------------------------------------------------------------ pairing (V3)

@Serializable
data class PairStart(val id: String, val code: String, val expires: Int = 600)

@Serializable
data class PairStatus(val status: String, val token: String? = null)

suspend fun Gateway.pairStart(name: String): PairStart = post("/api/pair/start", buildJsonObject { put("name", name) })

suspend fun Gateway.pairStatus(id: String): PairStatus = get("/api/pair/status?id=${Gateway.enc(id)}")


// ---------------------------------------------------------------- Plex activity (Tautulli)

@Serializable
data class ActStream(
    val id: String = "",
    val user: String? = null,
    val title: String? = null,
    val subtitle: String? = null,
    val kind: String? = null,
    val poster: String? = null,
    val art: String? = null,
    val state: String? = null,
    val progress: Int = 0,
    val position: Long = 0,
    val duration: Long = 0,
    val player: String? = null,
    val platform: String? = null,
    val quality: String? = null,
    val decision: String? = null,
    val bandwidth: Long = 0,
    val location: String? = null,
)

@Serializable
data class Activity(
    val streams: List<ActStream> = emptyList(),
    val count: Int = 0,
    val bandwidth: Long = 0,
    val lan: Long = 0,
    val wan: Long = 0,
)

@Serializable
data class ActHistoryItem(
    val id: String = "",
    val user: String? = null,
    val title: String? = null,
    val subtitle: String? = null,
    val kind: String? = null,
    val poster: String? = null,
    val started: Long? = null,
    val watched: Long = 0,
    val percent: Int = 0,
    val complete: Boolean = false,
    val player: String? = null,
    val platform: String? = null,
    val decision: String? = null,
    val location: String? = null,
)

@Serializable
data class ActHistory(val items: List<ActHistoryItem> = emptyList(), val total: Int = 0)

@Serializable
data class ActTop(
    val title: String? = null,
    val name: String? = null,
    val plays: Int = 0,
    val duration: Long = 0,
    val users: Int = 0,
    val poster: String? = null,
)

@Serializable
data class ActStats(
    val days: Int = 30,
    val movies: List<ActTop> = emptyList(),
    val shows: List<ActTop> = emptyList(),
    val users: List<ActTop> = emptyList(),
    val platforms: List<ActTop> = emptyList(),
)

@Serializable
data class ActUser(val id: Long = 0, val name: String? = null)

@Serializable
data class ActUsers(val users: List<ActUser> = emptyList())

suspend fun Gateway.activity(): Activity = get("/api/activity")

suspend fun Gateway.activityHistory(start: Int, user: Long?): ActHistory =
    get("/api/activity/history?start=$start&length=40" + (user?.let { "&user=$it" } ?: ""))

suspend fun Gateway.activityStats(days: Int): ActStats = get("/api/activity/stats?days=$days")

suspend fun Gateway.activityUsers(): ActUsers = get("/api/activity/users")


// ---------------------------------------------------------------- Discussion (Trakt)

@Serializable
data class Comment(
    val id: Long = 0,
    val user: String = "",
    val text: String = "",
    val spoiler: Boolean = false,
    val review: Boolean = false,
    val likes: Int = 0,
    val replies: Int = 0,
    val rating: Int? = null,
    val created: String? = null,
)

@Serializable
data class Discussion(
    val title: String = "",
    val episodeTitle: String? = null,
    val url: String? = null,
    val rating: Int? = null,
    val votes: Int = 0,
    val page: Int = 1,
    val pages: Int = 1,
    val total: Int = 0,
    val comments: List<Comment> = emptyList(),
)

@Serializable
data class Replies(val comments: List<Comment> = emptyList())

suspend fun Gateway.discussion(ref: String, sort: String, page: Int): Discussion =
    get("/api/discuss?ref=${Gateway.enc(ref)}&sort=$sort&page=$page")

suspend fun Gateway.discussionReplies(id: Long): Replies = get("/api/discuss/replies/$id")
