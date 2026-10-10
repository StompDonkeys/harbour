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
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRestorer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.HomePage
import au.matt.harbour.data.MediaItem
import au.matt.harbour.data.MediaRow
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.home
import kotlinx.coroutines.delay

/** Netflix-style home for one Radarr / Sonarr / Seerr instance. */
@Composable
fun LibraryScreen(svc: ServiceInfo, onOpen: (String) -> Unit, onBrowse: (Boolean) -> Unit) {
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
        p != null -> LibraryContent(svc, p, onOpen, onBrowse, onRefresh = { reload++ })
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
                        row = row,
                        rowIndex = ri,
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

@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun MediaRowView(
    row: MediaRow,
    rowIndex: Int,
    onOpen: (String) -> Unit,
    onFocusItem: (String, MediaItem) -> Unit,
    shouldGrab: (String) -> Boolean,
    onGrabbed: () -> Unit,
) {
    Column {
        Text(
            row.title,
            color = HColors.Text,
            fontSize = 18.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(start = 48.dp, bottom = 2.dp),
        )
        LazyRow(
            modifier = Modifier.focusRestorer(),
            contentPadding = PaddingValues(horizontal = 48.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            itemsIndexed(row.items, key = { i, it -> "$i:${it.key}" }) { _, item ->
                val fk = "$rowIndex:${item.key}"
                val fr = remember { FocusRequester() }
                if (shouldGrab(fk)) {
                    LaunchedEffect(Unit) {
                        runCatching { fr.requestFocus() }
                        onGrabbed()
                    }
                }
                if (row.style == "landscape") {
                    LandscapeCard(
                        item = item,
                        onClick = { onOpen(item.id) },
                        onFocused = { onFocusItem(fk, item) },
                        focusRequester = fr,
                    )
                } else {
                    PosterCard(
                        item = item,
                        onClick = { onOpen(item.id) },
                        onFocused = { onFocusItem(fk, item) },
                        focusRequester = fr,
                    )
                }
            }
        }
    }
}
