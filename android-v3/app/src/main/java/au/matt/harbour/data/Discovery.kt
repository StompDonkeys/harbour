package au.matt.harbour.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.TimeUnit

/** Finds Harbour gateways on the home network by asking every address on the TV's subnet. */
object Discovery {
    data class Found(val url: String, val host: String, val version: String, val ready: Boolean)

    private const val PORT = 8765
    private val client = OkHttpClient.Builder()
        .connectTimeout(500, TimeUnit.MILLISECONDS)
        .readTimeout(1500, TimeUnit.MILLISECONDS)
        .build()

    /** This device's IPv4 addresses on the local network (Wi-Fi or Ethernet). */
    private fun localAddresses(): List<Inet4Address> = runCatching {
        NetworkInterface.getNetworkInterfaces().toList()
            .filter { it.isUp && !it.isLoopback && !it.isVirtual }
            .flatMap { it.inetAddresses.toList() }
            .filterIsInstance<Inet4Address>()
            .filter { it.isSiteLocalAddress }
    }.getOrDefault(emptyList())

    private fun probe(host: String): Found? = runCatching {
        val req = Request.Builder().url("http://$host:$PORT/?json=1").header("Accept", "application/json").build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) return null
            val o = Json.parseToJsonElement(r.body?.string().orEmpty()).jsonObject
            if (o["app"]?.jsonPrimitive?.content != "harbour-gateway") return null
            Found("http://$host:$PORT", host, o["version"]?.jsonPrimitive?.content ?: "?",
                o["setup"]?.jsonPrimitive?.content == "true")
        }
    }.getOrNull()

    /** Scan the /24 around each local address (the saved gateway, if any, first). Takes a few seconds. */
    suspend fun scan(hintHost: String? = null): List<Found> = withContext(Dispatchers.IO) {
        hintHost?.let { h -> probe(h)?.let { return@withContext listOf(it) } }
        val mine = localAddresses()
        val hosts = mine.flatMap { a ->
            val b = a.address
            val prefix = "${b[0].toInt() and 255}.${b[1].toInt() and 255}.${b[2].toInt() and 255}"
            (1..254).map { "$prefix.$it" }.filter { it != a.hostAddress }
        }.distinct()
        val gate = Semaphore(48)
        coroutineScope {
            hosts.map { h -> async { gate.withPermit { probe(h) } } }.awaitAll()
        }.filterNotNull().distinctBy { it.url }
    }
}
