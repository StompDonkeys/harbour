package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.IdxResult
import au.matt.harbour.data.IdxTargets
import au.matt.harbour.data.indexerGrab
import au.matt.harbour.data.indexerSearch
import au.matt.harbour.data.indexerTargets
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Search your indexers directly — usenet and torrent, through Prowlarr and/or your Newznab
 * indexers — and send a result straight to NZBGet or qBittorrent.
 */
@Composable
fun IndexerScreen(externalQuery: String? = null) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val scope = rememberCoroutineScope()
    var ownQuery by rememberSaveable { mutableStateOf("") }
    val query = externalQuery ?: ownQuery
    var kind by rememberSaveable { mutableStateOf("all") }        // all | movies | tv
    var protocol by rememberSaveable { mutableStateOf("all") }    // all | usenet | torrent
    var sort by rememberSaveable { mutableStateOf("best") }       // best | size | newest
    var asking by rememberSaveable { mutableStateOf(externalQuery == null) }
    var results by remember { mutableStateOf<List<IdxResult>?>(null) }
    var errors by remember { mutableStateOf<List<String>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf<String?>(null) }
    var run by remember { mutableIntStateOf(0) }
    var elapsed by remember { mutableIntStateOf(0) }
    var targets by remember { mutableStateOf<IdxTargets?>(null) }
    var picked by remember { mutableStateOf<IdxResult?>(null) }
    var busy by remember { mutableStateOf(false) }
    val first = remember { FocusRequester() }
    val searchBtn = remember { FocusRequester() }

    LaunchedEffect(run, externalQuery) {
        if (query.isBlank() || (externalQuery == null && run == 0)) return@LaunchedEffect
        loading = true
        failed = null
        results = null
        try {
            val r = gw.indexerSearch(query, kind, protocol)
            results = r.results
            errors = r.errors
        } catch (e: Exception) {
            failed = e.message
        } finally {
            loading = false
        }
    }
    LaunchedEffect(loading) {
        elapsed = 0
        while (loading) {
            delay(1000)
            elapsed++
        }
    }
    LaunchedEffect(Unit) { runCatching { gw.indexerTargets() }.getOrNull()?.let { targets = it } }

    val shown = remember(results, sort) {
        val list = results.orEmpty()
        when (sort) {
            "size" -> list.sortedByDescending { it.size }
            "newest" -> list.sortedBy { ageDays(it.age) }
            else -> list
        }
    }

    Column(Modifier.fillMaxSize().padding(start = 40.dp, end = 36.dp, top = 6.dp)) {
        if (externalQuery == null) Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Indexer search", fontSize = 26.sp, fontWeight = FontWeight.Bold)
                Text(
                    if (query.isBlank()) "Search your usenet and torrent indexers directly"
                    else "“$query”" + (results?.let { "  ·  ${it.size} results" } ?: ""),
                    color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            TvButton(if (query.isBlank()) "Search" else "New search", { asking = true },
                style = "primary", icon = Icons.Filled.Search, focusRequester = searchBtn)
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            FilterChip("All", kind == "all") { kind = "all"; if (query.isNotBlank()) run++ }
            FilterChip("Movies", kind == "movies") { kind = "movies"; if (query.isNotBlank()) run++ }
            FilterChip("TV", kind == "tv") { kind = "tv"; if (query.isNotBlank()) run++ }
            Spacer(Modifier.width(10.dp))
            FilterChip("Usenet + torrents", protocol == "all") { protocol = "all"; if (query.isNotBlank()) run++ }
            FilterChip("Usenet", protocol == "usenet") { protocol = "usenet"; if (query.isNotBlank()) run++ }
            FilterChip("Torrents", protocol == "torrent") { protocol = "torrent"; if (query.isNotBlank()) run++ }
            Spacer(Modifier.width(10.dp))
            FilterChip("Sort: " + mapOf("best" to "Best", "size" to "Biggest", "newest" to "Newest")[sort], false) {
                sort = when (sort) { "best" -> "size"; "size" -> "newest"; else -> "best" }
            }
        }
        if (errors.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Text("⚠ " + errors.joinToString("  ·  "), color = HColors.Warn, fontSize = 12.sp, maxLines = 2,
                overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(10.dp))
        Box(Modifier.weight(1f)) {
            val err = failed
            when {
                loading -> Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center) {
                    CircularProgressIndicator(color = HColors.Accent)
                    Spacer(Modifier.height(14.dp))
                    Text("Asking your indexers… ${elapsed}s", color = HColors.TextDim, fontSize = 15.sp)
                }
                err != null -> ErrorPanel(err, onRetry = { run++ })
                results == null -> Text(if (externalQuery != null) "Type a search above." else "Press Search and type what you're after.", color = HColors.TextDim, fontSize = 15.sp)
                shown.isEmpty() -> Text("Nothing found. Try fewer words, or switch the filters.",
                    color = HColors.TextDim, fontSize = 15.sp)
                else -> LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    contentPadding = PaddingValues(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 40.dp),
                ) {
                    itemsIndexed(shown, key = { _, r -> r.id }) { i, r ->
                        IdxRow(r, if (i == 0) first else null) { picked = r }
                        if (i == 0) LaunchedEffect(shown) { delay(60); runCatching { first.requestFocus() } }
                    }
                }
            }
        }
    }

    if (asking) {
        TextInputDialog(
            title = "Search indexers",
            initial = query,
            onDone = {
                asking = false
                if (it.isNotBlank()) {
                    ownQuery = it.trim()
                    run++
                }
            },
            onDismiss = {
                asking = false
                scope.launch { delay(80); runCatching { searchBtn.requestFocus() } }
            },
            imeAction = androidx.compose.ui.text.input.ImeAction.Search,
        )
    }
    picked?.let { r ->
        val opts = (if (r.protocol == "torrent") targets?.torrent else targets?.usenet).orEmpty()
        if (opts.isEmpty()) {
            LaunchedEffect(r) {
                toaster.show("⚠ No ${if (r.protocol == "torrent") "torrent" else "usenet"} download client is set up on the gateway")
                picked = null
            }
        } else {
            MenuDialog(
                title = "Send to…",
                subtitle = r.title,
                items = opts.map { it.label to null },
                onPick = { i ->
                    picked = null
                    val t = opts.getOrNull(i) ?: return@MenuDialog
                    busy = true
                    scope.launch {
                        try {
                            toaster.show(gw.indexerGrab(r.id, t.value).message)
                        } catch (e: Exception) {
                            toaster.show("⚠ ${e.message}")
                        } finally {
                            busy = false
                        }
                    }
                },
                onDismiss = { picked = null },
            )
        }
    }
    if (busy) BusyDialog("Sending…")
}

