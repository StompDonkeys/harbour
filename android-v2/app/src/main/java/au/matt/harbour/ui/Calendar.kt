package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.KeyboardArrowLeft
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import au.matt.harbour.data.CalEntry
import au.matt.harbour.data.CalendarData
import au.matt.harbour.data.calendar
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import java.util.Calendar as JCal
import java.util.GregorianCalendar

// ------------------------------------------------------------------ tiny date maths
// (java.time needs API 26; Harbour still runs on Fire OS 6, so plain Calendar it is.)

data class Ymd(val y: Int, val m: Int, val d: Int) : Comparable<Ymd> {
    private fun cal() = GregorianCalendar(y, m - 1, d)
    fun plusDays(n: Int): Ymd = cal().apply { add(JCal.DAY_OF_MONTH, n) }.toYmd()
    fun plusMonths(n: Int): Ymd = GregorianCalendar(y, m - 1, 1).apply { add(JCal.MONTH, n) }.toYmd()
    /** Monday = 0 … Sunday = 6. */
    val weekday: Int get() = (cal().get(JCal.DAY_OF_WEEK) + 5) % 7
    val daysInMonth: Int get() = cal().getActualMaximum(JCal.DAY_OF_MONTH)
    val iso: String get() = "%04d-%02d-%02d".format(y, m, d)
    fun sameMonth(o: Ymd) = y == o.y && m == o.m
    override fun compareTo(other: Ymd) = compareValuesBy(this, other, { it.y }, { it.m }, { it.d })

    companion object {
        fun today(): Ymd = GregorianCalendar().toYmd()
        fun parse(s: String): Ymd? = runCatching {
            Ymd(s.substring(0, 4).toInt(), s.substring(5, 7).toInt(), s.substring(8, 10).toInt())
        }.getOrNull()
    }
}

private fun JCal.toYmd() = Ymd(get(JCal.YEAR), get(JCal.MONTH) + 1, get(JCal.DAY_OF_MONTH))

private val MONTHS = listOf("January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December")
private val WEEKDAYS = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

// ------------------------------------------------------------------ colours

private val Cinema = Color(0xFFBF5AF2)
private val Airing = Color(0xFF64D2FF)

fun calStatusColor(s: String): Color = when (s) {
    "downloaded" -> HColors.Ok
    "downloading" -> HColors.Warn
    "airing" -> Airing
    "missing" -> HColors.Danger
    "upcoming" -> HColors.Info
    "cinema" -> Cinema
    else -> HColors.Muted
}

private val RANK = listOf("downloaded", "downloading", "airing", "missing", "upcoming", "cinema", "unmonitored")

/** Survives tab switches, so coming back to the calendar is instant (then quietly refreshes). */
private object CalCache {
    val months = mutableStateMapOf<String, CalendarData>()
}

// ------------------------------------------------------------------ screen

