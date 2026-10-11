package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.ActionDef
import au.matt.harbour.data.Detail
import au.matt.harbour.data.Episode
import au.matt.harbour.data.Season
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.detail
import au.matt.harbour.data.runAction
import coil.compose.AsyncImage
import kotlinx.coroutines.launch

/** An action waiting on the user: optionally a picked choice, then optionally a confirmation. */
private data class Pending(val action: ActionDef, val choice: String? = null)

@Composable
fun DetailScreen(
    svc: ServiceInfo,
    itemId: String,
    onClose: () -> Unit,
    onOpenReleases: (arg: String?, title: String) -> Unit,
    onOpenItem: (ref: String) -> Unit = {},
) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val cache = LocalCache.current
    val scope = rememberCoroutineScope()
    var detail by remember { mutableStateOf<Detail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf<String?>(null) }
    var choosing by remember { mutableStateOf<ActionDef?>(null) }
    var confirming by remember { mutableStateOf<Pending?>(null) }
    var episodeMenu by remember { mutableStateOf<Episode?>(null) }

    LaunchedEffect(reload) {
        error = null
        try {
            detail = gw.detail(svc, itemId)
        } catch (e: Exception) {
            if (detail == null) error = e.message else toaster.show("⚠ ${e.message}")
        }
    }

    val run: (ActionDef, String?) -> Unit = { a, choice ->
        if (busy == null) {
            busy = a.label
            scope.launch {
                try {
                    val r = gw.runAction(svc, itemId, a, choice)
                    toaster.show(r.message)
                    cache.invalidate(svc)
                    busy = null
                    if (r.close) onClose() else if (r.refresh) reload++
                } catch (e: Exception) {
                    busy = null
                    toaster.show("⚠ ${e.message}")
                }
            }
        }
    }
    val afterChoice: (ActionDef, String?) -> Unit = { a, choice ->
        if (a.confirm != null) confirming = Pending(a, choice) else run(a, choice)
    }
    val trigger: (ActionDef) -> Unit = { a ->
        when {
            a.opens == "releases" -> onOpenReleases(a.arg, detail?.item?.title ?: "")
            a.opens == "item" && a.arg != null -> onOpenItem(a.arg)
            a.choose != null && a.choose.options.size > 1 -> choosing = a
            else -> afterChoice(a, a.choose?.options?.firstOrNull()?.value)
        }
    }

    val d = detail
    val err = error
    when {
        d != null -> DetailContent(d, onAction = trigger, onBack = onClose, onEpisode = { episodeMenu = it })
        err != null -> ErrorPanel(err, onRetry = { reload++ })
        else -> Loading()
    }

    episodeMenu?.let { ep ->
        MenuDialog(
            title = "${ep.label} · ${ep.title}",
            subtitle = ep.subtitle,
            items = ep.actions.map { it.label to it.style },
            onPick = { i ->
                episodeMenu = null
                ep.actions.getOrNull(i)?.let(trigger)
            },
            onDismiss = { episodeMenu = null },
        )
    }
    choosing?.let { a ->
        val opts = a.choose?.options.orEmpty()
        MenuDialog(
            title = a.choose?.title ?: a.label,
            items = opts.map { it.label to null },
            onPick = { i ->
                choosing = null
                afterChoice(a, opts.getOrNull(i)?.value)
            },
            onDismiss = { choosing = null },
        )
    }
    confirming?.let { p ->
        ConfirmDialog(
            title = p.action.label,
            message = p.action.confirm ?: "",
            confirmLabel = p.action.label,
            danger = p.action.style == "danger",
            onConfirm = {
                confirming = null
                run(p.action, p.choice)
            },
            onDismiss = { confirming = null },
        )
    }
    busy?.let { BusyDialog("$it…") }
}

