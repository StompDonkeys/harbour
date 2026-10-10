package au.matt.harbour.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.MediaItem
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.search

/**
 * Grid of posters. For Radarr/Sonarr it's your whole library A–Z, filtered as you type.
 * For Seerr it's a TMDB search.
 */
@Composable
fun BrowseScreen(svc: ServiceInfo, searchFirst: Boolean, onOpen: (String) -> Unit) {
    val gw = LocalGateway.current
    val cache = LocalCache.current
    val remote = svc.isDiscover

    var query by rememberSaveable { mutableStateOf("") }
    var asking by rememberSaveable { mutableStateOf(searchFirst) }
    var all by remember { mutableStateOf(if (remote) null else cache.browse[svc.key]) }
    var results by remember { mutableStateOf<List<MediaItem>?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var focused by remember { mutableStateOf<MediaItem?>(null) }
    var lastFocus by rememberSaveable { mutableStateOf<String?>(null) }
    var grabPending by remember { mutableStateOf(true) }
    var retry by remember { mutableIntStateOf(0) }

    LaunchedEffect(query, retry) {
        error = null
        try {
            if (remote) {
                if (query.isBlank()) {
                    results = emptyList()
                } else {
                    loading = true
                    results = gw.search(svc, query).items
                }
            } else if (all == null) {
                loading = true
                val items = gw.search(svc, "").items
                cache.browse[svc.key] = items
                all = items
            }
        } catch (e: Exception) {
            error = e.message
        } finally {
            loading = false
        }
    }

    val shown: List<MediaItem> = if (remote) {
        results.orEmpty()
    } else {
        val q = query.trim()
        all.orEmpty().filter { q.isEmpty() || it.title.contains(q, ignoreCase = true) }
    }
    val target = lastFocus ?: shown.firstOrNull()?.key

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(start = 48.dp, end = 48.dp, top = 30.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    if (remote) "Search ${svc.name}" else "All of ${svc.name}",
                    color = HColors.Text, fontSize = 26.sp, fontWeight = FontWeight.Bold,
                )
                val f = focused
                Text(
                    when {
                        f != null -> listOfNotNull(f.title, f.subtitle).joinToString("  ·  ")
                        loading -> "Loading…"
                        else -> "${shown.size} titles"
                    },
                    color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
            TvButton(if (query.isBlank()) "🔍  Search" else "🔍  “$query”", { asking = true })
            if (query.isNotBlank()) {
                TvButton("Clear", {
                    query = ""
                    lastFocus = null
                    grabPending = true
                })
            }
        }

        Box(Modifier.weight(1f)) {
            val err = error
            when {
                err != null -> ErrorPanel(err, onRetry = { retry++ })
                loading && shown.isEmpty() -> Loading()
                shown.isEmpty() -> Text(
                    if (remote && query.isBlank()) "Press Search to find movies and shows." else "No matches.",
                    color = HColors.TextDim, fontSize = 16.sp,
                    modifier = Modifier.padding(start = 48.dp, top = 24.dp),
                )
                else -> LazyVerticalGrid(
                    columns = GridCells.Adaptive(112.dp),
                    state = rememberLazyGridState(),
                    contentPadding = PaddingValues(start = 48.dp, end = 48.dp, top = 14.dp, bottom = 48.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                    verticalArrangement = Arrangement.spacedBy(20.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    itemsIndexed(shown, key = { i, it -> "$i:${it.key}" }) { _, item ->
                        val fr = remember { FocusRequester() }
                        if (grabPending && item.key == target) {
                            LaunchedEffect(Unit) {
                                runCatching { fr.requestFocus() }
                                grabPending = false
                            }
                        }
                        PosterCard(
                            item = item,
                            onClick = { onOpen(item.id) },
                            onFocused = {
                                focused = item
                                lastFocus = item.key
                            },
                            focusRequester = fr,
                        )
                    }
                }
            }
        }
    }

    if (asking) {
        TextInputDialog(
            title = if (remote) "Search movies & TV" else "Filter ${svc.name}",
            initial = query,
            imeAction = ImeAction.Search,
            onDone = {
                asking = false
                query = it.trim()
                lastFocus = null
                focused = null
                grabPending = true
            },
            onDismiss = { asking = false },
        )
    }
}
