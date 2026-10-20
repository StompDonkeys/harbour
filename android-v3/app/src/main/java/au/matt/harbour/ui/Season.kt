package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.ActionDef
import au.matt.harbour.data.Discussion
import au.matt.harbour.data.discussion
import au.matt.harbour.data.EpisodeInfo
import au.matt.harbour.data.SeasonPage
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.runAction
import au.matt.harbour.data.season
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * One season of a show, episode by episode: stills, stories, what you have, what you've watched.
 * OK on an episode opens its actions (play, search, monitor, delete, discussion); the panel on the
 * right is just for reading — the still, the story, ratings and what people are saying on Trakt.
 */
@Composable
fun SeasonScreen(
    svc: ServiceInfo,
    showId: String,
    number: Int,
    focusEpisode: Int?,
    onOpenReleases: (arg: String?, title: String) -> Unit,
    onPlay: (String) -> Unit,
    onTrailer: (String, String) -> Unit,
    onOpenShow: () -> Unit,
) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    var current by rememberSaveable { mutableIntStateOf(number) }
    val pages = remember { mutableStateMapOf<Int, SeasonPage>() }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(current, reload) {
        error = null
        try {
            pages[current] = gw.season(svc, showId, current)
        } catch (e: Exception) {
            if (pages[current] == null) error = e.message else toaster.show("⚠ ${e.message}")
        }
    }

    val page = pages[current]
    val trigger = rememberActionRunner(
        svc = svc,
        itemId = showId,
        title = { page?.show?.title ?: "" },
        onOpenReleases = onOpenReleases,
        onPlay = onPlay,
        onTrailer = onTrailer,
        onChanged = { reload++ },
    )
    val err = error
    when {
        page != null -> SeasonContent(
            page = page,
            focusEpisode = if (current == number) focusEpisode else null,
            onSeason = { current = it },
            onAction = trigger,
            onTrailer = onTrailer,
            onOpenShow = onOpenShow,
        )
        err != null -> ErrorPanel(err, onRetry = { reload++ })
        else -> Loading()
    }
}

@Composable
private fun SeasonContent(
    page: SeasonPage,
    focusEpisode: Int?,
    onSeason: (Int) -> Unit,
    onAction: (ActionDef) -> Unit,
    onTrailer: (String, String) -> Unit,
    onOpenShow: () -> Unit,
) {
    val gw = LocalGateway.current
    val eps = page.episodes
    val startIdx = remember(page.season.number) {
        eps.indexOfFirst { it.number == focusEpisode }.takeIf { it >= 0 }
            ?: eps.indexOfFirst { !it.watched && it.status != "upcoming" }.takeIf { it >= 0 } ?: 0
    }
    var sel by remember(page.season.number) { mutableIntStateOf(startIdx) }
    val ep = eps.getOrNull(sel)
    val epFr = remember(page.season.number, eps.size) { List(eps.size) { FocusRequester() } }
    var menuFor by remember { mutableStateOf<EpisodeInfo?>(null) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var grabbed by remember(page.season.number) { mutableStateOf(false) }

    LaunchedEffect(page.season.number) {
        if (eps.isNotEmpty() && !grabbed) {
            listState.scrollToItem((startIdx - 1).coerceAtLeast(0))
            delay(90)
            runCatching { epFr[startIdx].requestFocus() }
            grabbed = true
        }
    }

    Box(Modifier.fillMaxSize()) {
        Backdrop(gw.hero(page.show.backdrop), dim = 1.4f)
        Column(Modifier.fillMaxSize().padding(start = 48.dp, end = 40.dp, top = 28.dp, bottom = 18.dp)) {
            // ---- header
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(page.show.title.uppercase(), fontSize = 12.sp, fontWeight = FontWeight.Bold,
                        letterSpacing = 1.4.sp, color = HColors.TextDim, maxLines = 1)
                    Text(page.season.label, fontSize = 30.sp, fontWeight = FontWeight.ExtraBold,
                        letterSpacing = (-0.6).sp, maxLines = 1)
                    page.season.meta?.let { Text(it, fontSize = 13.sp, color = HColors.TextDim) }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    page.videos.firstOrNull()?.let { v ->
                        TvButton("Trailer", { onTrailer(v.key, v.name) }, icon = HIcons.Theaters)
                    }
                    page.actions.forEach { a -> TvButton(a.label, { onAction(a) }, style = a.style) }
                    TvButton("Show", onOpenShow)
                }
            }
            Spacer(Modifier.height(10.dp))
            if (page.seasons.size > 1) {
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp),
                    contentPadding = PaddingValues(vertical = 4.dp, horizontal = 2.dp)) {
                    itemsIndexed(page.seasons, key = { _, s -> s.number }) { _, s ->
                        val on = s.number == page.season.number
                        FocusBox(onClick = { onSeason(s.number) }, shape = RoundedCornerShape(50),
                            focusedScale = 1.06f, borderWidth = 0.dp) { f ->
                            Text(
                                if (s.number == 0) "Specials" else "S${s.number}",
                                fontSize = 14.sp,
                                fontWeight = if (on || f) FontWeight.Bold else FontWeight.Medium,
                                color = if (f) Color(0xFF0A0A0B) else HColors.Text,
                                modifier = Modifier
                                    .background(when {
                                        f -> HColors.Text
                                        on -> HColors.GlassStrong
                                        else -> HColors.GlassFill
                                    })
                                    .padding(horizontal = 16.dp, vertical = 7.dp),
                            )
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))
            }
            // ---- episodes + the focused episode
            Row(Modifier.fillMaxSize(), horizontalArrangement = Arrangement.spacedBy(18.dp)) {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.width(420.dp).fillMaxHeight().fadeTop(10.dp),
                    contentPadding = PaddingValues(top = 6.dp, bottom = 120.dp, start = 4.dp, end = 4.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (eps.isEmpty()) {
                        item { Text("No episodes listed yet.", color = HColors.TextDim, fontSize = 14.sp) }
                    }
                    itemsIndexed(eps, key = { _, e -> e.key }) { i, e ->
                        EpisodeTile(
                            e = e,
                            selected = i == sel,
                            focusRequester = epFr[i],
                            onFocused = { sel = i },
                            onClick = { if (e.actions.isNotEmpty()) menuFor = e },
                        )
                    }
                }
                if (ep != null) {
                    EpisodePanel(ep = ep, backTo = epFr.getOrNull(sel), onAction = onAction,
                        modifier = Modifier.weight(1f).fillMaxHeight())
                }
            }
        }
    }
    menuFor?.let { e ->
        MenuDialog(
            title = "${e.label} · ${e.title}",
            subtitle = listOfNotNull(e.statusLabel.ifBlank { null }, e.airDate).joinToString("  ·  ").ifBlank { null },
            items = e.actions.map { (if (it.opens == "play") "▶  " else "") + it.label to it.style },
            onPick = { i -> menuFor = null; e.actions.getOrNull(i)?.let(onAction) },
            onDismiss = { menuFor = null },
        )
    }
}

