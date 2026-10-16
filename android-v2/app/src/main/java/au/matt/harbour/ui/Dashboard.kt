package au.matt.harbour.ui

import androidx.compose.foundation.background
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

    Box(Modifier.fillMaxSize()) {
        Backdrop(art, dim = 1.3f)
        LazyColumn(
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
                            Text("⚠  ${a.text}", fontSize = 11.sp, color = Color.White, maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier
                                    .clip(RoundedCornerShape(8.dp))
                                    .background((if (a.tone == "danger") HColors.Danger else HColors.Warn).copy(alpha = 0.85f))
                                    .padding(horizontal = 10.dp, vertical = 5.dp)
                                    .width(220.dp))
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
                Row(
                    Modifier
                        .padding(start = 40.dp, end = 36.dp)
                        .onFocusChanged { if (it.hasFocus) focusRow = -1 },
                    horizontalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    TodayPanel(
                        entries = d.today,
                        firstFocus = firstFocus,
                        onOpen = { e -> onOpen(e.openId ?: e.open ?: "${e.svc}/${e.id}") },
                        onFocused = { e -> e.backdrop?.let { artUrl = it } },
                        onMore = { d.week.firstOrNull()?.let { onCalendarDay(it.date) } },
                        modifier = Modifier.weight(1.15f),
                    )
                    WeekPanel(d.week, onDay = onCalendarDay, modifier = Modifier.weight(1f))
                }
                LaunchedEffect(Unit) {
                    delay(80)
                    runCatching { firstFocus.requestFocus() }
                }
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
        }
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

/** Today's episodes and releases — the first thing focused on Home. */
@Composable
private fun TodayPanel(
    entries: List<CalEntry>,
    firstFocus: FocusRequester,
    onOpen: (CalEntry) -> Unit,
    onFocused: (CalEntry) -> Unit,
    onMore: () -> Unit,
    modifier: Modifier,
) {
    GlassPanel(modifier.height(236.dp), shape = RoundedCornerShape(20.dp)) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(start = 4.dp)) {
                Text("OUT TODAY", fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp,
                    color = HColors.TextDim, modifier = Modifier.weight(1f))
                if (entries.isNotEmpty()) {
                    val ready = entries.count { it.status == "downloaded" }
                    Text(if (ready > 0) "$ready ready to watch" else "${entries.size} today", fontSize = 11.sp,
                        color = if (ready > 0) HColors.Ok else HColors.TextDim, fontWeight = FontWeight.SemiBold)
                }
            }
            if (entries.isEmpty()) {
                Box(Modifier.fillMaxWidth().weight(1f), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text("Nothing new today", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                        Text("A good night for the backlog.", fontSize = 13.sp, color = HColors.TextDim)
                        Spacer(Modifier.height(12.dp))
                        TvButton("Open calendar", onMore, focusRequester = firstFocus)
                    }
                }
            } else {
                val shown = entries.take(if (entries.size > 4) 3 else 4)
                shown.forEachIndexed { i, e ->
                    TodayRow(e, if (i == 0) firstFocus else null, { onOpen(e) }, { onFocused(e) })
                }
                if (entries.size > shown.size) {
                    FocusBox(onClick = onMore, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
                        focusedScale = 1.02f, borderWidth = 0.dp) { f ->
                        Text("+ ${entries.size - shown.size} more today  ›", fontSize = 13.sp, fontWeight = FontWeight.Bold,
                            color = if (f) Color(0xFF0A0A0B) else HColors.TextDim,
                            modifier = Modifier.fillMaxWidth().background(if (f) HColors.Text else Color.Transparent)
                                .padding(horizontal = 12.dp, vertical = 8.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun TodayRow(e: CalEntry, fr: FocusRequester?, onClick: () -> Unit, onFocused: () -> Unit) {
    val gw = LocalGateway.current
    FocusBox(onClick = onClick, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
        focusedScale = 1.02f, borderWidth = 0.dp, onFocused = onFocused, focusRequester = fr) { f ->
        Row(
            Modifier.fillMaxWidth().height(38.dp)
                .background(if (f) HColors.Text else Color.White.copy(alpha = 0.06f)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.width(4.dp).fillMaxHeight().background(calStatusColor(e.status)))
            Spacer(Modifier.width(10.dp))
            AsyncImage(model = gw.img(e.poster), contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.size(width = 24.dp, height = 36.dp).clip(RoundedCornerShape(4.dp))
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
                    "upcoming" -> e.time ?: "Later"
                    else -> e.label
                },
                fontSize = 11.sp, fontWeight = FontWeight.Bold, maxLines = 1,
                color = if (f) fg else calStatusColor(e.status),
            )
            Spacer(Modifier.width(12.dp))
        }
    }
}

/** The next seven days at a glance; each day opens the calendar there. */
@Composable
private fun WeekPanel(week: List<WeekDay>, onDay: (String) -> Unit, modifier: Modifier) {
    GlassPanel(modifier.height(236.dp), shape = RoundedCornerShape(20.dp)) {
        Column(Modifier.padding(14.dp)) {
            Text("THIS WEEK", fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp,
                color = HColors.TextDim, modifier = Modifier.padding(start = 4.dp, bottom = 10.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                week.forEach { day ->
                    FocusBox(onClick = { onDay(day.date) }, modifier = Modifier.weight(1f), shape = RoundedCornerShape(12.dp),
                        focusedScale = 1.08f, borderWidth = 0.dp) { f ->
                        Column(
                            Modifier.fillMaxWidth().height(176.dp)
                                .background(if (f) HColors.Text else Color.White.copy(alpha = if (day.dow == "Today") 0.12f else 0.05f))
                                .padding(vertical = 10.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            val fg = if (f) Color(0xFF0A0A0B) else HColors.Text
                            Text(if (day.dow == "Today") "TDY" else day.dow.uppercase(), fontSize = 10.sp,
                                fontWeight = FontWeight.Bold, letterSpacing = 0.8.sp,
                                color = if (f) fg else HColors.TextDim)
                            Text("${day.day}", fontSize = 20.sp, fontWeight = FontWeight.ExtraBold, color = fg)
                            Spacer(Modifier.height(8.dp))
                            Column(verticalArrangement = Arrangement.spacedBy(4.dp),
                                horizontalAlignment = Alignment.CenterHorizontally) {
                                day.markers.take(6).forEach { m ->
                                    Box(Modifier.size(width = if (m.kind == "movie") 18.dp else 8.dp, height = 8.dp)
                                        .clip(if (m.kind == "movie") RoundedCornerShape(3.dp) else CircleShape)
                                        .background(calStatusColor(m.status)))
                                }
                            }
                            Spacer(Modifier.weight(1f))
                            if (day.count > 0) {
                                Text("${day.count}", fontSize = 11.sp, fontWeight = FontWeight.Bold,
                                    color = if (f) fg else HColors.TextDim)
                            }
                        }
                    }
                }
            }
        }
    }
}
