package au.matt.harbour.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.HomePage
import au.matt.harbour.data.MediaItem
import au.matt.harbour.data.MediaRow
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.home
import kotlinx.coroutines.delay
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import au.matt.harbour.data.rowPage
import kotlinx.coroutines.launch

/** Netflix-style home for one Radarr / Sonarr / Seerr instance. */
@Composable
fun LibraryScreen(
    svc: ServiceInfo,
    onOpen: (String) -> Unit,
    onBrowse: (Boolean) -> Unit,
    onSeeAll: (key: String, title: String) -> Unit = { _, _ -> },
) {
    val gw = LocalGateway.current
    val cache = LocalCache.current
    val toaster = LocalToaster.current
    var page by remember { mutableStateOf(cache.homes[svc.key]) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload) {
        if (page != null && reload == 0) return@LaunchedEffect
        error = null
        try {
            val p = gw.home(svc)
            cache.homes[svc.key] = p
            page = p
            if (reload > 0) toaster.show("${svc.name} refreshed")
        } catch (e: Exception) {
            if (page == null) error = e.message else toaster.show("⚠ ${e.message}")
        }
    }

    val p = page
    val err = error
    when {
        p != null -> LibraryContent(svc, p, onOpen, onBrowse, onSeeAll, onRefresh = { reload++ })
        err != null -> ErrorPanel(err, onRetry = { reload++ })
        else -> Loading()
    }
}

@Composable
private fun LibraryContent(
    svc: ServiceInfo,
    page: HomePage,
    onOpen: (String) -> Unit,
    onBrowse: (Boolean) -> Unit,
    onSeeAll: (key: String, title: String) -> Unit,
    onRefresh: () -> Unit,
) {
    val gw = LocalGateway.current
    val firstItem = page.rows.firstOrNull()?.items?.firstOrNull()
    var featured by remember(page) { mutableStateOf(page.hero ?: firstItem) }
    // Debounce the big backdrop so fast D-pad scrolling doesn't thrash image loads.
    var backdrop by remember { mutableStateOf(featured?.let { gw.img(it.backdrop ?: it.poster) }) }
    LaunchedEffect(featured) {
        delay(280)
        backdrop = featured?.let { gw.img(it.backdrop ?: it.poster) }
    }

    var lastFocus by rememberSaveable { mutableStateOf<String?>(null) }
    var grabPending by remember { mutableStateOf(true) }
    val target = lastFocus ?: firstItem?.let { "0:${it.key}" }

    Box(Modifier.fillMaxSize()) {
        Backdrop(backdrop)
        Column(Modifier.fillMaxSize()) {
            InfoPanel(featured, Modifier.fillMaxWidth().height(236.dp))
            LazyColumn(
                state = rememberLazyListState(),
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(bottom = 60.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                item(key = "__actions") {
                    Row(
                        Modifier.padding(start = 48.dp, top = 4.dp, bottom = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        TvButton("🔍  Search", { onBrowse(true) })
                        if (!svc.isDiscover) TvButton("All titles A–Z", { onBrowse(false) })
                        TvButton("⟳  Refresh", onRefresh)
                    }
                }
                if (page.rows.isEmpty()) {
                    item(key = "__empty") {
                        Text("Nothing here yet.", color = HColors.TextDim, fontSize = 16.sp,
                            modifier = Modifier.padding(start = 48.dp, top = 24.dp))
                    }
                }
                itemsIndexed(page.rows, key = { i, r -> "row$i:${r.title}" }) { ri, row ->
                    MediaRowView(
                        svc = svc,
                        row = row,
                        rowIndex = ri,
                        onSeeAll = onSeeAll,
                        onOpen = onOpen,
                        onFocusItem = { key, item ->
                            featured = item
                            lastFocus = key
                        },
                        shouldGrab = { key -> grabPending && key == target },
                        onGrabbed = { grabPending = false },
                    )
                }
            }
        }
    }
}

@Composable
fun MediaRowView(
    svc: ServiceInfo,
    row: MediaRow,
    rowIndex: Int,
    onOpen: (String) -> Unit,
    onFocusItem: (String, MediaItem) -> Unit,
    shouldGrab: (String) -> Boolean,
    onGrabbed: () -> Unit,
    onSeeAll: (key: String, title: String) -> Unit = { _, _ -> },
    edgePadding: Dp = 48.dp,
) {
    val gw = LocalGateway.current
    val scope = rememberCoroutineScope()
    // Rows with a key keep loading pages as you scroll right.
    val extra = remember(row) { mutableStateListOf<MediaItem>() }
    var nextPage by remember(row) { mutableIntStateOf(2) }
    var hasMore by remember(row) { mutableStateOf(row.more && row.key != null) }
    var loading by remember(row) { mutableStateOf(false) }
    val items = remember(row, extra.size) {
        val seen = HashSet<String>()
        (row.items + extra).filter { seen.add(it.key) }
    }
    val loadMore: () -> Unit = {
        val key = row.key
        if (key != null && hasMore && !loading) {
            loading = true
            scope.launch {
                try {
                    val pg = gw.rowPage(svc, key, nextPage)
                    extra.addAll(pg.items)
                    hasMore = pg.hasMore && pg.items.isNotEmpty()
                    nextPage++
                } catch (e: Exception) {
                    hasMore = false
                } finally {
                    loading = false
                }
            }
        }
    }

    Column {
        Text(
            row.title,
            color = HColors.Text,
            fontSize = 18.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(start = edgePadding, bottom = 2.dp),
        )
        LazyRow(
            contentPadding = PaddingValues(horizontal = edgePadding, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            itemsIndexed(items, key = { i, it -> "$i:${it.key}" }) { i, item ->
                val fk = "$rowIndex:${item.key}"
                val fr = remember { FocusRequester() }
                if (shouldGrab(fk)) {
                    LaunchedEffect(Unit) {
                        runCatching { fr.requestFocus() }
                        onGrabbed()
                    }
                }
                val focused: () -> Unit = {
                    onFocusItem(fk, item)
                    if (i >= items.size - 6) loadMore()
                }
                if (row.style == "landscape") {
                    LandscapeCard(item = item, onClick = { onOpen(item.id) }, onFocused = focused, focusRequester = fr)
                } else {
                    PosterCard(item = item, onClick = { onOpen(item.id) }, onFocused = focused, focusRequester = fr)
                }
            }
            val key = row.key
            if (key != null) {
                item(key = "__seeall") {
                    SeeAllCard(onClick = { onSeeAll(key, row.title) })
                }
            }
        }
    }
}

@Composable
private fun SeeAllCard(onClick: () -> Unit) {
    FocusBox(onClick = onClick, modifier = Modifier.width(112.dp).aspectRatio(2f / 3f)) { f ->
        Box(
            Modifier
                .fillMaxSize()
                .background(if (f) HColors.SurfaceHi else HColors.Surface),
            contentAlignment = Alignment.Center,
        ) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("›", color = if (f) HColors.Accent else HColors.TextDim, fontSize = 40.sp)
                Text("See all", color = HColors.Text, fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
            }
        }
    }
}