@Composable
private fun EpisodeTile(
    e: EpisodeInfo,
    selected: Boolean,
    focusRequester: FocusRequester,
    onFocused: () -> Unit,
    onClick: () -> Unit,
) {
    val gw = LocalGateway.current
    FocusBox(onClick = onClick, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp),
        focusedScale = 1.03f, borderWidth = 0.dp, onFocused = onFocused, focusRequester = focusRequester) { f ->
        Row(
            Modifier
                .fillMaxWidth()
                .background(when {
                    f -> HColors.Text
                    selected -> Color.White.copy(alpha = 0.12f)
                    else -> Color(0x8C141418)
                })
                .padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.width(112.dp).aspectRatio(16f / 9f).clip(RoundedCornerShape(9.dp)).background(HColors.Surface)) {
                AsyncImage(model = gw.img(e.still), contentDescription = null, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize())
                if (e.watched) {
                    Box(Modifier.align(Alignment.TopEnd).padding(4.dp).size(18.dp).clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.6f)), contentAlignment = Alignment.Center) {
                        Icon(Icons.Filled.Check, contentDescription = "Watched", tint = Color.White,
                            modifier = Modifier.size(13.dp))
                    }
                }
                e.progress?.let { ProgressStrip(it, Modifier.align(Alignment.BottomCenter)) }
            }
            Spacer(Modifier.width(12.dp))
            val fg = if (f) Color(0xFF0A0A0B) else HColors.Text
            val dim = if (f) fg.copy(alpha = 0.65f) else HColors.TextDim
            Column(Modifier.weight(1f)) {
                Text("${e.label} · ${e.title}", fontSize = 14.sp, fontWeight = FontWeight.Bold, color = fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(listOfNotNull(e.airDate, e.runtime).joinToString("  ·  "), fontSize = 11.sp, color = dim,
                    maxLines = 1)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(7.dp).clip(CircleShape).background(epColor(e.status)))
                    Spacer(Modifier.width(6.dp))
                    Text(e.statusLabel, fontSize = 11.sp, color = dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

private fun epColor(status: String) = if (status == "none") HColors.Muted else calStatusColor(status)

@Composable
private fun EpisodePanel(ep: EpisodeInfo, backTo: FocusRequester?, onAction: (ActionDef) -> Unit, modifier: Modifier) {
    val gw = LocalGateway.current
    // The episode's Trakt discussion (if the gateway offers one): score + the top comments.
    val discuss = ep.actions.firstOrNull { it.opens == "discuss" }
    val ref = discuss?.arg
    var talk by remember(ref) { mutableStateOf<Discussion?>(TalkCache[ref]) }
    var talkErr by remember(ref) { mutableStateOf(false) }
    val revealed = remember(ref) { mutableStateMapOf<Long, Boolean>() }
    LaunchedEffect(ref) {
        if (ref == null || TalkCache.containsKey(ref)) return@LaunchedEffect
        delay(450)   // don't fetch for every episode the focus passes on the way down
        runCatching { gw.discussion(ref, "likes", 1) }
            .onSuccess { TalkCache[ref] = it; talk = it }
            .onFailure { talkErr = true }
    }
    // Right from an episode comes in here; Left goes back to that episode.
    val back = Modifier.focusProperties { if (backTo != null) left = backTo }
    val panelList = rememberLazyListState()
    LaunchedEffect(ep.key) { panelList.scrollToItem(0) }

    GlassPanel(modifier, shape = RoundedCornerShape(22.dp)) {
        ScrollRules(auto = true) {
            LazyColumn(
                state = panelList,
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                item(key = "still") {
                    Box(Modifier.fillMaxWidth().height(170.dp).clip(RoundedCornerShape(14.dp)).background(HColors.Surface)) {
                        AsyncImage(model = gw.hero(ep.still), contentDescription = null, contentScale = ContentScale.Crop,
                            modifier = Modifier.fillMaxSize())
                    }
                }
                item(key = "head") {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("${ep.label} · ${ep.title}", fontSize = 18.sp, fontWeight = FontWeight.ExtraBold, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(Modifier.size(8.dp).clip(CircleShape).background(epColor(ep.status)))
                            Spacer(Modifier.width(7.dp))
                            Text(
                                listOfNotNull(ep.statusLabel.ifBlank { null }, ep.airDate, ep.runtime,
                                    if (ep.watched) "Watched" else null).joinToString("  ·  "),
                                fontSize = 12.sp, color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
                // The story: focusable so you can move here and read all of it.
                item(key = "story") {
                    FocusBox(onClick = {}, modifier = back.fillMaxWidth(), focusedScale = 1f, borderWidth = 0.dp,
                        shape = RoundedCornerShape(10.dp)) { f ->
                        Text(ep.overview ?: "No synopsis yet.", fontSize = 14.sp, lineHeight = 20.sp,
                            color = HColors.Text.copy(alpha = if (f) 1f else 0.85f),
                            maxLines = if (f) 40 else 3, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.fillMaxWidth().background(if (f) HColors.GlassFill else Color.Transparent)
                                .padding(horizontal = 6.dp, vertical = 4.dp))
                    }
                }
                val t = talk
                if (ep.rating != null || t?.rating != null) {
                    item(key = "scores") {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                            ep.rating?.let { ScoreChip("TMDB", "★ $it") }
                            t?.rating?.let { ScoreChip("Trakt", "$it%" + if (t.votes > 0) "  · ${t.votes} votes" else "") }
                        }
                    }
                }
                if (ref != null) {
                    item(key = "talk-head") {
                        Text(
                            when {
                                t == null && !talkErr -> "COMMENTS  ·  LOADING…"
                                t == null -> "COMMENTS — COULDN'T LOAD"
                                t.total == 0 -> "NO COMMENTS YET"
                                else -> "TOP COMMENTS  ·  ${t.total}"
                            },
                            fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp, color = HColors.TextDim,
                            modifier = Modifier.padding(top = 6.dp),
                        )
                    }
                    itemsIndexed(t?.comments.orEmpty().take(10), key = { _, c -> "c${c.id}" }) { _, c ->
                        val open = !c.spoiler || revealed[c.id] == true
                        FocusBox(
                            onClick = { if (!open) revealed[c.id] = true else discuss?.let(onAction) },
                            modifier = back.fillMaxWidth(), focusedScale = 1.01f, borderWidth = 0.dp,
                            shape = RoundedCornerShape(10.dp),
                        ) { f ->
                            Column(Modifier.fillMaxWidth().background(if (f) HColors.GlassStrong else HColors.GlassFill)
                                .padding(horizontal = 10.dp, vertical = 7.dp)) {
                                Text(
                                    c.user + (c.rating?.let { "  ★ $it/10" } ?: "") + "   ♥ ${c.likes}" +
                                        if (c.replies > 0) "   ${c.replies} repl${if (c.replies == 1) "y" else "ies"}" else "",
                                    fontSize = 11.sp, fontWeight = FontWeight.Bold, color = HColors.TextDim, maxLines = 1,
                                )
                                Text(if (open) c.text else "⚠ Spoiler — press OK to read", fontSize = 13.sp,
                                    lineHeight = 18.sp, maxLines = if (f) 20 else 3, overflow = TextOverflow.Ellipsis,
                                    color = if (open) HColors.Text else HColors.TextDim)
                            }
                        }
                    }
                    if (t != null && discuss != null) {
                        item(key = "all") {
                            TvButton(if (t.total > 0) "All ${t.total} comments" else "Open discussion",
                                { onAction(discuss) }, modifier = back)
                        }
                    }
                }
                item(key = "hint") {
                    Text("OK on an episode: play, search, monitor and more", fontSize = 11.sp, color = HColors.TextFaint,
                        modifier = Modifier.padding(top = 4.dp))
                }
            }
        }
    }
}

@Composable
private fun ScoreChip(label: String, value: String) {
    Row(
        Modifier.clip(RoundedCornerShape(8.dp)).background(HColors.GlassFill).padding(horizontal = 9.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.8.sp,
            color = HColors.TextDim)
        Spacer(Modifier.width(6.dp))
        Text(value, fontSize = 13.sp, fontWeight = FontWeight.Bold)
    }
}

/** Trakt discussions already fetched this session, so moving up and down the list is instant. */
private val TalkCache = mutableMapOf<String?, Discussion>()

/**
 * Runs a page's actions the same way everywhere: menus, choices, confirmations, the busy
 * spinner, and screens some actions open (manual search, the player, trailers).
 */
@Composable
fun rememberActionRunner(
    svc: ServiceInfo,
    itemId: String,
    title: () -> String,
    onOpenReleases: (arg: String?, title: String) -> Unit,
    onPlay: (String) -> Unit,
    onTrailer: (String, String) -> Unit,
    onChanged: () -> Unit,
): (ActionDef) -> Unit {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val cache = LocalCache.current
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf<String?>(null) }
    var menu by remember { mutableStateOf<ActionDef?>(null) }
    var choosing by remember { mutableStateOf<ActionDef?>(null) }
    var confirming by remember { mutableStateOf<Pair<ActionDef, String?>?>(null) }

    val run: (ActionDef, String?) -> Unit = { a, choice ->
        if (busy == null) {
            busy = a.label
            scope.launch {
                try {
                    val r = gw.runAction(svc, itemId, a, choice)
                    toaster.show(r.message)
                    cache.invalidate(svc)
                    busy = null
                    onChanged()
                } catch (e: Exception) {
                    busy = null
                    toaster.show("⚠ ${e.message}")
                }
            }
        }
    }
    val afterChoice: (ActionDef, String?) -> Unit = { a, c -> if (a.confirm != null) confirming = a to c else run(a, c) }
    val openDiscussion = LocalOpenDiscussion.current
    lateinit var trigger: (ActionDef) -> Unit
    trigger = { a ->
        when {
            a.opens == "menu" && a.items.isNotEmpty() -> menu = a
            a.opens == "trailer" && a.arg != null -> onTrailer(a.arg, title())
            a.opens == "releases" -> onOpenReleases(a.arg, title())
            a.opens == "play" && a.arg != null -> onPlay(a.arg)
            a.opens == "discuss" && a.arg != null -> openDiscussion(a.arg, title())
            a.choose != null && a.choose.options.size > 1 -> choosing = a
            else -> afterChoice(a, a.choose?.options?.firstOrNull()?.value)
        }
    }

    menu?.let { m ->
        MenuDialog(title = m.label, items = m.items.map { it.label to it.style },
            onPick = { i -> menu = null; m.items.getOrNull(i)?.let(trigger) }, onDismiss = { menu = null })
    }
    choosing?.let { a ->
        val opts = a.choose?.options.orEmpty()
        MenuDialog(title = a.choose?.title ?: a.label, items = opts.map { it.label to null },
            onPick = { i -> choosing = null; afterChoice(a, opts.getOrNull(i)?.value) }, onDismiss = { choosing = null })
    }
    confirming?.let { (a, c) ->
        ConfirmDialog(title = a.label, message = a.confirm ?: "", confirmLabel = a.label, danger = a.style == "danger",
            onConfirm = { confirming = null; run(a, c) }, onDismiss = { confirming = null })
    }
    busy?.let { BusyDialog("$it…") }
    return trigger
}
