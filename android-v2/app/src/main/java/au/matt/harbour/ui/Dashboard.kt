package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.CalEntry
import au.matt.harbour.data.Dash
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.Stat
import au.matt.harbour.data.WeekDay
import au.matt.harbour.data.dash
import coil.compose.AsyncImage
import kotlinx.coroutines.delay

/** Kept between visits so Home shows instantly, then refreshes in the background. */
private object DashCache {
    var last: Dash? = null
}

/**
 * Home: what's out today and this week first (the "what can I watch tonight?" question),
 * your library at a glance, then rows of things that just landed or are about to.
 */
@Composable
fun DashboardScreen(hub: ServiceInfo, onOpen: (String) -> Unit, onCalendarDay: (String) -> Unit) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    var data by remember { mutableStateOf(DashCache.last) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload) {
        error = null
        try {
            val d = gw.dash()
            DashCache.last = d
            data = d
        } catch (e: Exception) {
            if (data == null) error = e.message else toaster.show("⚠ ${e.message}")
        }
    }

    val d = data
    val err = error
    when {
        d != null -> DashboardContent(hub, d, onOpen, onCalendarDay)
        err != null -> ErrorPanel(err, onRetry = { reload++ })
        else -> Loading()
    }
}

@Composable
private fun DashboardContent(hub: ServiceInfo, d: Dash, onOpen: (String) -> Unit, onCalendarDay: (String) -> Unit) {
    val gw = LocalGateway.current
    var artUrl by remember { mutableStateOf(d.hero) }
    var art by remember { mutableStateOf(gw.hero(d.hero)) }
    LaunchedEffect(artUrl) {
        delay(250)
        if (artUrl != null) art = gw.hero(artUrl)
    }
    val listState = rememberLazyListState()
    var focusRow by remember { mutableIntStateOf(-2) }   // -1 = the Today / week panels
    LaunchedEffect(focusRow) {
        when {
            focusRow == -1 -> listState.animateScrollToItem(0)
            focusRow >= 0 -> listState.animateScrollToItem(focusRow + 2)
        }
    }
    val firstFocus = remember { FocusRequester() }
    var grabbed by androidx.compose.runtime.saveable.rememberSaveable { mutableStateOf(false) }

    Box(Modifier.fillMaxSize()) {
        Backdrop(art, dim = 1.3f)
        ScrollRules(NoAutoScroll) { LazyColumn(
            state = listState,
            modifier = Modifier.fillMaxSize().fadeTop(16.dp),
            contentPadding = PaddingValues(top = 26.dp, bottom = 220.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            item(key = "head") {
                Column(Modifier.padding(start = 40.dp, end = 36.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                    Row(verticalAlignment = Alignment.Bottom) {
                        Column(Modifier.weight(1f)) {
                            Text(d.greeting.uppercase(), color = HColors.TextDim, fontSize = 12.sp,
                                fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp)
                            Text(d.date, fontSize = 32.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.6).sp)
                        }
                        d.alerts.take(2).forEach { a ->
                            Spacer(Modifier.width(8.dp))
                            val tone = if (a.tone == "danger") HColors.Danger else HColors.Warn
                            Text("⚠  ${a.text}", fontSize = 11.sp, color = HColors.Text, maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(tone.copy(alpha = 0.18f))
                                    .border(1.dp, tone.copy(alpha = 0.6f), RoundedCornerShape(8.dp))
                                    .padding(horizontal = 10.dp, vertical = 5.dp)
                                    .width(230.dp))
                        }
                    }
                    if (d.stats.isNotEmpty()) {
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            d.stats.forEach { StatTile(it, Modifier.weight(1f)) }
                        }
                    }
                }
            }
            item(key = "today") {
                WeekAgenda(
                    week = d.week,
                    firstFocus = firstFocus,
                    onOpen = { e -> onOpen(e.openId ?: e.open ?: "${e.svc}/${e.id}") },
                    onFocusedEntry = { e -> e.backdrop?.let { artUrl = it } },
                    onDay = onCalendarDay,
                    grab = !grabbed,
                    onGrabbed = { grabbed = true },
                    modifier = Modifier
                        .padding(start = 40.dp, end = 36.dp)
                        .onFocusChanged { if (it.hasFocus) focusRow = -1 },
                )
            }
            itemsIndexed(d.rows, key = { i, r -> "row$i:${r.title}" }) { ri, row ->
                MediaRowView(
                    svc = hub,
                    row = row,
                    rowIndex = ri,
                    onOpen = onOpen,
                    onFocusItem = { _, item ->
                        focusRow = ri
                        (item.backdrop ?: item.poster)?.let { artUrl = it }
                    },
                    shouldGrab = { false },
                    onGrabbed = {},
                    edgePadding = 40.dp,
                )
            }
        } }
    }
}

