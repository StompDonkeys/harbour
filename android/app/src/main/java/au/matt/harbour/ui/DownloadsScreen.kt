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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.DlClient
import au.matt.harbour.data.DlItem
import au.matt.harbour.data.Downloads
import au.matt.harbour.data.downloadAction
import au.matt.harbour.data.downloads
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private data class DlAction(val label: String, val action: String, val style: String? = null, val confirm: String? = null)

/** Live NZBGet + qBittorrent queue, refreshed every 2 seconds while it's on screen. */
@Composable
fun DownloadsScreen() {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<Downloads?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var poke by remember { mutableIntStateOf(0) }
    var menuFor by remember { mutableStateOf<DlItem?>(null) }
    var confirm by remember { mutableStateOf<Pair<DlItem, DlAction>?>(null) }
    var grabPending by remember { mutableStateOf(true) }
    val first = remember { FocusRequester() }

    LaunchedEffect(poke) {
        while (true) {
            try {
                data = gw.downloads()
                error = null
            } catch (e: Exception) {
                error = e.message
            }
            delay(2000)
        }
    }

    fun send(client: String, id: String?, action: String) {
        scope.launch {
            try {
                toaster.show(gw.downloadAction(client, id, action).message)
            } catch (e: Exception) {
                toaster.show("⚠ ${e.message}")
            }
            poke++
        }
    }

    Column(Modifier.fillMaxSize().padding(start = 48.dp, end = 48.dp, top = 30.dp)) {
        Text("Downloads", color = HColors.Text, fontSize = 28.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(12.dp))
        val d = data
        val err = error
        when {
            d == null && err != null -> ErrorPanel(err, onRetry = { poke++ })
            d == null -> Loading()
            else -> {
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    d.clients.forEach { c -> ClientCard(c, onToggle = { send(c.id, null, if (c.paused) "resume" else "pause") }) }
                }
                if (err != null) {
                    Text("⚠ $err (showing last known state)", color = HColors.Warn, fontSize = 13.sp,
                        modifier = Modifier.padding(top = 8.dp))
                }
                Spacer(Modifier.height(14.dp))
                if (d.items.isEmpty()) {
                    Text("Nothing downloading.", color = HColors.TextDim, fontSize = 16.sp)
                }
                LazyColumn(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    contentPadding = PaddingValues(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 30.dp),
                ) {
                    items(d.items, key = { "${it.client}:${it.id}" }) { dl ->
                        val isFirst = dl == d.items.first()
                        DlRow(dl, onClick = { menuFor = dl }, focusRequester = if (isFirst) first else null)
                        if (isFirst && grabPending) {
                            LaunchedEffect(Unit) {
                                runCatching { first.requestFocus() }
                                grabPending = false
                            }
                        }
                    }
                }
            }
        }
    }

    menuFor?.let { item ->
        val acts = buildList {
            add(if (item.paused) DlAction("Resume", "resume", "primary") else DlAction("Pause", "pause"))
            if (item.canTop) add(DlAction("Move to top of queue", "top"))
            add(DlAction("Delete", "delete", "danger", "Remove “${item.name}” from ${item.clientName}?"))
            if (item.canDeleteFiles) {
                add(DlAction("Delete + files", "delete_files", "danger",
                    "Remove “${item.name}” and delete its downloaded files?"))
            }
        }
        MenuDialog(
            title = item.name,
            subtitle = "${item.clientName} · ${item.status} · ${(item.progress * 100).toInt()}% of ${fmtBytes(item.size)}",
            items = acts.map { it.label to it.style },
            onPick = { i ->
                menuFor = null
                val a = acts[i]
                if (a.confirm != null) confirm = item to a else send(item.client, item.id, a.action)
            },
            onDismiss = { menuFor = null },
        )
    }
    confirm?.let { (item, a) ->
        ConfirmDialog(
            title = a.label,
            message = a.confirm ?: "",
            confirmLabel = a.label,
            danger = true,
            onConfirm = {
                confirm = null
                send(item.client, item.id, a.action)
            },
            onDismiss = { confirm = null },
        )
    }
}

@Composable
private fun ClientCard(c: DlClient, onToggle: () -> Unit) {
    Row(
        Modifier
            .clip(RoundedCornerShape(12.dp))
            .background(HColors.Surface)
            .padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column {
            Text(c.name, color = HColors.Text, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            Text(
                when {
                    c.error != null -> "⚠ ${c.error}"
                    c.paused -> "Paused · ${c.count} queued"
                    else -> "↓ ${fmtSpeed(c.speed)} · ${c.count} active" + if (c.seeding > 0) " · ${c.seeding} seeding" else ""
                },
                color = if (c.error != null) HColors.Warn else HColors.TextDim,
                fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        if (c.error == null) {
            TvButton(if (c.paused) "Resume all" else "Pause all", onToggle, style = if (c.paused) "primary" else null)
        }
    }
}

@Composable
private fun DlRow(item: DlItem, onClick: () -> Unit, focusRequester: FocusRequester?) {
    FocusBox(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        focusedScale = 1.01f,
        borderWidth = 2.dp,
        focusRequester = focusRequester,
    ) { f ->
        Column(
            Modifier
                .fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f)),
        ) {
            Row(
                Modifier.padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(item.name, color = if (item.paused) HColors.TextDim else HColors.Text, fontSize = 14.sp,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.height(3.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        Pill(item.status, when {
                            item.paused -> "muted"
                            item.speed > 0 -> "info"
                            item.status.startsWith("Error") || item.status.startsWith("Stalled") -> "warn"
                            else -> null
                        })
                        Text(
                            listOfNotNull(item.clientName, item.category,
                                "${fmtBytes(item.size - item.left)} of ${fmtBytes(item.size)}").joinToString("  ·  "),
                            color = HColors.TextDim, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                Spacer(Modifier.width(16.dp))
                Column(horizontalAlignment = Alignment.End) {
                    Text("${(item.progress * 100).toInt()}%", color = HColors.Text, fontSize = 16.sp,
                        fontWeight = FontWeight.Bold)
                    Text(
                        if (item.speed > 0) "${fmtSpeed(item.speed)} · ${fmtEta(item.eta)}" else "",
                        color = HColors.TextDim, fontSize = 12.sp,
                    )
                }
            }
            Box(Modifier.fillMaxWidth()) { ProgressStrip(item.progress) }
        }
    }
}
