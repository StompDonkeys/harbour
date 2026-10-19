package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.ActHistoryItem
import au.matt.harbour.data.ActStats
import au.matt.harbour.data.ActStream
import au.matt.harbour.data.ActTop
import au.matt.harbour.data.ActUser
import au.matt.harbour.data.Activity
import au.matt.harbour.data.activity
import au.matt.harbour.data.activityHistory
import au.matt.harbour.data.activityStats
import au.matt.harbour.data.activityUsers
import coil.compose.AsyncImage
import kotlinx.coroutines.delay

/**
 * Plex activity from Tautulli: who's watching right now, the watch history, and the most watched
 * titles, people and devices.
 */
@Composable
fun ActivityScreen() {
    var mode by rememberSaveable { mutableStateOf("now") }   // now | history | top
    var header by remember { mutableStateOf("") }
    val firstTab = remember { FocusRequester() }

    Column(Modifier.fillMaxSize().padding(start = 40.dp, end = 36.dp, top = 6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Plex activity", fontSize = 26.sp, fontWeight = FontWeight.Bold)
                Text(header.ifBlank { " " }, color = HColors.TextDim, fontSize = 14.sp, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                TvButton("Now playing", { mode = "now" }, style = if (mode == "now") "primary" else null,
                    focusRequester = firstTab)
                TvButton("History", { mode = "history" }, style = if (mode == "history") "primary" else null)
                TvButton("Top", { mode = "top" }, style = if (mode == "top") "primary" else null)
            }
        }
        Spacer(Modifier.height(14.dp))
        Box(Modifier.weight(1f)) {
            when (mode) {
                "now" -> NowPlaying { header = it }
                "history" -> HistoryList { header = it }
                else -> TopLists { header = it }
            }
        }
    }
    LaunchedEffect(Unit) {
        delay(80)
        runCatching { firstTab.requestFocus() }
    }
}

@Composable
private fun NowPlaying(onHeader: (String) -> Unit) {
    val gw = LocalGateway.current
    var data by remember { mutableStateOf<Activity?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) }
    LaunchedEffect(retry) {
        while (true) {
            try {
                val a = gw.activity()
                data = a
                err = null
                onHeader(
                    if (a.count == 0) "Nobody's watching right now"
                    else "${a.count} stream${if (a.count == 1) "" else "s"}  ·  ${kbps(a.bandwidth)}" +
                        (if (a.wan > 0) "  (${kbps(a.wan)} remote)" else ""),
                )
            } catch (e: Exception) {
                if (data == null) err = e.message
            }
            delay(10_000)
        }
    }
    val d = data
    when {
        err != null && d == null -> ErrorPanel(err ?: "", onRetry = { err = null; retry++ })
        d == null -> Loading()
        d.streams.isEmpty() -> Text("Streams show up here as soon as someone presses play.",
            color = HColors.TextDim, fontSize = 16.sp)
        else -> LazyColumn(
            verticalArrangement = Arrangement.spacedBy(10.dp),
            contentPadding = PaddingValues(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 48.dp),
        ) {
            itemsIndexed(d.streams, key = { _, s -> s.id }) { _, s -> StreamRow(s) }
        }
    }
}

