package au.matt.harbour.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.MediaItem
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.rowPage
import kotlinx.coroutines.launch

/** A whole discovery category as a poster grid that keeps loading as you scroll down. */
@Composable
fun CategoryScreen(svc: ServiceInfo, key: String, title: String, onOpen: (String) -> Unit) {
    val gw = LocalGateway.current
    val cache = LocalCache.current
    val scope = rememberCoroutineScope()
    val cacheKey = "${svc.key}#$key"
    val items = remember { mutableStateListOf<MediaItem>().apply { addAll(cache.browse[cacheKey].orEmpty()) } }
    var page by remember { mutableIntStateOf(if (items.isEmpty()) 0 else (items.size + 19) / 20) }
    var hasMore by remember { mutableStateOf(true) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var focused by remember { mutableStateOf<MediaItem?>(null) }
    var lastFocus by rememberSaveable { mutableStateOf<String?>(null) }
    var grabPending by remember { mutableStateOf(true) }

    val loadMore: () -> Unit = {
        if (hasMore && !loading) {
            loading = true
            scope.launch {
                try {
                    val pg = gw.rowPage(svc, key, page + 1)
                    val seen = items.map { it.key }.toHashSet()
                    items.addAll(pg.items.filter { seen.add(it.key) })
                    cache.browse[cacheKey] = items.toList()
                    page = pg.page
                    hasMore = pg.hasMore && pg.items.isNotEmpty()
                    error = null
                } catch (e: Exception) {
                    error = e.message
                    hasMore = false
                } finally {
                    loading = false
                }
            }
        }
    }
    LaunchedEffect(Unit) { if (items.isEmpty()) loadMore() }
    val target = lastFocus ?: items.firstOrNull()?.key

    Column(Modifier.fillMaxSize()) {
        Column(Modifier.padding(start = 48.dp, end = 48.dp, top = 30.dp, bottom = 6.dp)) {
            Text(title, color = HColors.Text, fontSize = 26.sp, fontWeight = FontWeight.Bold)
            val f = focused
            Text(
                if (f != null) listOfNotNull(f.title, f.subtitle).joinToString("  ·  ")
                else "${items.size} titles" + if (hasMore) " so far" else "",
                color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        Box(Modifier.weight(1f)) {
            val err = error
            when {
                items.isEmpty() && err != null -> ErrorPanel(err, onRetry = { hasMore = true; error = null; loadMore() })
                items.isEmpty() -> Loading()
                else -> LazyVerticalGrid(
                    columns = GridCells.Adaptive(112.dp),
                    state = rememberLazyGridState(),
                    contentPadding = PaddingValues(start = 48.dp, end = 48.dp, top = 14.dp, bottom = 48.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
                    verticalArrangement = Arrangement.spacedBy(20.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    itemsIndexed(items, key = { _, it -> it.key }) { i, item ->
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
                                if (i >= items.size - 12) loadMore()
                            },
                            focusRequester = fr,
                        )
                    }
                    if (loading) {
                        item(span = { GridItemSpan(maxLineSpan) }) {
                            Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
                                CircularProgressIndicator(color = HColors.Accent)
                            }
                        }
                    }
                }
            }
        }
    }
}
