package au.matt.harbour.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.focusGroup
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
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
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.zIndex
import au.matt.harbour.R
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
    data class Releases(val svc: ServiceInfo, val itemId: String, val arg: String?, val title: String) : Screen
    data object Downloads : Screen
    data object Files : Screen
    data object Settings : Screen
}

private data class Tab(val label: String, val icon: String, val root: Screen)

private val RailCollapsed = 76.dp
private val RailExpanded = 228.dp

@Composable
fun HarbourApp(prefs: Prefs) {
    val context = LocalContext.current
    var url by remember { mutableStateOf(prefs.url) }
    var token by remember { mutableStateOf(prefs.token) }
    val gateway = remember(url, token) { if (url.isBlank()) null else Gateway(url, token) }
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
            toaster.show("Harbour ${it.versionName} is available — open Settings to install")
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
            ToastHost(toaster, Modifier.align(Alignment.BottomEnd))
        }
    }
}

@Composable
private fun MainShell(config: GatewayConfig, settings: @Composable () -> Unit, updateAvailable: Boolean) {
    val tabs = remember(config) {
        buildList {
            config.services.forEach { add(Tab(it.name, iconFor(it.icon ?: it.kind), Screen.Library(it))) }
            if (config.downloads) add(Tab("Downloads", iconFor("downloads"), Screen.Downloads))
            if (config.files) add(Tab("Files", iconFor("files"), Screen.Files))
            add(Tab("Settings", iconFor("settings"), Screen.Settings))
        }
    }
    var tabIndex by remember { mutableIntStateOf(0) }
    val stack = remember { mutableStateListOf(tabs[0].root) }
    val railRequesters = remember(tabs) { tabs.map { FocusRequester() } }
    var railFocused by remember { mutableStateOf(false) }
    val holder = rememberSaveableStateHolder()

    fun selectTab(i: Int) {
        if (i == tabIndex && stack.size == 1) return
        tabIndex = i
        stack.clear()
        stack.add(tabs[i].root)
    }

    fun push(s: Screen) = stack.add(s)
    fun pop() {
        if (stack.size > 1) {
            val gone = stack.removeAt(stack.lastIndex)
            holder.removeState("${stack.size}:$gone")
        }
    }

    // Back: pop a screen; on a tab's first screen, jump to the menu (like Netflix);
    // from the menu, Back leaves the app.
    BackHandler(enabled = stack.size > 1) { pop() }
    BackHandler(enabled = stack.size == 1 && !railFocused) {
        runCatching { railRequesters[tabIndex].requestFocus() }
    }

    Box(Modifier.fillMaxSize()) {
        Box(
            Modifier
                .fillMaxSize()
                .padding(start = RailCollapsed),
        ) {
            val index = stack.lastIndex
            val screen = stack.last()
            val stateKey = "$index:$screen"
            // key() gives each screen its own slot in the composition, so switching tabs
            // tears the old screen down completely instead of re-using its pieces.
            key(stateKey) {
                holder.SaveableStateProvider(stateKey) {
                    when (screen) {
                        is Screen.Library -> LibraryScreen(
                            svc = screen.svc,
                            onOpen = { id -> push(Screen.Detail(screen.svc, id)) },
                            onBrowse = { search -> push(Screen.Browse(screen.svc, search)) },
                        )
                        is Screen.Detail -> DetailScreen(
                            svc = screen.svc,
                            itemId = screen.itemId,
                            onClose = { pop() },
                            onOpenReleases = { arg, title -> push(Screen.Releases(screen.svc, screen.itemId, arg, title)) },
                            onOpenItem = { ref ->
                                // "kind/inst/id", e.g. radarr/radarr-truehd/123 — jump to that app's page
                                val parts = ref.split("/", limit = 3)
                                val target = config.services.firstOrNull {
                                    parts.size == 3 && it.kind == parts[0] && it.inst == parts[1]
                                }
                                if (target != null) push(Screen.Detail(target, parts[2]))
                            },
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
                            onDone = { pop() },
                        )
                        Screen.Downloads -> DownloadsScreen()
                        Screen.Files -> FilesScreen()
                        Screen.Settings -> settings()
                    }
                }
            }
        }
        NavRail(
            title = config.name,
            tabs = tabs,
            selected = tabIndex,
            requesters = railRequesters,
            onSelect = { selectTab(it) },
            onFocusChanged = { railFocused = it },
            dotOn = if (updateAvailable) tabs.lastIndex else -1,
            modifier = Modifier.zIndex(2f),
        )
    }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun NavRail(
    title: String,
    tabs: List<Tab>,
    selected: Int,
    requesters: List<FocusRequester>,
    onSelect: (Int) -> Unit,
    onFocusChanged: (Boolean) -> Unit,
    dotOn: Int,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(false) }
    val width by animateDpAsState(if (expanded) RailExpanded else RailCollapsed, label = "rail")
    Column(
        modifier
            .fillMaxHeight()
            .width(width)
            .background(
                Brush.horizontalGradient(
                    0f to Color.Black.copy(alpha = 0.95f),
                    1f to Color.Black.copy(alpha = if (expanded) 0.88f else 0.55f),
                ),
            )
            .onFocusChanged {
                expanded = it.hasFocus
                onFocusChanged(it.hasFocus)
            }
            // Entering the menu always lands on the current tab.
            .focusProperties { enter = { requesters[selected] } }
            .focusGroup()
            .padding(horizontal = 12.dp, vertical = 30.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(
            Modifier
                .height(48.dp)
                .padding(start = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Image(
                painter = painterResource(R.drawable.logo),
                contentDescription = title,
                modifier = Modifier
                    .size(48.dp)
                    .clip(RoundedCornerShape(12.dp)),
            )
            if (expanded) {
                Spacer(Modifier.width(12.dp))
                Text(title, color = HColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Bold, maxLines = 1)
            }
        }
        Spacer(Modifier.height(18.dp))
        tabs.forEachIndexed { i, tab ->
            FocusBox(
                onClick = { onSelect(i) },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp),
                shape = RoundedCornerShape(12.dp),
                focusedScale = 1f,
                borderWidth = 0.dp,
                focusRequester = requesters[i],
            ) { f ->
                Row(
                    Modifier
                        .fillMaxSize()
                        .background(if (f) Color.White else Color.Transparent)
                        .padding(horizontal = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(tab.icon, fontSize = 20.sp)
                    if (expanded) {
                        Spacer(Modifier.width(14.dp))
                        Text(
                            tab.label,
                            color = when {
                                f -> Color.Black
                                i == selected -> HColors.Text
                                else -> HColors.TextDim
                            },
                            fontSize = 16.sp,
                            fontWeight = if (i == selected) FontWeight.Bold else FontWeight.Medium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                if (i == selected && !f) {
                    Box(
                        Modifier
                            .align(Alignment.CenterStart)
                            .width(3.dp)
                            .height(22.dp)
                            .background(HColors.Accent),
                    )
                }
                if (i == dotOn) {
                    Box(
                        Modifier
                            .align(Alignment.TopEnd)
                            .padding(6.dp)
                            .size(9.dp)
                            .clip(CircleShape)
                            .background(HColors.Warn),
                    )
                }
            }
        }
    }
}