@Composable
private fun StreamRow(s: ActStream) {
    val gw = LocalGateway.current
    FocusBox(onClick = {}, modifier = Modifier.fillMaxWidth(), focusedScale = 1.01f) { f ->
        Row(
            Modifier.fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Poster(gw.img(s.poster), 72.dp)
            Spacer(Modifier.width(16.dp))
            Column(Modifier.weight(1f)) {
                Text(s.title ?: "", fontSize = 18.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                s.subtitle?.let { Text(it, fontSize = 14.sp, color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                Text(listOfNotNull(s.user, s.player ?: s.platform).joinToString("  ·  "),
                    fontSize = 14.sp, color = HColors.Text, maxLines = 1)
                Spacer(Modifier.height(8.dp))
                Box(
                    Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(3.dp))
                        .background(Color.White.copy(alpha = 0.18f)),
                ) {
                    Box(
                        Modifier.fillMaxWidth((s.progress / 100f).coerceIn(0f, 1f)).fillMaxHeight()
                            .background(if (s.state == "paused") HColors.Muted else HColors.Info),
                    )
                }
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        (when (s.state) { "paused" -> "Paused"; "buffering" -> "Buffering"; else -> "Playing" }) +
                            (if (s.duration > 0) "  ${clock(s.position)} / ${clock(s.duration)}" else "  ${s.progress}%"),
                        fontSize = 12.sp, color = HColors.TextDim,
                    )
                    Spacer(Modifier.width(4.dp))
                    s.decision?.let { Pill(it, if (it == "Transcode") "warn" else if (it == "Direct play") "ok" else null) }
                    s.quality?.let { Pill(it, null) }
                    if (s.bandwidth > 0) Pill(kbps(s.bandwidth), null)
                    s.location?.let { Pill(it, null) }
                }
            }
        }
    }
}

@Composable
private fun HistoryList(onHeader: (String) -> Unit) {
    val gw = LocalGateway.current
    var users by remember { mutableStateOf<List<ActUser>>(emptyList()) }
    var who by rememberSaveable { mutableStateOf<Long?>(null) }
    var items by remember { mutableStateOf<List<ActHistoryItem>>(emptyList()) }
    var total by remember { mutableIntStateOf(0) }
    var loading by remember { mutableStateOf(true) }
    var err by remember { mutableStateOf<String?>(null) }
    var more by remember { mutableIntStateOf(0) }
    var retry by remember { mutableIntStateOf(0) }

    LaunchedEffect(Unit) { runCatching { gw.activityUsers() }.getOrNull()?.let { users = it.users } }
    LaunchedEffect(who, retry) {
        loading = true
        err = null
        items = emptyList()
        try {
            val r = gw.activityHistory(0, who)
            items = r.items
            total = r.total
            onHeader("$total plays" + (users.firstOrNull { it.id == who }?.name?.let { " by $it" } ?: ""))
        } catch (e: Exception) {
            err = e.message
        } finally {
            loading = false
        }
    }
    LaunchedEffect(more) {
        if (more == 0 || items.size >= total) return@LaunchedEffect
        runCatching { gw.activityHistory(items.size, who) }.getOrNull()?.let { items = items + it.items }
    }

    Column {
        if (users.size > 1) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip("Everyone", who == null) { who = null }
                users.take(10).forEach { u -> FilterChip(u.name ?: "?", who == u.id) { who = u.id } }
            }
            Spacer(Modifier.height(10.dp))
        }
        Box(Modifier.weight(1f)) {
            when {
                err != null -> ErrorPanel(err ?: "", onRetry = { retry++ })
                loading -> Loading()
                items.isEmpty() -> Text("No plays recorded yet.", color = HColors.TextDim, fontSize = 16.sp)
                else -> LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    contentPadding = PaddingValues(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 48.dp),
                ) {
                    itemsIndexed(items, key = { i, x -> "$i:${x.id}" }) { i, x ->
                        HistoryRow(x)
                        if (i == items.lastIndex - 5 || i == items.lastIndex) LaunchedEffect(items.size) { more++ }
                    }
                }
            }
        }
    }
}

