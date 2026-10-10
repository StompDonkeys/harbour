package au.matt.harbour.ui

import androidx.compose.foundation.background
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
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRestorer
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

@Composable
fun DetailScreen(svc: ServiceInfo, itemId: String, onClose: () -> Unit) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val cache = LocalCache.current
    val scope = rememberCoroutineScope()
    var detail by remember { mutableStateOf<Detail?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf<String?>(null) }
    var confirm by remember { mutableStateOf<ActionDef?>(null) }

    LaunchedEffect(reload) {
        error = null
        try {
            detail = gw.detail(svc, itemId)
        } catch (e: Exception) {
            if (detail == null) error = e.message else toaster.show("⚠ ${e.message}")
        }
    }

    val run: (ActionDef) -> Unit = { a ->
        if (busy == null) {
            busy = a.label
            scope.launch {
                try {
                    val r = gw.runAction(svc, itemId, a)
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
    val trigger: (ActionDef) -> Unit = { a -> if (a.confirm != null) confirm = a else run(a) }

    val d = detail
    val err = error
    when {
        d != null -> DetailContent(d, onAction = trigger, onBack = onClose)
        err != null -> ErrorPanel(err, onRetry = { reload++ })
        else -> Loading()
    }

    confirm?.let { a ->
        ConfirmDialog(
            title = a.label,
            message = a.confirm ?: "",
            confirmLabel = a.label,
            danger = a.style == "danger",
            onConfirm = {
                confirm = null
                run(a)
            },
            onDismiss = { confirm = null },
        )
    }
    busy?.let { BusyDialog("$it…") }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun DetailContent(d: Detail, onAction: (ActionDef) -> Unit, onBack: () -> Unit) {
    val gw = LocalGateway.current
    val firstButton = remember { FocusRequester() }
    var grabbed by remember { mutableStateOf(false) }
    var seasonIndex by rememberSaveable { mutableIntStateOf(0) }
    val season: Season? = d.seasons.getOrNull(seasonIndex)

    Box(Modifier.fillMaxSize()) {
        Backdrop(gw.img(d.item.backdrop ?: d.item.poster))
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 48.dp, end = 48.dp, top = 40.dp, bottom = 60.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            item(key = "header") {
                Row {
                    val poster = gw.img(d.item.poster)
                    if (poster != null) {
                        AsyncImage(
                            model = poster,
                            contentDescription = d.item.title,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .width(150.dp)
                                .aspectRatio(2f / 3f)
                                .clip(RoundedCornerShape(10.dp)),
                        )
                        Spacer(Modifier.width(28.dp))
                    }
                    Column(Modifier.widthIn(max = 540.dp)) {
                        Text(d.item.title, color = HColors.Text, fontSize = 32.sp, fontWeight = FontWeight.Bold,
                            maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 36.sp)
                        Spacer(Modifier.height(8.dp))
                        Row(verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            d.item.subtitle?.let { Text(it, color = HColors.TextDim, fontSize = 15.sp) }
                            d.item.badge?.let { Pill(it, d.item.badgeTone) }
                        }
                        d.item.overview?.let {
                            Spacer(Modifier.height(12.dp))
                            Text(it, color = HColors.Text.copy(alpha = 0.88f), fontSize = 14.sp,
                                lineHeight = 20.sp, maxLines = 7, overflow = TextOverflow.Ellipsis)
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
                        modifier = Modifier.focusRestorer(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                        contentPadding = PaddingValues(vertical = 6.dp),
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
                        Row(verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                            Column {
                                Text(season.label, color = HColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                                season.subtitle?.let { Text(it, color = HColors.TextDim, fontSize = 13.sp) }
                            }
                            season.action?.let { a -> TvButton(a.label, { onAction(a) }) }
                        }
                    }
                    items(season.episodes, key = { "ep${it.id}" }) { ep ->
                        EpisodeRow(ep, onClick = { ep.action?.let(onAction) })
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
            Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                rowFacts.forEach { f ->
                    Column(Modifier.width(250.dp)) {
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
                if (ep.hasFile) "✓" else if (f && ep.action != null) "Search" else "",
                color = if (ep.hasFile) HColors.Ok else HColors.TextDim,
                fontSize = if (ep.hasFile) 18.sp else 13.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}