private fun ageDays(age: String?): Double {
    if (age.isNullOrBlank()) return 1e9
    val n = age.takeWhile { it.isDigit() }.toDoubleOrNull() ?: return 1e9
    return when {
        age.endsWith("mo") -> n * 30
        age.endsWith("h") -> n / 24
        age.endsWith("d") -> n
        age.endsWith("y") -> n * 365
        else -> n
    }
}

@Composable
internal fun FilterChip(text: String, on: Boolean, onClick: () -> Unit) {
    FocusBox(onClick = onClick, shape = RoundedCornerShape(999.dp), focusedScale = 1.06f, borderWidth = 0.dp) { f ->
        Text(
            text, fontSize = 13.sp, maxLines = 1,
            fontWeight = if (on || f) FontWeight.Bold else FontWeight.SemiBold,
            color = when {
                f -> Color(0xFF0A0A0B)
                on -> Color.White
                else -> HColors.TextDim
            },
            modifier = Modifier
                .background(when {
                    f -> HColors.Text
                    on -> HColors.Info
                    else -> HColors.GlassFill
                })
                .padding(horizontal = 13.dp, vertical = 7.dp),
        )
    }
}

@Composable
private fun IdxRow(r: IdxResult, fr: FocusRequester?, onClick: () -> Unit) {
    FocusBox(onClick = onClick, modifier = Modifier.fillMaxWidth(), focusedScale = 1.01f, focusRequester = fr) { f ->
        Row(
            Modifier
                .fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val torrent = r.protocol == "torrent"
            Text(
                if (torrent) "TORRENT" else "NZB",
                fontSize = 10.sp, fontWeight = FontWeight.ExtraBold, color = Color.White, letterSpacing = 0.6.sp,
                modifier = Modifier.clip(RoundedCornerShape(5.dp))
                    .background(if (torrent) HColors.Info else HColors.Ok.copy(alpha = 0.9f))
                    .padding(horizontal = 6.dp, vertical = 2.dp).widthIn(min = 44.dp),
            )
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(r.title, fontSize = 14.sp, maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 18.sp)
                Spacer(Modifier.height(3.dp))
                Text(
                    listOfNotNull(
                        r.indexer.ifBlank { null },
                        r.category,
                        r.age,
                        if (torrent) r.seeders?.let { "$it seeders" + (r.leechers?.let { l -> " · $l leechers" } ?: "") }
                        else r.grabs?.let { "$it grabs" },
                    ).joinToString("  ·  "),
                    color = if (torrent && (r.seeders ?: 0) == 0) HColors.Warn else HColors.TextDim,
                    fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            Spacer(Modifier.width(14.dp))
            Text(fmtBytes(r.size), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}
