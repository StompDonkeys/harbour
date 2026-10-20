package au.matt.harbour.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Search
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.CrashReporter
import au.matt.harbour.data.Gateway
import au.matt.harbour.data.GatewayConfig
import au.matt.harbour.data.Prefs
import au.matt.harbour.data.ServiceInfo
import au.matt.harbour.data.UpdateInfo
import au.matt.harbour.data.Updater
import au.matt.harbour.data.config
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import androidx.compose.ui.platform.LocalFocusManager
import au.matt.harbour.data.home
import au.matt.harbour.data.report

/** Bumps when the open menu section is picked again, so its screen can re-take focus. */
val LocalReselect = compositionLocalOf { 0 }

sealed interface Screen {
    data class Library(val svc: ServiceInfo) : Screen
    data class Detail(val svc: ServiceInfo, val itemId: String) : Screen
    data class Browse(val svc: ServiceInfo, val searchFirst: Boolean) : Screen
    data class Category(val svc: ServiceInfo, val key: String, val title: String) : Screen
    data class Releases(val svc: ServiceInfo, val itemId: String, val arg: String?, val title: String) : Screen
    data class Player(val ref: String) : Screen
    data object Calendar : Screen
    data object Dashboard : Screen
    data class CalendarDay(val date: String) : Screen
    data class Season(val svc: ServiceInfo, val showId: String, val number: Int, val focusEpisode: Int? = null) : Screen
    data class Trailer(val key: String, val title: String) : Screen
    data object Indexers : Screen
    data object Search : Screen
    data object Activity : Screen
    data class Discussion(val ref: String, val title: String) : Screen
    data object Downloads : Screen
    data object Files : Screen
    data object Settings : Screen
}

@Composable
fun HarbourApp(prefs: Prefs) {
    val context = LocalContext.current
    var url by remember { mutableStateOf(prefs.url) }
    var token by remember { mutableStateOf(prefs.token) }
    var plexUser by remember { mutableStateOf(prefs.plexUser) }
    var plexPin by remember { mutableStateOf(prefs.plexPin) }
    var plexName by remember { mutableStateOf(prefs.plexUserName) }
    val gateway = remember(url, token, plexUser, plexPin) {
        if (url.isBlank()) null else Gateway(url, token, plexUser, plexPin)
    }
    var loadedConfig by remember { mutableStateOf<GatewayConfig?>(null) }
    var configError by remember { mutableStateOf<String?>(null) }
    var reloadKey by remember { mutableIntStateOf(0) }
    var showSettings by remember { mutableStateOf(false) }
    var update by remember { mutableStateOf<UpdateInfo?>(null) }
    val toaster = remember { Toaster() }
    val cache = remember(gateway) { ScreenCache() }

    LaunchedEffect(gateway, reloadKey) {
        loadedConfig = null
        configError = null
        val g = gateway ?: return@LaunchedEffect
        val cfg = try {
            g.config()
        } catch (e: Exception) {
            configError = e.message ?: e.javaClass.simpleName
            return@LaunchedEffect
        }
        loadedConfig = cfg
        // A crash last session? Send it to the gateway log so it can be diagnosed.
        CrashReporter.pending(context)?.let { text ->
            runCatching {
                g.report(text, Updater.installedVersionName(context))
                CrashReporter.clear(context)
                toaster.show("Harbour closed unexpectedly last time — details sent to the gateway log")
            }
        }
        // Warm every library so switching tabs is instant.
        val warm = if (cfg.dashboard) cfg.libraries + cfg.services.filter { it.kind == "seerr" } else cfg.services
        for (s in warm) {
            if (cache.homes[s.key] == null) runCatching { cache.homes[s.key] = g.home(s) }
        }
        cfg.gatewayUpdate?.let {
            toaster.show("Gateway $it is available — the setup page shows how to update it")
        }
        // Quietly look for a newer build on GitHub.
        runCatching { Updater.check(context, cfg.repo) }.getOrNull()?.let {
            update = it
            toaster.show("Harbour V3 ${it.versionName} is available — open Settings to install")
        }
    }

    // Pick up changes made on the setup page or the phone (new services, indexers…) without a restart.
    LaunchedEffect(gateway) {
        val g = gateway ?: return@LaunchedEffect
        while (true) {
            kotlinx.coroutines.delay(60_000)
            val fresh = runCatching { g.config() }.getOrNull() ?: continue
            val cur = loadedConfig ?: continue
            if (fresh.copy(gatewayUpdate = cur.gatewayUpdate) != cur) loadedConfig = fresh
        }
    }

    val onSaved: (String, String) -> Unit = { u, t ->
        prefs.url = u
        prefs.token = t
        url = u
        token = t
        showSettings = false
        reloadKey++
    }
    val settings: @Composable () -> Unit = {
        SettingsScreen(
            initialUrl = url,
            initialToken = token,
            firstRun = gateway == null,
            onSaved = onSaved,
            repo = loadedConfig?.repo ?: "StompDonkeys/harbour",
            update = update,
            onUpdateFound = { update = it },
            plexEnabled = loadedConfig?.plex == true,
            gateway = gateway,
            plexName = plexName,
            onPlexProfile = { id, name, pin ->
                prefs.plexUser = id
                prefs.plexUserName = name
                prefs.plexPin = pin
                plexUser = id
                plexName = name
                plexPin = pin
                toaster.show(if (id.isBlank()) "Following the Plex owner account" else "Following $name on Plex")
            },
        )
    }

    CompositionLocalProvider(LocalToaster provides toaster, LocalCache provides cache) {
        Box(
            Modifier
                .fillMaxSize()
                .background(HColors.Bg),
        ) {
            val cfg = loadedConfig
            val gw = gateway
            val err = configError
            if (gw == null || showSettings) {
                if (showSettings) BackHandler { showSettings = false }
                settings()
            } else if (err != null) {
                ErrorPanel(
                    message = "Couldn't load from the Harbour gateway at ${gw.base}\n\n$err",
                    onRetry = { reloadKey++ },
                    extraLabel = "Settings",
                    onExtra = { showSettings = true },
                )
            } else if (cfg == null) {
                Loading()
            } else {
                CompositionLocalProvider(LocalGateway provides gw) {
                    MainShell(cfg, settings, updateAvailable = update != null)
                }
            }
            RecordingOverlay()
            ToastHost(toaster, Modifier.align(Alignment.BottomCenter))
        }
    }
}