@Composable
private fun HistoryRow(x: ActHistoryItem) {
    val gw = LocalGateway.current
    FocusBox(onClick = {}, modifier = Modifier.fillMaxWidth(), focusedScale = 1.01f) { f ->
        Row(
            Modifier.fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Poster(gw.img(x.poster), 40.dp)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(listOfNotNull(x.title, x.subtitle).joinToString("  ·  "), fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(
                    listOfNotNull(x.user, x.started?.let { ago(it) }, x.player ?: x.platform, x.decision, x.location)
                        .joinToString("  ·  "),
                    fontSize = 12.sp, color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(if (x.watched > 0) length(x.watched) else "", fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                Text(if (x.complete) "✓ watched" else "${x.percent}%", fontSize = 12.sp,
                    color = if (x.complete) HColors.Ok else HColors.TextDim)
            }
        }
    }
}

@Composable
private fun TopLists(onHeader: (String) -> Unit) {
    val gw = LocalGateway.current
    var days by rememberSaveable { mutableIntStateOf(30) }
    var stats by remember { mutableStateOf<ActStats?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) }
    LaunchedEffect(days, retry) {
        stats = null
        err = null
        try {
            stats = gw.activityStats(days)
            onHeader(if (days >= 365) "The last year" else "The last $days days")
        } catch (e: Exception) {
            err = e.message
        }
    }
    Column {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(7 to "7 days", 30 to "30 days", 90 to "90 days", 365 to "Year").forEach { (d, l) ->
                FilterChip(l, days == d) { days = d }
            }
        }
        Spacer(Modifier.height(12.dp))
        val s = stats
        when {
            err != null -> ErrorPanel(err ?: "", onRetry = { retry++ })
            s == null -> Loading()
            else -> LazyColumn(
                verticalArrangement = Arrangement.spacedBy(6.dp),
                contentPadding = PaddingValues(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 48.dp),
            ) {
                fun section(title: String, list: List<ActTop>, art: Boolean) {
                    if (list.isEmpty()) return
                    item(key = "h:$title") {
                        Text(title.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp,
                            color = HColors.TextDim, modifier = Modifier.padding(top = 10.dp, bottom = 2.dp))
                    }
                    itemsIndexed(list, key = { i, _ -> "$title:$i" }) { i, x -> TopRow(i + 1, x, art) }
                }
                section("Most watched movies", s.movies, true)
                section("Most watched shows", s.shows, true)
                section("Top people", s.users, false)
                section("Top devices", s.platforms, false)
                if (s.movies.isEmpty() && s.shows.isEmpty() && s.users.isEmpty()) {
                    item { Text("Nothing watched in this period.", color = HColors.TextDim, fontSize = 16.sp) }
                }
            }
        }
    }
}

@Composable
private fun TopRow(rank: Int, x: ActTop, art: Boolean) {
    val gw = LocalGateway.current
    FocusBox(onClick = {}, modifier = Modifier.fillMaxWidth(), focusedScale = 1.01f) { f ->
        Row(
            Modifier.fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("$rank", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = HColors.TextFaint,
                modifier = Modifier.width(28.dp))
            if (art) {
                Poster(gw.img(x.poster), 36.dp)
                Spacer(Modifier.width(12.dp))
            }
            Text(x.title ?: x.name ?: "Unknown", fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(
                listOfNotNull(
                    "${x.plays} play${if (x.plays == 1) "" else "s"}",
                    if (x.users > 0) "${x.users} ${if (x.users == 1) "person" else "people"}" else null,
                    if (x.duration > 0) length(x.duration) else null,
                ).joinToString("  ·  "),
                fontSize = 13.sp, color = HColors.TextDim,
            )
        }
    }
}

@Composable
private fun Poster(url: String?, width: Dp) {
    Box(
        Modifier.width(width).height(width * 1.5f).clip(RoundedCornerShape(6.dp)).background(HColors.SurfaceHi),
    ) {
        if (url != null) {
            AsyncImage(model = url, contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize())
        }
    }
}

private fun kbps(k: Long): String = if (k >= 1000) "%.1f Mbps".format(k / 1000.0) else "$k kbps"

private fun clock(sec: Long): String {
    val h = sec / 3600
    val m = sec % 3600 / 60
    val s = sec % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

private fun length(sec: Long): String {
    val h = sec / 3600
    val m = (sec % 3600 + 30) / 60
    return if (h > 0) "${h}h ${m}m" else "${m}m"
}

private fun ago(t: Long): String {
    val s = System.currentTimeMillis() / 1000 - t
    return when {
        s < 90 -> "just now"
        s < 3600 -> "${s / 60} min ago"
        s < 86400 -> "${s / 3600} h ago"
        s < 7 * 86400 -> "${s / 86400} d ago"
        else -> java.text.SimpleDateFormat("d MMM yyyy", java.util.Locale.getDefault()).format(java.util.Date(t * 1000))
    }
}
