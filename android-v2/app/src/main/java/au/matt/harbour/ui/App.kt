package au.matt.harbour.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
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
import au.matt.harbour.data.home
import au.matt.harbour.data.report

sealed interface Screen {
    data class Library(val svc: ServiceInfo) : Screen
    data class Detail(val svc: ServiceInfo, val itemId: String) : Screen
    data class Browse(val svc: ServiceInfo, val searchFirst: Boolean) : Screen
    data class Category(val svc: ServiceInfo, val key: String, val title: String) : Screen
    data class Releases(val svc: ServiceInfo, val itemId: String, val arg: String?, val title: String) : Screen
    data class Player(val ref: String) : Screen
    data object Downloads : Screen
    data object Files : Screen
    data object Settings : Screen
}

private data class Tab(val label: String, val root: Screen, val icon: ImageVector? = null)

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
        for (s in cfg.services) {
            if (cache.homes[s.key] == null) runCatching { cache.homes[s.key] = g.home(s) }
        }
        // Quietly look for a newer build on GitHub.
        runCatching { Updater.check(context, cfg.repo) }.getOrNull()?.let {
            update = it
            toaster.show("Harbour v2 ${it.versionName} is available — open Settings to install")
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
            ToastHost(toaster, Modifier.align(Alignment.BottomCenter))
        }
    }
}