private data class NavItem(val label: String, val root: Screen, val icon: ImageVector)

private val RailCollapsed = 76.dp
private val RailExpanded = 236.dp

private fun iconFor(s: ServiceInfo): ImageVector = when (s.kind) {
    "radarr" -> HIcons.Movie
    "sonarr" -> HIcons.Tv
    "seerr" -> HIcons.Explore
    "hub" -> Icons.Filled.Home
    "lib" -> if (s.inst == "tv") HIcons.Tv else HIcons.Movie
    else -> HIcons.Dashboard
}

private val EpisodeRef = Regex("""(tv-\d+):s(\d+)(?:e(\d+))?""")
private val TitleRef = Regex("""(movie|tv|person)-\d+""")

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun MainShell(config: GatewayConfig, settings: @Composable () -> Unit, updateAvailable: Boolean) {
    val hub = remember(config) { config.services.firstOrNull { it.kind == "hub" } }
    val v2 = config.dashboard && hub != null
    val items = remember(config) {
        buildList {
            if (v2 && hub != null) {
                add(NavItem("Search", Screen.Search, Icons.Filled.Search))
                add(NavItem("Home", Screen.Dashboard, Icons.Filled.Home))
                config.services.firstOrNull { it.kind == "seerr" }?.let { add(NavItem("Discover", Screen.Library(it), HIcons.Explore)) }
                config.libraries.forEach { add(NavItem(it.name, Screen.Library(it), iconFor(it))) }
            } else {
                config.services.forEach { add(NavItem(it.name, Screen.Library(it), iconFor(it))) }
            }
            if (config.calendar) add(NavItem("Calendar", Screen.Calendar, HIcons.Calendar))
            if (config.activity) add(NavItem("Activity", Screen.Activity, HIcons.Activity))
            // With the hub, indexer search lives inside Search (Movies & TV | Indexers).
            if (config.indexers && !v2) add(NavItem("Indexers", Screen.Indexers, HIcons.ManageSearch))
            if (config.downloads) add(NavItem("Downloads", Screen.Downloads, HIcons.Download))
            if (config.files) add(NavItem("Files", Screen.Files, HIcons.Folder))
            add(NavItem("Settings", Screen.Settings, Icons.Filled.Settings))
        }
    }
    val homeIndex = items.indexOfFirst { it.root == Screen.Dashboard }.takeIf { it >= 0 } ?: 0
    var tabIndex by remember { mutableIntStateOf(homeIndex) }
    val stack = remember { mutableStateListOf(items[homeIndex].root) }
    val requesters = remember(items) { items.map { FocusRequester() } }
    var railFocused by remember { mutableStateOf(false) }
    val contentFocus = remember { FocusRequester() }
    val holder = rememberSaveableStateHolder()
    val scope = rememberCoroutineScope()
    val focusManager = LocalFocusManager.current
    var reselect by remember { mutableIntStateOf(0) }
    var backdrop by remember { mutableStateOf<String?>(null) }

    fun selectTab(i: Int) {
        if (i != tabIndex || stack.size != 1) {
            tabIndex = i
            stack.clear()
            stack.add(items[i].root)
        } else {
            reselect++   // same section again: let the screen put focus back on itself
        }
        runCatching { contentFocus.requestFocus() }
        // If nothing in the screen took focus, the menu would stay open. Nudge focus out of it.
        scope.launch {
            delay(150)
            if (railFocused) {
                runCatching { contentFocus.requestFocus() }
                delay(50)
                if (railFocused) focusManager.moveFocus(FocusDirection.Right)
            }
        }
    }

    fun push(s: Screen) = stack.add(s)
    fun pop() {
        if (stack.size > 1) {
            val gone = stack.removeAt(stack.lastIndex)
            holder.removeState("${stack.size}:$gone")
        }
    }
    val play: (String) -> Unit = { ref -> push(Screen.Player(ref)) }

    /** One place that knows where an id leads: an episode, a title, a person, or an app's own page. */
    fun open(from: ServiceInfo?, id: String) {
        val ep = EpisodeRef.matchEntire(id)
        val parts = id.split("/", limit = 3)
        when {
            ep != null && hub != null ->
                push(Screen.Season(hub, ep.groupValues[1], ep.groupValues[2].toInt(), ep.groupValues[3].toIntOrNull()))
            parts.size == 3 -> config.services.firstOrNull { it.kind == parts[0] && it.inst == parts[1] }
                ?.let { push(Screen.Detail(it, parts[2])) }
            v2 && hub != null && TitleRef.matches(id) -> push(Screen.Detail(hub, id))
            from != null -> push(Screen.Detail(from, id))
            hub != null -> push(Screen.Detail(hub, id))
        }
    }

    // Back: pop a screen; on a section's first screen, jump to the menu; from there Back leaves the app.
    BackHandler(enabled = stack.size > 1) { pop() }
    BackHandler(enabled = stack.size == 1 && !railFocused) {
        runCatching { requesters[tabIndex].requestFocus() }
    }

    val screen = stack.last()
    val showRail = stack.size == 1
    val inset by animateDpAsState(if (showRail) RailCollapsed else 0.dp, tween(280), label = "inset")

    CompositionLocalProvider(LocalBackdrop provides backdrop, LocalBackdropSink provides { backdrop = it },
        LocalReselect provides reselect,
        LocalOpenDiscussion provides { ref, t -> push(Screen.Discussion(ref, t)) }) {
        Box(Modifier.fillMaxSize().background(HColors.Bg)) {
            Box(
                Modifier
                    .fillMaxSize()
                    .padding(start = inset)
                    .focusRequester(contentFocus)
                    .focusGroup(),
            ) { ScrollRules(auto = true) {
                AnimatedContent(
                    targetState = stack.lastIndex to screen,
                    transitionSpec = {
                        (fadeIn(tween(320)) + scaleIn(tween(380), initialScale = 0.985f))
                            .togetherWith(fadeOut(tween(200)))
                    },
                    label = "screen",
                ) { (idx, sc) ->
                    val stateKey = "$idx:$sc"
                    key(stateKey) {
                        holder.SaveableStateProvider(stateKey) {
                            ScreenContent(sc, config, settings, ::push, ::pop, play, ::open)
                        }
                    }
                }
            } }
            AnimatedVisibility(
                visible = showRail,
                enter = fadeIn(tween(250)) + slideInHorizontally(tween(300)) { -it },
                exit = fadeOut(tween(200)) + slideOutHorizontally(tween(250)) { -it },
            ) {
                Rail(
                    items = items,
                    selected = tabIndex,
                    requesters = requesters,
                    onSelect = { selectTab(it) },
                    onFocusChanged = { railFocused = it },
                    onExit = { runCatching { contentFocus.requestFocus() } },
                    dotOn = if (updateAvailable) items.lastIndex else -1,
                )
            }
        }
    }
}