@Composable
private fun StatTile(s: Stat, modifier: Modifier) {
    GlassPanel(modifier, shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
            Text(s.label.uppercase(), fontSize = 10.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp,
                color = HColors.TextDim, maxLines = 1)
            Text(s.value, fontSize = 22.sp, fontWeight = FontWeight.ExtraBold, maxLines = 1,
                color = if (s.tone == "warn") HColors.Warn else HColors.Text)
            s.sub?.let {
                Text(it, fontSize = 11.sp, color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/**
 * The week at a glance: seven day tabs along the top (moving onto one shows that day), and the
 * day's episodes and movies underneath — today's first. OK on a day opens the full calendar.
 */
@Composable
private fun WeekAgenda(
    week: List<WeekDay>,
    firstFocus: FocusRequester,
    onOpen: (CalEntry) -> Unit,
    onFocusedEntry: (CalEntry) -> Unit,
    onDay: (String) -> Unit,
    grab: Boolean,
    onGrabbed: () -> Unit,
    modifier: Modifier,
) {
    var sel by remember { mutableIntStateOf(0) }
    val day = week.getOrNull(sel)
    val entries = day?.entries.orEmpty()
    val chipFr = remember(week.size) { List(week.size) { FocusRequester() } }
    GlassPanel(modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 4.dp, end = 4.dp)) {
                Text(
                    if (sel == 0) "OUT TODAY" else (day?.let { "${it.dow.uppercase()} ${it.day}" } ?: ""),
                    fontSize = 12.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp, color = HColors.TextDim,
                    modifier = Modifier.weight(1f),
                )
                val ready = entries.count { it.status == "downloaded" }
                val tv = entries.count { it.kind == "tv" }
                val mv = entries.count { it.kind == "movie" }
                Text(
                    listOfNotNull(
                        if (ready > 0) "$ready ready to watch" else null,
                        if (tv > 0) "$tv episode${if (tv != 1) "s" else ""}" else null,
                        if (mv > 0) "$mv movie${if (mv != 1) "s" else ""}" else null,
                    ).joinToString("  ·  "),
                    fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                    color = if (ready > 0) HColors.Ok else HColors.TextDim,
                )
            }
            Spacer(Modifier.height(10.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                week.forEachIndexed { i, wd ->
                    FocusBox(
                        onClick = { onDay(wd.date) },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(12.dp),
                        focusedScale = 1.05f,
                        borderWidth = 0.dp,
                        onFocused = { sel = i },
                        focusRequester = chipFr[i],
                    ) { f ->
                        val on = i == sel
                        Column(
                            Modifier
                                .fillMaxWidth()
                                .height(58.dp)
                                .background(when {
                                    f -> HColors.Text
                                    on -> Color.White.copy(alpha = 0.16f)
                                    else -> Color.White.copy(alpha = 0.05f)
                                }),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center,
                        ) {
                            val fg = if (f) Color(0xFF0A0A0B) else HColors.Text
                            Row(verticalAlignment = Alignment.Bottom) {
                                Text(if (i == 0) "Today" else wd.dow, fontSize = 11.sp, fontWeight = FontWeight.Bold,
                                    color = if (f) fg else HColors.TextDim)
                                Spacer(Modifier.width(5.dp))
                                Text("${wd.day}", fontSize = 16.sp, fontWeight = FontWeight.ExtraBold, color = fg)
                            }
                            Spacer(Modifier.height(5.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.CenterVertically,
                                modifier = Modifier.height(7.dp)) {
                                wd.markers.take(5).forEach { m ->
                                    Box(Modifier.size(width = if (m.kind == "movie") 12.dp else 6.dp, height = 6.dp)
                                        .clip(RoundedCornerShape(3.dp)).background(calStatusColor(m.status)))
                                }
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            if (entries.isEmpty()) {
                Box(Modifier.fillMaxWidth().height(110.dp), contentAlignment = Alignment.Center) {
                    Text(if (sel == 0) "Nothing new today — a good night for the backlog." else "Nothing on this day.",
                        fontSize = 14.sp, color = HColors.TextDim)
                }
            } else {
                val shown = if (entries.size > 4) entries.take(3) else entries
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    for (pair in (0 until 4).chunked(2)) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            pair.forEach { idx ->
                                val m = Modifier.weight(1f).focusProperties { up = chipFr[sel] }
                                when {
                                    idx < shown.size -> AgendaRow(shown[idx], if (idx == 0) firstFocus else null,
                                        m, { onOpen(shown[idx]) }, { onFocusedEntry(shown[idx]) })
                                    idx == 3 && entries.size > 4 -> MoreRow(entries.size - 3, m) { day?.let { onDay(it.date) } }
                                    else -> Spacer(m.height(52.dp))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    // Focus lands on today's first item (or today's tab when there's nothing today).
    if (grab) {
        LaunchedEffect(Unit) {
            delay(80)
            if (week.firstOrNull()?.entries.isNullOrEmpty()) runCatching { chipFr.firstOrNull()?.requestFocus() }
            else runCatching { firstFocus.requestFocus() }
            onGrabbed()
        }
    }
}

@Composable
private fun AgendaRow(e: CalEntry, fr: FocusRequester?, modifier: Modifier, onClick: () -> Unit, onFocused: () -> Unit) {
    val gw = LocalGateway.current
    FocusBox(onClick = onClick, modifier = modifier, shape = RoundedCornerShape(12.dp),
        focusedScale = 1.03f, borderWidth = 0.dp, onFocused = onFocused, focusRequester = fr) { f ->
        Row(
            Modifier.fillMaxWidth().height(52.dp)
                .background(if (f) HColors.Text else Color.White.copy(alpha = 0.07f)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.width(4.dp).fillMaxHeight().background(calStatusColor(e.status)))
            Spacer(Modifier.width(10.dp))
            AsyncImage(model = gw.img(e.poster), contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.size(width = 28.dp, height = 42.dp).clip(RoundedCornerShape(4.dp))
                    .background(HColors.Surface))
            Spacer(Modifier.width(10.dp))
            val fg = if (f) Color(0xFF0A0A0B) else HColors.Text
            Column(Modifier.weight(1f)) {
                Text(e.title, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = fg, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
                Text(e.subtitle ?: e.label, fontSize = 11.sp, color = if (f) fg.copy(alpha = 0.7f) else HColors.TextDim,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.width(8.dp))
            Text(
                when (e.status) {
                    "downloaded" -> "Ready"
                    "upcoming" -> e.time ?: "Soon"
                    "cinema" -> "Cinemas"
                    else -> e.label
                },
                fontSize = 11.sp, fontWeight = FontWeight.Bold, maxLines = 1,
                color = if (f) fg else calStatusColor(e.status),
            )
            Spacer(Modifier.width(12.dp))
        }
    }
}

@Composable
private fun MoreRow(more: Int, modifier: Modifier, onClick: () -> Unit) {
    FocusBox(onClick = onClick, modifier = modifier, shape = RoundedCornerShape(12.dp),
        focusedScale = 1.03f, borderWidth = 0.dp) { f ->
        Box(Modifier.fillMaxWidth().height(52.dp).background(if (f) HColors.Text else Color.White.copy(alpha = 0.07f)),
            contentAlignment = Alignment.Center) {
            Text("+ $more more  ›  open calendar", fontSize = 13.sp, fontWeight = FontWeight.Bold,
                color = if (f) Color(0xFF0A0A0B) else HColors.TextDim)
        }
    }
}