@Composable
private fun MainShell(config: GatewayConfig, settings: @Composable () -> Unit, updateAvailable: Boolean) {
    val tabs = remember(config) {
        buildList {
            config.services.forEach { add(Tab(it.name, Screen.Library(it))) }
            if (config.downloads) add(Tab("Downloads", Screen.Downloads))
            if (config.files) add(Tab("Files", Screen.Files))
            add(Tab("Settings", Screen.Settings, Icons.Filled.Settings))
        }
    }
    var tabIndex by remember { mutableIntStateOf(0) }
    val stack = remember { mutableStateListOf(tabs[0].root) }
    val tabRequesters = remember(tabs) { tabs.map { FocusRequester() } }
    var navFocused by remember { mutableStateOf(false) }
    val contentFocus = remember { FocusRequester() }
    val holder = rememberSaveableStateHolder()
    var backdrop by remember { mutableStateOf<String?>(null) }

    fun selectTab(i: Int) {
        if (i != tabIndex || stack.size != 1) {
            tabIndex = i
            stack.clear()
            stack.add(tabs[i].root)
        }
        runCatching { contentFocus.requestFocus() }
    }

    fun push(s: Screen) = stack.add(s)
    fun pop() {
        if (stack.size > 1) {
            val gone = stack.removeAt(stack.lastIndex)
            holder.removeState("${stack.size}:$gone")
        }
    }
    val play: (String) -> Unit = { ref -> push(Screen.Player(ref)) }

    // Back: pop a screen; on a tab's first screen, jump up to the tab bar; from there Back leaves the app.
    BackHandler(enabled = stack.size > 1) { pop() }
    BackHandler(enabled = stack.size == 1 && !navFocused) {
        runCatching { tabRequesters[tabIndex].requestFocus() }
    }

    val screen = stack.last()
    val showNav = stack.size == 1

    CompositionLocalProvider(LocalBackdrop provides backdrop, LocalBackdropSink provides { backdrop = it }) {
        Box(Modifier.fillMaxSize().background(HColors.Bg)) {
            Box(
                Modifier
                    .fillMaxSize()
                    .focusRequester(contentFocus)
                    .focusGroup(),
            ) {
                AnimatedContent(
                    targetState = stack.lastIndex to screen,
                    transitionSpec = {
                        (fadeIn(tween(320)) + scaleIn(tween(380), initialScale = 0.985f))
                            .togetherWith(fadeOut(tween(200)))
                    },
                    label = "screen",
                ) { (idx, sc) ->
                    val stateKey = "$idx:$sc"
                    // Each screen gets its own composition slot and saved state.
                    key(stateKey) {
                        holder.SaveableStateProvider(stateKey) {
                            ScreenContent(sc, config, settings, ::push, ::pop, play)
                        }
                    }
                }
            }
            AnimatedVisibility(
                visible = showNav,
                enter = fadeIn(tween(250)) + slideInVertically(tween(300)) { -it },
                exit = fadeOut(tween(200)) + slideOutVertically(tween(250)) { -it },
                modifier = Modifier.align(Alignment.TopCenter).padding(top = 18.dp),
            ) {
                TopNav(
                    tabs = tabs,
                    selected = tabIndex,
                    requesters = tabRequesters,
                    onSelect = { selectTab(it) },
                    onFocusChanged = { navFocused = it },
                    dotOn = if (updateAvailable) tabs.lastIndex else -1,
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
) {
    when (screen) {
        is Screen.Library -> LibraryScreen(
            svc = screen.svc,
            onOpen = { id -> push(Screen.Detail(screen.svc, id)) },
            onBrowse = { search -> push(Screen.Browse(screen.svc, search)) },
            onSeeAll = { key, title -> push(Screen.Category(screen.svc, key, title)) },
            onPlay = if (config.plex) play else null,
        )
        is Screen.Category -> CategoryScreen(
            svc = screen.svc,
            key = screen.key,
            title = screen.title,
            onOpen = { id -> push(Screen.Detail(screen.svc, id)) },
        )
        is Screen.Detail -> DetailScreen(
            svc = screen.svc,
            itemId = screen.itemId,
            onClose = pop,
            onOpenReleases = { arg, title -> push(Screen.Releases(screen.svc, screen.itemId, arg, title)) },
            onOpenId = { id -> push(Screen.Detail(screen.svc, id)) },
            onOpenItem = { ref ->
                // "kind/inst/id", e.g. radarr/radarr-truehd/123 — jump to that app's page
                val parts = ref.split("/", limit = 3)
                val target = config.services.firstOrNull { parts.size == 3 && it.kind == parts[0] && it.inst == parts[1] }
                if (target != null) push(Screen.Detail(target, parts[2]))
            },
            onPlay = play,
        )
        is Screen.Browse -> BrowseScreen(
            svc = screen.svc,
            searchFirst = screen.searchFirst,
            onOpen = { id -> push(Screen.Detail(screen.svc, id)) },
        )
        is Screen.Releases -> ReleasesScreen(
            svc = screen.svc,
            itemId = screen.itemId,
            arg = screen.arg,
            title = screen.title,
            onDone = pop,
        )
        is Screen.Player -> PlayerScreen(ref = screen.ref, onExit = pop)
        Screen.Downloads -> TopInset { DownloadsScreen() }
        Screen.Files -> TopInset { FilesScreen() }
        Screen.Settings -> TopInset { settings() }
    }
}

/** Leaves room for the floating tab bar on plain (non-artwork) screens. */
@Composable
private fun TopInset(content: @Composable () -> Unit) {
    val sink = LocalBackdropSink.current
    SideEffect { sink(null) }
    Box(Modifier.fillMaxSize().padding(top = 56.dp)) { content() }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun TopNav(
    tabs: List<Tab>,
    selected: Int,
    requesters: List<FocusRequester>,
    onSelect: (Int) -> Unit,
    onFocusChanged: (Boolean) -> Unit,
    dotOn: Int,
) {
    GlassPanel(
        Modifier
            .onFocusChanged { onFocusChanged(it.hasFocus) }
            // Entering the bar always lands on the current tab.
            .focusProperties { enter = { requesters[selected] } }
            .focusGroup(),
        shape = RoundedCornerShape(999.dp),
        tint = Color(0x66141418),
    ) {
        Row(Modifier.padding(5.dp), horizontalArrangement = Arrangement.spacedBy(2.dp),
            verticalAlignment = Alignment.CenterVertically) {
            tabs.forEachIndexed { i, tab ->
                FocusBox(
                    onClick = { onSelect(i) },
                    shape = RoundedCornerShape(999.dp),
                    focusedScale = 1.04f,
                    borderWidth = 0.dp,
                    focusRequester = requesters[i],
                ) { f ->
                    Box {
                        Row(
                            Modifier
                                .background(
                                    when {
                                        f -> HColors.Text
                                        i == selected -> Color.White.copy(alpha = 0.16f)
                                        else -> Color.Transparent
                                    },
                                )
                                .padding(horizontal = if (tab.icon != null) 11.dp else 15.dp, vertical = 7.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            val fg = when {
                                f -> Color(0xFF0A0A0B)
                                i == selected -> HColors.Text
                                else -> HColors.TextDim
                            }
                            if (tab.icon != null) {
                                Icon(tab.icon, contentDescription = tab.label, tint = fg, modifier = Modifier.size(17.dp))
                            } else {
                                Text(tab.label, color = fg, fontSize = 14.sp,
                                    fontWeight = if (f || i == selected) FontWeight.Bold else FontWeight.SemiBold, maxLines = 1)
                            }
                        }
                        if (i == dotOn) {
                            Box(
                                Modifier
                                    .align(Alignment.TopEnd)
                                    .padding(3.dp)
                                    .size(7.dp)
                                    .clip(CircleShape)
                                    .background(HColors.Warn),
                            )
                        }
                    }
                }
            }
        }
    }
}