@Composable
private fun ScreenContent(
    screen: Screen,
    config: GatewayConfig,
    settings: @Composable () -> Unit,
    push: (Screen) -> Unit,
    pop: () -> Unit,
    play: (String) -> Unit,
    open: (ServiceInfo?, String) -> Unit,
) {
    val trailer: (String, String) -> Unit = { key, title -> push(Screen.Trailer(key, title)) }
    when (screen) {
        is Screen.Library -> LibraryScreen(
            svc = screen.svc,
            onOpen = { id -> open(screen.svc, id) },
            onBrowse = { search -> push(Screen.Browse(screen.svc, search)) },
            onSeeAll = { key, title -> push(Screen.Category(screen.svc, key, title)) },
            onPlay = if (config.plex) play else null,
        )
        is Screen.Category -> CategoryScreen(
            svc = screen.svc,
            key = screen.key,
            title = screen.title,
            onOpen = { id -> open(screen.svc, id) },
        )
        is Screen.Detail -> DetailScreen(
            svc = screen.svc,
            itemId = screen.itemId,
            onClose = pop,
            onOpenReleases = { arg, title -> push(Screen.Releases(screen.svc, screen.itemId, arg, title)) },
            onOpenId = { id -> open(screen.svc, id) },
            onOpenItem = { ref -> open(null, ref) },
            onPlay = play,
            onTrailer = trailer,
        )
        is Screen.Season -> SeasonScreen(
            svc = screen.svc,
            showId = screen.showId,
            number = screen.number,
            focusEpisode = screen.focusEpisode,
            onOpenReleases = { arg, title -> push(Screen.Releases(screen.svc, screen.showId, arg, title)) },
            onPlay = play,
            onTrailer = trailer,
            onOpenShow = { open(screen.svc, screen.showId) },
        )
        is Screen.Browse -> BrowseScreen(
            svc = screen.svc,
            searchFirst = screen.searchFirst,
            onOpen = { id -> open(screen.svc, id) },
        )
        is Screen.Releases -> ReleasesScreen(
            svc = screen.svc,
            itemId = screen.itemId,
            arg = screen.arg,
            title = screen.title,
            onDone = pop,
        )
        is Screen.Player -> PlayerScreen(ref = screen.ref, onExit = pop)
        is Screen.Trailer -> TrailerScreen(key = screen.key, title = screen.title, onExit = pop)
        Screen.Dashboard -> DashboardScreen(
            hub = config.services.first { it.kind == "hub" },
            onOpen = { id -> open(null, id) },
            onCalendarDay = { date -> push(Screen.CalendarDay(date)) },
        )
        Screen.Calendar -> CalendarScreen(onOpen = { e -> open(null, calTarget(e)) })
        is Screen.CalendarDay -> CalendarScreen(onOpen = { e -> open(null, calTarget(e)) }, initialDate = screen.date)
        Screen.Indexers -> TopInset { IndexerScreen() }
        Screen.Search -> SearchScreen(
            hub = config.services.first { it.kind == "hub" },
            indexers = config.indexers,
            onOpen = { id -> open(null, id) },
        )
        Screen.Downloads -> TopInset { DownloadsScreen() }
        Screen.Activity -> TopInset { ActivityScreen() }
        is Screen.Discussion -> TopInset { DiscussionScreen(screen.ref, screen.title) }
        Screen.Files -> TopInset { FilesScreen(onPlay = { path -> play("file:$path") }) }
        Screen.Settings -> TopInset { settings() }
    }
}

