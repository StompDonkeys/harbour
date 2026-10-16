package au.matt.harbour.ui

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import kotlinx.coroutines.delay
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import au.matt.harbour.data.Person
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
import au.matt.harbour.data.Video
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
    onOpenId: (id: String) -> Unit = {},
    onPlay: (ref: String) -> Unit = {},
    onTrailer: (key: String, title: String) -> Unit = { _, _ -> },
) {
    val context = LocalContext.current
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
    var actionMenu by remember { mutableStateOf<ActionDef?>(null) }

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
            a.opens == "menu" && a.items.isNotEmpty() -> actionMenu = a
            a.opens == "trailer" && a.arg != null -> onTrailer(a.arg, detail?.item?.title ?: "Trailer")
            a.opens == "releases" -> onOpenReleases(a.arg, detail?.item?.title ?: "")
            a.opens == "item" && a.arg != null -> onOpenItem(a.arg)
            a.opens == "youtube" && a.arg != null -> {
                // A plain watch link works with YouTube *and* SmartTube; vnd.youtube: is the fallback.
                if (!openExternal(context, "https://www.youtube.com/watch?v=${a.arg}") &&
                    !openExternal(context, "vnd.youtube:${a.arg}")
                ) toaster.show("Couldn't open a YouTube app — install YouTube or SmartTube")
            }
            a.opens == "play" && a.arg != null -> onPlay(a.arg)
            a.opens == "plex" && a.arg != null && a.arg.contains("|") -> onPlay("plex:" + a.arg.substringAfter("|"))
            a.opens == "plex" && a.arg != null -> {
                val (machine, key) = a.arg.split("|", limit = 2).let { it[0] to it.getOrElse(1) { "" } }
                // Plex has no documented deep links; try the community-reported play link first,
                // then the older server:// form, then just open the app.
                val play = "plex://play/?metadataKey=%2Flibrary%2Fmetadata%2F$key&server=$machine"
                val legacy = "plex://server://$machine/com.plexapp.plugins.library/library/metadata/$key"
                if (!openExternal(context, play, PLEX_PACKAGE) &&
                    !openExternal(context, legacy, PLEX_PACKAGE) &&
                    !launchApp(context, PLEX_PACKAGE)
                ) {
                    toaster.show("Couldn't open Plex — is the Plex app installed?")
                }
            }
            a.choose != null && a.choose.options.size > 1 -> choosing = a
            else -> afterChoice(a, a.choose?.options?.firstOrNull()?.value)
        }
    }

    val d = detail
    val err = error
    when {
        d != null -> DetailContent(svc, d, onAction = trigger, onBack = onClose, onEpisode = { episodeMenu = it },
            onOpenId = onOpenId, onTrailer = { v -> onTrailer(v.key, v.name) })
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
    actionMenu?.let { m ->
        MenuDialog(
            title = m.label,
            subtitle = detail?.item?.title,
            items = m.items.map { it.label to it.style },
            onPick = { i ->
                actionMenu = null
                m.items.getOrNull(i)?.let(trigger)
            },
            onDismiss = { actionMenu = null },
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
    svc: ServiceInfo,
    d: Detail,
    onAction: (ActionDef) -> Unit,
    onBack: () -> Unit,
    onEpisode: (Episode) -> Unit,
    onOpenId: (String) -> Unit,
    onTrailer: (Video) -> Unit,
) {
    val gw = LocalGateway.current
    val rich = d.meta.isNotEmpty()
    val firstButton = remember { FocusRequester() }
    var grabbed by remember { mutableStateOf(false) }
    var seasonIndex by rememberSaveable { mutableIntStateOf(0) }
    val season: Season? = d.seasons.getOrNull(seasonIndex)
    val listState = rememberLazyListState()

    val isPerson = d.item.id.startsWith("person-")
    val panelFacts = if (isPerson) emptyList() else d.facts.take(8)
    val gridFacts = if (isPerson) d.facts else d.facts.drop(8)

    Box(Modifier.fillMaxSize()) {
        Backdrop(gw.hero(d.item.backdrop ?: d.item.poster), dim = if (isPerson) 1.15f else 1f)
        LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 48.dp, end = 40.dp, top = 40.dp, bottom = 60.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            item(key = "header") {
                // Focusable (but invisible) so pressing Up from the buttons scrolls the title back into view.
                Row(Modifier.focusable(), verticalAlignment = Alignment.Top) {
                    if (isPerson) {
                        val photo = gw.img(d.item.poster)
                        if (photo != null) {
                            AsyncImage(model = photo, contentDescription = d.item.title, contentScale = ContentScale.Crop,
                                modifier = Modifier.size(132.dp).clip(CircleShape))
                            Spacer(Modifier.width(28.dp))
                        }
                    }
                    if (rich) {
                        RichHeader(d, Modifier.weight(1f))
                    } else Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        d.item.subtitle?.let {
                            Text(it.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp,
                                color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        Text(d.item.title, fontSize = 40.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.8).sp,
                            maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 43.sp)
                        d.item.badge?.let { Pill(it, d.item.badgeTone) }
                        d.item.overview?.let {
                            Text(it, color = HColors.Text.copy(alpha = 0.86f), fontSize = 14.sp,
                                lineHeight = 21.sp, maxLines = if (isPerson) 6 else 5, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.widthIn(max = 560.dp))
                        }
                    }
                    if (panelFacts.isNotEmpty()) {
                        Spacer(Modifier.width(24.dp))
                        GlassPanel(Modifier.width(290.dp)) {
                            Column(Modifier.padding(horizontal = 18.dp, vertical = 16.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("DETAILS", fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp,
                                    color = HColors.TextDim)
                                panelFacts.forEach { f ->
                                    Row {
                                        Text(f.label, fontSize = 12.sp, color = HColors.TextDim, modifier = Modifier.width(92.dp),
                                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        Text(f.value, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, maxLines = 2,
                                            overflow = TextOverflow.Ellipsis)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            item(key = "actions") {
                LazyRow(
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    contentPadding = PaddingValues(vertical = 6.dp, horizontal = 4.dp),
                ) {
                    if (d.actions.isEmpty()) {
                        item { TvButton("Back", onBack, focusRequester = firstButton) }
                    } else {
                        itemsIndexed(d.actions, key = { i, a -> "$i:${a.id}" }) { i, a ->
                            TvButton(
                                text = a.label.removePrefix("▶").trim() + if (a.opens == "menu") "  ▾" else "",
                                onClick = { onAction(a) },
                                style = a.style,
                                focusRequester = if (i == 0) firstButton else null,
                                icon = when (a.opens) {
                                    "play", "plex" -> Icons.Filled.PlayArrow
                                    "trailer" -> HIcons.Theaters
                                    else -> null
                                },
                            )
                        }
                    }
                }
                if (!grabbed) {
                    LaunchedEffect(Unit) {
                        delay(80)
                        runCatching { firstButton.requestFocus() }
                        listState.scrollToItem(0)
                        grabbed = true
                    }
                }
            }
            if (d.seasonCards.isNotEmpty()) {
                item(key = "seasonCards") {
                    MediaRowView(
                        svc = svc, row = au.matt.harbour.data.MediaRow("Seasons", items = d.seasonCards), rowIndex = 90,
                        onOpen = onOpenId, onFocusItem = { _, _ -> }, shouldGrab = { false }, onGrabbed = {},
                        edgePadding = 2.dp,
                    )
                }
            }
            if (d.videos.isNotEmpty()) {
                item(key = "videos") { VideoRow("Trailers & extras", d.videos, onTrailer) }
            }
            if (gridFacts.isNotEmpty() && !rich) {
                item(key = "facts") { FactsGrid(gridFacts) }
            }
            if (d.people.isNotEmpty()) {
                item(key = "people") {
                    Column {
                        Text("Cast & crew", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        LazyRow(
                            horizontalArrangement = Arrangement.spacedBy(14.dp),
                            contentPadding = PaddingValues(vertical = 10.dp, horizontal = 2.dp),
                        ) {
                            items(d.people, key = { it.id }) { p -> PersonCard(p, onClick = { onOpenId(p.id) }) }
                        }
                    }
                }
            }
            if (d.rows.isNotEmpty()) {
                itemsIndexed(d.rows, key = { i, r -> "xrow$i:${r.title}" }) { i, r ->
                    MediaRowView(
                        svc = svc, row = r, rowIndex = 100 + i, onOpen = onOpenId,
                        onFocusItem = { _, _ -> }, shouldGrab = { false }, onGrabbed = {},
                        edgePadding = 2.dp,
                    )
                }
            }
            if (gridFacts.isNotEmpty() && rich) {
                item(key = "facts") {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("More details", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        FactsGrid(gridFacts)
                    }
                }
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
private fun PersonCard(p: Person, onClick: () -> Unit) {
    val gw = LocalGateway.current
    FocusBox(
        onClick = onClick,
        modifier = Modifier.width(104.dp),
        shape = RoundedCornerShape(14.dp),
        focusedScale = 1.06f,
        borderWidth = 0.dp,
    ) { f ->
        Column(
            Modifier
                .fillMaxWidth()
                .background(if (f) HColors.SurfaceHi else Color.Transparent)
                .padding(vertical = 8.dp, horizontal = 4.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(
                Modifier
                    .size(84.dp)
                    .clip(CircleShape)
                    .background(HColors.Surface)
                    .border(if (f) 3.dp else 0.dp, if (f) Color.White else Color.Transparent, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                val photo = gw.img(p.photo)
                if (photo != null) {
                    AsyncImage(model = photo, contentDescription = p.name, contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize())
                } else {
                    Text(p.name.take(1), color = HColors.TextDim, fontSize = 30.sp, fontWeight = FontWeight.Bold)
                }
            }
            Spacer(Modifier.height(6.dp))
            Text(p.name, color = HColors.Text, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, maxLines = 2,
                overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center)
            p.role?.let {
                Text(it, color = HColors.TextDim, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    textAlign = TextAlign.Center)
            }
        }
    }
}

private const val PLEX_PACKAGE = "com.plexapp.android"

/** Opens a link in whichever app handles it; false if nothing does. */
private fun openExternal(context: Context, uri: String, pkg: String? = null): Boolean = try {
    context.startActivity(
        Intent(Intent.ACTION_VIEW, Uri.parse(uri)).apply {
            if (pkg != null) setPackage(pkg)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        },
    )
    true
} catch (e: ActivityNotFoundException) {
    false
} catch (e: SecurityException) {
    false
}

private fun launchApp(context: Context, pkg: String): Boolean {
    val intent = context.packageManager.getLaunchIntentForPackage(pkg)
        ?: context.packageManager.getLeanbackLaunchIntentForPackage(pkg)
        ?: return false
    context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    return true
}

@Composable
private fun FactsGrid(list: List<au.matt.harbour.data.Fact>) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        list.chunked(3).forEach { rowFacts ->
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
                        selected -> HColors.GlassStrong
                        else -> HColors.GlassFill
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
                .background(if (f) HColors.GlassStrong else HColors.GlassFill)
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


/** Harbour v2 title header: logo or title, the meta line, ratings, status, tagline, story, where to stream. */
@Composable
private fun RichHeader(d: Detail, modifier: Modifier) {
    val gw = LocalGateway.current
    Column(modifier, verticalArrangement = Arrangement.spacedBy(9.dp)) {
        val logo = gw.img(d.logo)
        if (logo != null) {
            AsyncImage(model = logo, contentDescription = d.item.title, contentScale = ContentScale.Fit,
                alignment = Alignment.CenterStart,
                modifier = Modifier.height(86.dp).widthIn(max = 420.dp))
        } else {
            Text(d.item.title, fontSize = 40.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.8).sp,
                maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 43.sp)
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(d.meta.joinToString("  ·  "), fontSize = 13.sp, color = HColors.Text.copy(alpha = 0.86f),
                fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false))
            d.ratings.take(3).forEach { r ->
                Text(
                    buildString {
                        append(when (r.label) { "Rotten Tomatoes" -> "RT"; "Audience" -> "Audience"; else -> r.label })
                        append("  ")
                        append(r.value)
                    },
                    fontSize = 11.sp, fontWeight = FontWeight.Bold, maxLines = 1,
                    color = if (r.label == "IMDb") Color(0xFF0A0A0B) else HColors.Text,
                    modifier = Modifier
                        .clip(RoundedCornerShape(5.dp))
                        .background(if (r.label == "IMDb") Color(0xFFF5C518) else Color.White.copy(alpha = 0.14f))
                        .padding(horizontal = 6.dp, vertical = 2.dp),
                )
            }
        }
        if (d.status.isNotEmpty()) {
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                d.status.take(4).forEach { c -> StatusChip(c) }
            }
        }
        d.tagline?.let {
            Text(it, fontSize = 15.sp, fontStyle = androidx.compose.ui.text.font.FontStyle.Italic,
                color = HColors.Text, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        d.item.overview?.let {
            Text(it, color = HColors.Text.copy(alpha = 0.82f), fontSize = 14.sp, lineHeight = 20.sp,
                maxLines = 4, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 600.dp))
        }
        if (d.providers.isNotEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("STREAMING ON", fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp,
                    color = HColors.TextDim)
                Spacer(Modifier.width(2.dp))
                d.providers.forEach { p ->
                    val img = gw.img(p.logo)
                    if (img != null) {
                        AsyncImage(model = img, contentDescription = p.name, contentScale = ContentScale.Crop,
                            modifier = Modifier.size(24.dp).clip(RoundedCornerShape(6.dp)))
                    } else {
                        Text(p.name, fontSize = 12.sp, color = HColors.Text)
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusChip(c: au.matt.harbour.data.Chip) {
    val col = toneColor(c.tone)
    val muted = c.tone == null || c.tone == "muted"
    Row(
        Modifier
            .clip(RoundedCornerShape(7.dp))
            .background(if (muted) Color.White.copy(alpha = 0.10f) else col.copy(alpha = 0.22f))
            .border(1.dp, if (muted) HColors.GlassEdge else col.copy(alpha = 0.55f), RoundedCornerShape(7.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (!muted) {
            Box(Modifier.size(6.dp).clip(CircleShape).background(col))
            Spacer(Modifier.width(6.dp))
        }
        Text(c.text, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, color = HColors.Text, maxLines = 1)
    }
}

/** Trailers, teasers and featurettes; OK plays them right here. */
@Composable
fun VideoRow(title: String, videos: List<Video>, onPlay: (Video) -> Unit) {
    val gw = LocalGateway.current
    Column {
        Text(title, fontSize = 17.sp, fontWeight = FontWeight.Bold)
        LazyRow(
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            contentPadding = PaddingValues(vertical = 12.dp, horizontal = 2.dp),
        ) {
            items(videos, key = { it.key }) { v ->
                Column(Modifier.width(220.dp)) {
                    FocusBox(onClick = { onPlay(v) }, modifier = Modifier.width(220.dp).aspectRatio(16f / 9f),
                        shape = RoundedCornerShape(14.dp), focusedScale = 1.07f) { f ->
                        Box(Modifier.fillMaxSize().background(HColors.Surface)) {
                            AsyncImage(model = gw.img(v.thumb), contentDescription = v.name, contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize())
                            Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = if (f) 0.05f else 0.25f)))
                            Box(
                                Modifier.align(Alignment.Center).size(44.dp).clip(CircleShape)
                                    .background(if (f) Color.White else Color.Black.copy(alpha = 0.5f)),
                                contentAlignment = Alignment.Center,
                            ) {
                                androidx.compose.material3.Icon(Icons.Filled.PlayArrow, contentDescription = null,
                                    tint = if (f) Color.Black else Color.White, modifier = Modifier.size(26.dp))
                            }
                            Sheen(f)
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Text(v.name, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                    v.type?.let { Text(it, fontSize = 11.sp, color = HColors.TextDim, maxLines = 1) }
                }
            }
        }
    }
}