@Composable
fun CalendarScreen(onOpen: (CalEntry) -> Unit, initialDate: String? = null) {
    val gw = LocalGateway.current
    val toaster = LocalToaster.current
    val today = remember { Ymd.today() }

    val start = initialDate?.let { Ymd.parse(it) } ?: today
    var monthIso by rememberSaveable { mutableStateOf(Ymd(start.y, start.m, 1).iso) }
    val month = Ymd.parse(monthIso) ?: Ymd(today.y, today.m, 1)
    var selIso by rememberSaveable { mutableStateOf(start.iso) }
    val selected = Ymd.parse(selIso) ?: today
    var filter by rememberSaveable { mutableStateOf("all") }        // all | tv | movie
    var unmonitored by rememberSaveable { mutableStateOf(false) }
    var radarrSel by rememberSaveable { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    // Six Monday-first weeks around the month.
    val gridStart = month.plusDays(-month.weekday)
    val days = remember(monthIso) { List(42) { gridStart.plusDays(it) } }
    val cacheKey = "$monthIso|$unmonitored"
    val data = CalCache.months[cacheKey]

    LaunchedEffect(cacheKey, reload) {
        loading = true
        error = null
        try {
            CalCache.months[cacheKey] = gw.calendar(days.first().iso, days.last().iso, unmonitored)
        } catch (e: Exception) {
            if (CalCache.months[cacheKey] == null) error = e.message else toaster.show("⚠ ${e.message}")
        } finally {
            loading = false
        }
    }

    val radarrs = data?.sources?.filter { it.kind == "radarr" }.orEmpty()
    val entries = remember(data, filter, radarrSel) {
        val all = data?.entries.orEmpty().filter { e ->
            (filter == "all" || e.kind == filter) &&
                (e.kind != "movie" || radarrSel == null || e.svc == radarrSel)
        }
        // With every Radarr showing, a movie that's in both (HD + 4K) appears once, best status wins.
        all.groupBy { if (it.kind == "movie") "${it.open ?: it.title}|${it.date}|${it.subtitle}" else it.key }
            .values.map { g -> g.minBy { RANK.indexOf(it.status).let { r -> if (r < 0) 99 else r } } }
    }
    val byDay = remember(entries) { entries.groupBy { it.date } }
    val dayEntries = byDay[selected.iso].orEmpty()

    // Backdrop follows the focused entry, else the first thing on the selected day.
    var focusedEntry by remember { mutableStateOf<CalEntry?>(null) }
    var art by remember { mutableStateOf<String?>(null) }
    val wantArt = (focusedEntry?.takeIf { it.date == selected.iso } ?: dayEntries.firstOrNull { it.backdrop != null })
        ?.backdrop
    LaunchedEffect(wantArt) {
        delay(250)
        if (wantArt != null) art = gw.hero(wantArt)
    }

    val dayFr = remember(monthIso) { List(42) { FocusRequester() } }
    val listFr = remember { FocusRequester() }
    var grabbed by remember { mutableStateOf(false) }
    val selIndex = days.indexOf(selected).takeIf { it >= 0 && days[it].sameMonth(month) }
        ?: days.indexOfFirst { it.sameMonth(month) }

    fun goMonth(delta: Int) {
        val nm = month.plusMonths(delta)
        monthIso = nm.iso
        selIso = (if (nm.sameMonth(today)) today else nm).iso
    }

    Box(Modifier.fillMaxSize()) {
        Backdrop(art, dim = 1.25f)
        Row(Modifier.fillMaxSize().padding(start = 40.dp, end = 36.dp, top = 30.dp, bottom = 20.dp)) {
            // ---------------------------------------------------------- left: month grid
            Column(Modifier.width(452.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TvButton("", { goMonth(-1) }, icon = Icons.Filled.KeyboardArrowLeft)
                    Text("${MONTHS[month.m - 1]} ${month.y}", fontSize = 24.sp, fontWeight = FontWeight.ExtraBold,
                        letterSpacing = (-0.4).sp, modifier = Modifier.weight(1f).padding(horizontal = 14.dp),
                        maxLines = 1)
                    if (loading) {
                        CircularProgressIndicator(color = HColors.TextDim, strokeWidth = 2.dp,
                            modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(12.dp))
                    }
                    TvButton("", { goMonth(1) }, icon = Icons.Filled.KeyboardArrowRight)
                    Spacer(Modifier.width(8.dp))
                    TvButton("Today", {
                        monthIso = Ymd(today.y, today.m, 1).iso
                        selIso = today.iso
                    })
                }
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Chip("All", filter == "all") { filter = "all" }
                    Chip("TV", filter == "tv") { filter = "tv" }
                    Chip("Movies", filter == "movie") { filter = "movie" }
                    Chip("Unmonitored", unmonitored) { unmonitored = !unmonitored }
                    if (radarrs.size > 1 && filter != "tv") {
                        val name = radarrs.firstOrNull { it.svc == radarrSel }?.name ?: "All"
                        Chip("Radarr: $name", radarrSel != null) {
                            val i = radarrs.indexOfFirst { it.svc == radarrSel }
                            radarrSel = if (i + 1 >= radarrs.size) null else radarrs[i + 1].svc
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                GlassPanel(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
                    Column(Modifier.padding(horizontal = 8.dp, vertical = 8.dp)) {
                        Row(Modifier.fillMaxWidth().padding(bottom = 2.dp)) {
                            WEEKDAYS.forEach {
                                Text(it.take(3), color = HColors.TextFaint, fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f),
                                    textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                            }
                        }
                        for (w in 0 until 6) {
                            Row(Modifier.fillMaxWidth()) {
                                for (c in 0 until 7) {
                                    val i = w * 7 + c
                                    val day = days[i]
                                    DayCell(
                                        day = day,
                                        inMonth = day.sameMonth(month),
                                        isToday = day == today,
                                        isSelected = day == selected,
                                        entries = byDay[day.iso].orEmpty(),
                                        focusRequester = dayFr[i],
                                        onFocused = { selIso = day.iso; focusedEntry = null },
                                        onClick = {
                                            if (byDay[day.iso].orEmpty().isNotEmpty()) runCatching { listFr.requestFocus() }
                                        },
                                        modifier = Modifier.weight(1f),
                                    )
                                }
                            }
                        }
                    }
                }
                Spacer(Modifier.height(10.dp))
                Legend()
            }

            // ---------------------------------------------------------- right: the day
            Column(Modifier.weight(1f).padding(start = 32.dp)) {
                Text(WEEKDAYS[selected.weekday].uppercase(), color = HColors.TextDim, fontSize = 12.sp,
                    fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp)
                Text(
                    "${selected.d} ${MONTHS[selected.m - 1]}" + if (selected == today) " · Today" else "",
                    fontSize = 30.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.6).sp,
                )
                val tv = dayEntries.count { it.kind == "tv" }
                val mv = dayEntries.count { it.kind == "movie" }
                Text(
                    listOfNotNull(
                        if (tv > 0) "$tv episode${if (tv != 1) "s" else ""}" else null,
                        if (mv > 0) "$mv movie${if (mv != 1) "s" else ""}" else null,
                    ).joinToString(" · ").ifEmpty { if (data == null) "Loading…" else "Nothing on this day" },
                    color = HColors.TextDim, fontSize = 14.sp,
                )
                Spacer(Modifier.height(12.dp))
                val err = error
                if (err != null && data == null) {
                    Text(err, color = HColors.Danger, fontSize = 14.sp)
                    Spacer(Modifier.height(10.dp))
                    TvButton("Try again", { reload++ })
                }
                androidx.compose.runtime.key(selected.iso) {
                    val list = dayEntries
                    LazyColumn(
                        state = rememberLazyListState(),
                        modifier = Modifier.fillMaxSize().fadeTop(10.dp),
                        contentPadding = PaddingValues(top = 6.dp, bottom = 40.dp, start = 4.dp, end = 8.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        itemsIndexed(list, key = { _, e -> e.key }) { i, e ->
                            EntryRow(
                                e = e,
                                focusRequester = if (i == 0) listFr else null,
                                modifier = Modifier.focusProperties {
                                    if (selIndex >= 0) left = dayFr[selIndex]
                                },
                                onFocused = { focusedEntry = e },
                                onClick = { onOpen(e) },
                            )
                        }
                    }
                }
            }
        }
    }

    // Land on the selected day when the screen opens or the month changes.
    LaunchedEffect(monthIso) {
        if (selIndex >= 0) {
            delay(if (grabbed) 0 else 60)
            if (!grabbed) runCatching { dayFr[selIndex].requestFocus() }
            grabbed = true
        }
    }
}

// ------------------------------------------------------------------ pieces

@Composable
private fun Chip(text: String, on: Boolean, onClick: () -> Unit) {
    FocusBox(onClick = onClick, shape = RoundedCornerShape(999.dp), focusedScale = 1.06f, borderWidth = 0.dp) { f ->
        Text(
            text,
            color = when {
                f -> Color(0xFF0A0A0B)
                on -> Color.White
                else -> HColors.TextDim
            },
            fontSize = 13.sp,
            fontWeight = if (on || f) FontWeight.Bold else FontWeight.SemiBold,
            maxLines = 1,
            modifier = Modifier
                .background(
                    when {
                        f -> HColors.Text
                        on -> HColors.Info
                        else -> HColors.GlassFill
                    },
                )
                .border(1.dp, if (on || f) Color.Transparent else HColors.GlassEdge, RoundedCornerShape(999.dp))
                .padding(horizontal = 14.dp, vertical = 7.dp),
        )
    }
}

@Composable
private fun DayCell(
    day: Ymd,
    inMonth: Boolean,
    isToday: Boolean,
    isSelected: Boolean,
    entries: List<CalEntry>,
    focusRequester: FocusRequester,
    onFocused: () -> Unit,
    onClick: () -> Unit,
    modifier: Modifier,
) {
    val body: @Composable (Boolean) -> Unit = { f ->
        Column(
            Modifier
                .fillMaxWidth()
                .height(46.dp)
                .background(
                    when {
                        f -> HColors.Text
                        isSelected -> Color.White.copy(alpha = 0.14f)
                        else -> Color.Transparent
                    },
                ),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Box(
                Modifier
                    .size(26.dp)
                    .clip(CircleShape)
                    .background(if (isToday && !f) HColors.Info else Color.Transparent),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    "${day.d}",
                    fontSize = 15.sp,
                    fontWeight = if (isToday || f || isSelected) FontWeight.Bold else FontWeight.Medium,
                    color = when {
                        f -> Color(0xFF0A0A0B)
                        !inMonth -> HColors.TextFaint.copy(alpha = 0.35f)
                        else -> HColors.Text
                    },
                )
            }
            Spacer(Modifier.height(3.dp))
            Markers(entries, dimmed = !inMonth)
        }
    }
    if (inMonth) {
        FocusBox(
            onClick = onClick,
            modifier = modifier.padding(2.dp),
            shape = RoundedCornerShape(10.dp),
            focusedScale = 1.1f,
            borderWidth = 0.dp,
            onFocused = onFocused,
            focusRequester = focusRequester,
        ) { f -> body(f) }
    } else {
        Box(modifier.padding(2.dp)) { body(false) }
    }
}

/** TV = dots, movies = little bars, coloured by status. A few at most, then a +n. */
@Composable
private fun Markers(entries: List<CalEntry>, dimmed: Boolean) {
    Row(Modifier.height(6.dp), horizontalArrangement = Arrangement.spacedBy(3.dp),
        verticalAlignment = Alignment.CenterVertically) {
        val shown = entries.sortedBy { if (it.kind == "movie") 0 else 1 }.take(4)
        shown.forEach { e ->
            val c = calStatusColor(e.status).let { if (dimmed) it.copy(alpha = 0.35f) else it }
            Box(
                Modifier
                    .size(width = if (e.kind == "movie") 12.dp else 5.dp, height = 5.dp)
                    .clip(RoundedCornerShape(3.dp))
                    .background(c),
            )
        }
        if (entries.size > 4) {
            Text("+${entries.size - 4}", fontSize = 8.sp, color = HColors.TextFaint, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
private fun Legend() {
    Row(Modifier.padding(start = 6.dp), horizontalArrangement = Arrangement.spacedBy(14.dp),
        verticalAlignment = Alignment.CenterVertically) {
        listOf("downloaded" to "Have it", "downloading" to "Downloading", "missing" to "Missing",
            "upcoming" to "Upcoming", "cinema" to "Cinema").forEach { (s, l) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(6.dp).clip(CircleShape).background(calStatusColor(s)))
                Spacer(Modifier.width(5.dp))
                Text(l, fontSize = 11.sp, color = HColors.TextDim)
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(width = 12.dp, height = 5.dp).clip(RoundedCornerShape(3.dp)).background(HColors.TextDim))
            Spacer(Modifier.width(5.dp))
            Text("Movie", fontSize = 11.sp, color = HColors.TextDim)
        }
    }
}

@Composable
private fun EntryRow(
    e: CalEntry,
    focusRequester: FocusRequester?,
    modifier: Modifier,
    onFocused: () -> Unit,
    onClick: () -> Unit,
) {
    val gw = LocalGateway.current
    val tone = calStatusColor(e.status)
    FocusBox(
        onClick = onClick,
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        focusedScale = 1.03f,
        onFocused = onFocused,
        focusRequester = focusRequester,
    ) { f ->
        Row(
            Modifier
                .fillMaxWidth()
                .background(if (f) Color(0x47FFFFFF) else Color(0x99141418))
                .border(1.dp, HColors.GlassEdge, RoundedCornerShape(16.dp)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.width(4.dp).height(84.dp).background(tone))
            Spacer(Modifier.width(12.dp))
            AsyncImage(
                model = gw.img(e.poster),
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .size(width = 46.dp, height = 68.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(HColors.Surface),
            )
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f).padding(vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(e.title, fontSize = 17.sp, fontWeight = FontWeight.Bold, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                    if (e.tag != null && e.kind == "tv") {
                        Spacer(Modifier.width(8.dp))
                        Text(e.tag.uppercase(), fontSize = 9.sp, fontWeight = FontWeight.ExtraBold,
                            letterSpacing = 0.6.sp, color = Color.White, maxLines = 1,
                            modifier = Modifier.clip(RoundedCornerShape(5.dp)).background(HColors.Info)
                                .padding(horizontal = 6.dp, vertical = 2.dp))
                    }
                }
                if (!e.subtitle.isNullOrBlank()) {
                    Text(e.subtitle, fontSize = 13.sp, color = HColors.TextDim, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(7.dp).clip(CircleShape).background(tone))
                    Spacer(Modifier.width(6.dp))
                    Text(
                        listOfNotNull(
                            e.label.ifBlank { null },
                            e.quality,
                            e.time?.takeIf { e.status != "upcoming" },
                            e.network ?: e.source.takeIf { e.kind == "movie" },
                        ).joinToString("  ·  "),
                        fontSize = 12.sp, color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Spacer(Modifier.width(12.dp))
        }
    }
}
