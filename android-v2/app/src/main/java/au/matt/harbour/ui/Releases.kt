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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.Release
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.grab
import au.matt.harbour.data.releases
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Interactive ("manual") search: every release the indexers found, pick one to grab. */
@Composable
fun ReleasesScreen(svc: ServiceInfo, itemId: String, arg: String?, title: String, onDone: () -> Unit) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val cache = LocalCache.current
    val scope = rememberCoroutineScope()
    var items by remember { mutableStateOf<List<Release>?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var picked by remember { mutableStateOf<Release?>(null) }
    var busy by remember { mutableStateOf(false) }
    var elapsed by remember { mutableIntStateOf(0) }
    val first = remember { FocusRequester() }

    LaunchedEffect(reload) {
        items = null
        error = null
        try {
            items = gw.releases(svc, itemId, arg).items
        } catch (e: Exception) {
            error = e.message
        }
    }
    LaunchedEffect(items, reload) {
        elapsed = 0
        while (items == null && error == null) {
            delay(1000)
            elapsed++
        }
    }

    val scopeLabel = when {
        arg?.startsWith("season:") == true -> "Season ${arg.substringAfter(':')}"
        arg?.startsWith("episode:") == true -> "Episode"
        else -> null
    }

    Column(Modifier.fillMaxSize().padding(start = 48.dp, end = 48.dp, top = 30.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f)) {
                Text("Manual search", color = HColors.Text, fontSize = 26.sp, fontWeight = FontWeight.Bold)
                Text(listOfNotNull(title.ifBlank { null }, scopeLabel).joinToString(" · "),
                    color = HColors.TextDim, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            val list = items
            if (list != null) {
                Text("${list.size} releases · ${list.count { it.approved }} acceptable",
                    color = HColors.TextDim, fontSize = 13.sp)
                TvButton("⟳  Search again", { reload++ })
            }
        }
        Spacer(Modifier.height(14.dp))
        Box(Modifier.weight(1f)) {
            val list = items
            val err = error
            when {
                err != null -> ErrorPanel(err, onRetry = { reload++ })
                list == null -> Column(
                    Modifier.fillMaxSize(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    CircularProgressIndicator(color = HColors.Accent)
                    Spacer(Modifier.height(16.dp))
                    Text("Asking your indexers… ${elapsed}s", color = HColors.TextDim, fontSize = 15.sp)
                    Text("This can take up to a minute.", color = HColors.TextDim, fontSize = 13.sp)
                }
                list.isEmpty() -> Text("No releases found.", color = HColors.TextDim, fontSize = 16.sp)
                else -> LazyColumn(
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    contentPadding = PaddingValues(start = 4.dp, end = 4.dp, bottom = 30.dp, top = 4.dp),
                ) {
                    itemsIndexed(list, key = { i, r -> "$i:${r.guid}" }) { i, r ->
                        ReleaseRow(r, onClick = { picked = r }, focusRequester = if (i == 0) first else null)
                        if (i == 0) {
                            LaunchedEffect(list) { runCatching { first.requestFocus() } }
                        }
                    }
                }
            }
        }
    }

    picked?.let { r ->
        val warn = if (r.rejections.isNotEmpty()) "\n\n⚠ Your profile would normally skip this:\n• " +
            r.rejections.joinToString("\n• ") else ""
        ConfirmDialog(
            title = "Download this release?",
            message = r.title + "\n" + listOfNotNull(r.quality, fmtBytes(r.size), r.indexer).joinToString(" · ") + warn,
            confirmLabel = "Download",
            danger = r.rejections.isNotEmpty(),
            onConfirm = {
                picked = null
                busy = true
                scope.launch {
                    try {
                        toaster.show(gw.grab(svc, itemId, r, arg).message)
                        cache.invalidate(svc)
                        busy = false
                        onDone()
                    } catch (e: Exception) {
                        busy = false
                        toaster.show("⚠ ${e.message}")
                    }
                }
            },
            onDismiss = { picked = null },
        )
    }
    if (busy) BusyDialog("Sending to download client…")
}

@Composable
private fun ReleaseRow(r: Release, onClick: () -> Unit, focusRequester: FocusRequester?) {
    FocusBox(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        focusedScale = 1.01f,
        borderWidth = 2.dp,
        focusRequester = focusRequester,
    ) { f ->
        Row(
            Modifier
                .fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    r.title,
                    color = if (r.approved) HColors.Text else HColors.TextDim,
                    fontSize = 14.sp, maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 18.sp,
                )
                Spacer(Modifier.height(4.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    r.quality?.let { Pill(it, if (r.approved) "ok" else "muted") }
                    Text(
                        listOfNotNull(r.indexer, r.protocol, r.peers, r.age, r.score?.let { "score $it" })
                            .joinToString("  ·  "),
                        color = HColors.TextDim, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
                if (r.rejections.isNotEmpty()) {
                    Text("⚠ " + r.rejections.first() + if (r.rejections.size > 1) " (+${r.rejections.size - 1})" else "",
                        color = HColors.Warn, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            Spacer(Modifier.width(14.dp))
            Text(fmtBytes(r.size), color = HColors.Text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}