@Composable
private fun DetailContent(
    d: Detail,
    onAction: (ActionDef) -> Unit,
    onBack: () -> Unit,
    onEpisode: (Episode) -> Unit,
) {
    val gw = LocalGateway.current
    val firstButton = remember { FocusRequester() }
    var grabbed by remember { mutableStateOf(false) }
    var seasonIndex by rememberSaveable { mutableIntStateOf(0) }
    val season: Season? = d.seasons.getOrNull(seasonIndex)
    val listState = rememberLazyListState()

    Box(Modifier.fillMaxSize()) {
        Backdrop(gw.img(d.item.backdrop ?: d.item.poster))
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 48.dp, end = 48.dp, top = 32.dp, bottom = 60.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            item(key = "header") {
                // Focusable (but invisible) so pressing Up from the buttons scrolls the title back into view.
                Row(Modifier.focusable()) {
                    val poster = gw.img(d.item.poster)
                    if (poster != null) {
                        AsyncImage(
                            model = poster,
                            contentDescription = d.item.title,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .width(124.dp)
                                .aspectRatio(2f / 3f)
                                .clip(RoundedCornerShape(10.dp)),
                        )
                        Spacer(Modifier.width(26.dp))
                    }
                    Column(Modifier.widthIn(max = 600.dp)) {
                        Text(d.item.title, color = HColors.Text, fontSize = 30.sp, fontWeight = FontWeight.Bold,
                            maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 34.sp)
                        Spacer(Modifier.height(6.dp))
                        Row(verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            d.item.subtitle?.let { Text(it, color = HColors.TextDim, fontSize = 15.sp) }
                            d.item.badge?.let { Pill(it, d.item.badgeTone) }
                        }
                        d.item.overview?.let {
                            Spacer(Modifier.height(10.dp))
                            Text(it, color = HColors.Text.copy(alpha = 0.88f), fontSize = 14.sp,
                                lineHeight = 20.sp, maxLines = 5, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
            item(key = "actions") {
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    if (d.actions.isEmpty()) {
                        TvButton("Back", onBack, focusRequester = firstButton)
                    } else {
                        d.actions.forEachIndexed { i, a ->
                            TvButton(
                                text = a.label,
                                onClick = { onAction(a) },
                                style = a.style,
                                focusRequester = if (i == 0) firstButton else null,
                            )
                        }
                    }
                }
                if (!grabbed) {
                    LaunchedEffect(Unit) {
                        runCatching { firstButton.requestFocus() }
                        listState.scrollToItem(0)
                        grabbed = true
                    }
                }
            }
            if (d.facts.isNotEmpty()) {
                item(key = "facts") { FactsGrid(d) }
            }
            if (d.seasons.isNotEmpty()) {
                item(key = "seasons") {
                    LazyRow(
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        contentPadding = PaddingValues(vertical = 6.dp, horizontal = 2.dp),
                    ) {
                        itemsIndexed(d.seasons, key = { _, s -> "s${s.number}" }) { i, s ->
                            SeasonChip(
                                season = s,
                                selected = i == seasonIndex,
                                onFocused = { seasonIndex = i },
                            )
                        }
                    }
                }
                if (season != null) {
                    item(key = "season-head-${season.number}") {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text(season.label, color = HColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                                season.subtitle?.let { Text(it, color = HColors.TextDim, fontSize = 13.sp) }
                            }
                            if (season.actions.isNotEmpty()) {
                                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    season.actions.forEach { a -> TvButton(a.label, { onAction(a) }, style = a.style) }
                                }
                            }
                        }
                    }
                    items(season.episodes, key = { "ep${it.id}" }) { ep ->
                        EpisodeRow(ep, onClick = { if (ep.actions.isNotEmpty()) onEpisode(ep) })
                    }
                }
            }
        }
    }
}

@Composable
private fun FactsGrid(d: Detail) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        d.facts.chunked(3).forEach { rowFacts ->
            Row(horizontalArrangement = Arrangement.spacedBy(20.dp)) {
                rowFacts.forEach { f ->
                    Column(Modifier.width(240.dp)) {
                        Text(f.label.uppercase(), color = HColors.TextDim, fontSize = 11.sp,
                            fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
                        Text(f.value, color = HColors.Text, fontSize = 14.sp, maxLines = 2,
                            overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
}

@Composable
private fun SeasonChip(season: Season, selected: Boolean, onFocused: () -> Unit) {
    FocusBox(
        onClick = onFocused,
        shape = RoundedCornerShape(50),
        focusedScale = 1.06f,
        borderWidth = 0.dp,
        onFocused = onFocused,
    ) { f ->
        Text(
            season.label,
            color = if (f) Color.Black else HColors.Text,
            fontSize = 15.sp,
            fontWeight = if (selected) FontWeight.Bold else FontWeight.Medium,
            modifier = Modifier
                .background(
                    when {
                        f -> Color.White
                        selected -> HColors.Accent.copy(alpha = 0.45f)
                        else -> Color.White.copy(alpha = 0.10f)
                    },
                )
                .padding(horizontal = 20.dp, vertical = 9.dp),
        )
    }
}

@Composable
private fun EpisodeRow(ep: Episode, onClick: () -> Unit) {
    FocusBox(
        onClick = onClick,
        modifier = Modifier
            .widthIn(max = 860.dp)
            .fillMaxWidth(),
        focusedScale = 1.015f,
        borderWidth = 2.dp,
    ) { f ->
        Row(
            Modifier
                .fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else HColors.Surface.copy(alpha = 0.7f))
                .padding(horizontal = 18.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(ep.label, color = HColors.Accent, fontSize = 15.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.width(56.dp))
            Column(Modifier.weight(1f)) {
                Text(ep.title, color = HColors.Text, fontSize = 15.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                ep.subtitle?.let { Text(it, color = HColors.TextDim, fontSize = 12.sp, maxLines = 1) }
            }
            Text(
                if (f) "Options" else if (ep.hasFile) "✓" else "",
                color = if (f) HColors.TextDim else HColors.Ok,
                fontSize = if (f) 13.sp else 18.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}