private fun calTarget(e: au.matt.harbour.data.CalEntry): String = e.openId
    ?: if (e.kind == "tv" && e.open != null && e.season != null) "${e.open}:s${e.season}e${e.episode ?: 0}"
    else e.open ?: "${e.svc}/${e.id}"

/** Plain (non-artwork) screens: clear the backdrop and leave a little breathing room up top. */
@Composable
private fun TopInset(content: @Composable () -> Unit) {
    val sink = LocalBackdropSink.current
    SideEffect { sink(null) }
    Box(Modifier.fillMaxSize().padding(top = 24.dp)) { content() }
}

/**
 * The menu: a slim rail of icons on the left that widens to show names when you move onto it,
 * dimming the screen behind. OK opens a section; Right goes back to the screen.
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun Rail(
    items: List<NavItem>,
    selected: Int,
    requesters: List<FocusRequester>,
    onSelect: (Int) -> Unit,
    onFocusChanged: (Boolean) -> Unit,
    onExit: () -> Unit,
    dotOn: Int,
) {
    var expanded by remember { mutableStateOf(false) }
    val width by animateDpAsState(if (expanded) RailExpanded else RailCollapsed, tween(260), label = "rail")
    val scrim by animateFloatAsState(if (expanded) 1f else 0f, tween(260), label = "scrim")
    Box(Modifier.fillMaxHeight()) {
        if (scrim > 0f) {
            Box(
                Modifier
                    .fillMaxHeight()
                    .width(720.dp)
                    .graphicsLayer { alpha = scrim }
                    .background(Brush.horizontalGradient(
                        0f to HColors.Bg.copy(alpha = 0.92f), 0.45f to HColors.Bg.copy(alpha = 0.6f), 1f to Color.Transparent)),
            )
        }
        Column(
            Modifier
                .fillMaxHeight()
                .width(width)
                .background(Brush.horizontalGradient(listOf(Color(0xF0050506), Color(0xD9050506))))
                .onFocusChanged {
                    expanded = it.hasFocus
                    onFocusChanged(it.hasFocus)
                }
                .focusProperties { enter = { requesters[selected] } }
                .onPreviewKeyEvent { e ->
                    // Right always goes back into the screen (the rail overlaps it, so don't rely on
                    // geometry to find something to the right).
                    if (e.nativeKeyEvent.keyCode == android.view.KeyEvent.KEYCODE_DPAD_RIGHT) {
                        if (e.type == KeyEventType.KeyDown) onExit()
                        true
                    } else false
                }
                .focusGroup()
                .padding(horizontal = 12.dp, vertical = 18.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(Modifier.height(40.dp).padding(start = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(32.dp).clip(RoundedCornerShape(9.dp))
                        .background(Brush.linearGradient(listOf(Color(0xFF5AC8FA), Color(0xFF0A84FF)))),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("H", color = Color.White, fontSize = 17.sp, fontWeight = FontWeight.ExtraBold)
                }
                if (width > 140.dp) {
                    Spacer(Modifier.width(12.dp))
                    Text("Harbour", fontSize = 18.sp, fontWeight = FontWeight.ExtraBold, maxLines = 1,
                        modifier = Modifier.graphicsLayer { alpha = scrim })
                }
            }
            Spacer(Modifier.height(10.dp))
            // Everything but Settings scrolls if it doesn't fit; Settings stays pinned at the bottom.
            Column(
                Modifier.weight(1f).verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                items.forEachIndexed { i, item ->
                    if (i != items.lastIndex) {
                        RailItem(item, i == selected, width > 140.dp, scrim, i == dotOn, requesters[i]) { onSelect(i) }
                    }
                }
            }
            items.lastIndex.let { i ->
                RailItem(items[i], i == selected, width > 140.dp, scrim, i == dotOn, requesters[i]) { onSelect(i) }
            }
        }
    }
}

@Composable
private fun RailItem(
    item: NavItem,
    selected: Boolean,
    showLabel: Boolean,
    labelAlpha: Float,
    dot: Boolean,
    requester: FocusRequester,
    onClick: () -> Unit,
) {
    FocusBox(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        focusedScale = 1.03f,
        borderWidth = 0.dp,
        focusRequester = requester,
    ) { f ->
        Row(
            Modifier
                .fillMaxWidth()
                .height(42.dp)
                .background(
                    when {
                        f -> HColors.Text
                        selected -> Color.White.copy(alpha = 0.13f)
                        else -> Color.Transparent
                    },
                )
                .padding(start = 15.dp, end = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val fg = when {
                f -> Color(0xFF0A0A0B)
                selected -> HColors.Text
                else -> HColors.TextDim
            }
            Box {
                Icon(item.icon, contentDescription = item.label, tint = fg, modifier = Modifier.size(22.dp))
                if (dot) {
                    Box(Modifier.align(Alignment.TopEnd).size(7.dp).clip(CircleShape).background(HColors.Warn))
                }
            }
            if (showLabel) {
                Spacer(Modifier.width(16.dp))
                Text(item.label, color = fg, fontSize = 16.sp, maxLines = 1,
                    fontWeight = if (f || selected) FontWeight.Bold else FontWeight.SemiBold,
                    modifier = Modifier.graphicsLayer { alpha = labelAlpha })
            }
        }
    }
}
