package au.matt.harbour.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.Gateway
import au.matt.harbour.data.UpdateInfo
import au.matt.harbour.data.Updater
import au.matt.harbour.data.config
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(
    initialUrl: String,
    initialToken: String,
    firstRun: Boolean,
    onSaved: (String, String) -> Unit,
    repo: String = "StompDonkeys/harbour",
    update: UpdateInfo? = null,
    onUpdateFound: (UpdateInfo?) -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf(initialUrl.ifBlank { "http://192.168.4.68:8765" }) }
    var token by remember { mutableStateOf(initialToken) }
    var editing by remember { mutableStateOf<String?>(null) }
    var status by remember { mutableStateOf<Pair<Boolean, String>?>(null) }
    var testing by remember { mutableStateOf(false) }
    var updStatus by remember { mutableStateOf<String?>(null) }
    var updBusy by remember { mutableStateOf(false) }
    val first = remember { FocusRequester() }
    val version = remember { Updater.installedVersionName(context) }

    val installUpdate: (UpdateInfo) -> Unit = { info ->
        if (!Updater.canInstall(context)) {
            updStatus = "First allow Harbour to install updates: switch it on in the screen that just opened, " +
                "then press Back and choose Install again."
            if (!Updater.openInstallPermission(context)) {
                updStatus = "Allow Harbour to install updates: Settings → My Fire TV → Developer options → " +
                    "Install unknown apps → Harbour → On. Then choose Install again."
            }
        } else if (!updBusy) {
            updBusy = true
            scope.launch {
                try {
                    val apk = Updater.download(context, info) { p -> updStatus = "Downloading… ${(p * 100).toInt()}%" }
                    updStatus = "Installing — confirm on the next screen."
                    Updater.install(context, apk)
                } catch (e: Exception) {
                    updStatus = "⚠ ${e.message}"
                } finally {
                    updBusy = false
                }
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(start = 56.dp, top = 40.dp, end = 56.dp, bottom = 30.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(if (firstRun) "Welcome to Harbour" else "Settings",
            color = HColors.Text, fontSize = 30.sp, fontWeight = FontWeight.Bold)
        Text(
            "Harbour talks to the small gateway container on your Synology, which holds the API keys " +
                "for your apps. The access token is the \"token\" in its config.json — a short PIN is fine.",
            color = HColors.TextDim, fontSize = 15.sp, modifier = Modifier.widthIn(max = 640.dp),
        )
        Spacer(Modifier.height(6.dp))
        SettingRow("Gateway address", url, first) { editing = "url" }
        SettingRow("Access token", if (token.isBlank()) "Not set" else "•".repeat(token.length.coerceAtMost(16)), null) {
            editing = "token"
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            TvButton(if (testing) "Testing…" else "Test & save", {
                if (!testing) {
                    testing = true
                    status = null
                    scope.launch {
                        try {
                            val cfg = Gateway(url, token).config()
                            val parts = cfg.services.map { it.name } +
                                (if (cfg.downloads) listOf("Downloads") else emptyList()) +
                                (if (cfg.files) listOf("Files") else emptyList())
                            status = true to "Connected — ${parts.joinToString(", ")}"
                            onSaved(url.trim(), token.trim())
                        } catch (e: Exception) {
                            status = false to (e.message ?: "Failed")
                        } finally {
                            testing = false
                        }
                    }
                }
            }, style = "primary")
        }
        status?.let { (ok, msg) ->
            Text((if (ok) "✓ " else "✗ ") + msg, color = if (ok) HColors.Ok else HColors.Danger, fontSize = 15.sp)
        }

        if (!firstRun) {
            Spacer(Modifier.height(10.dp))
            Text("Updates", color = HColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Text(
                if (update != null) "Harbour ${update.versionName} is available (you have $version)."
                else "You have Harbour $version.",
                color = if (update != null) HColors.Accent else HColors.TextDim, fontSize = 15.sp,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (update != null) {
                    TvButton(if (updBusy) "Working…" else "Install ${update.versionName}", { installUpdate(update) },
                        style = "primary")
                }
                TvButton("Check for updates", {
                    updStatus = "Checking GitHub…"
                    scope.launch {
                        try {
                            val found = Updater.check(context, repo)
                            onUpdateFound(found)
                            updStatus = if (found == null) "You're on the latest build." else null
                        } catch (e: Exception) {
                            updStatus = "⚠ ${e.message}"
                        }
                    }
                })
            }
            updStatus?.let {
                Text(it, color = HColors.TextDim, fontSize = 14.sp, modifier = Modifier.widthIn(max = 640.dp))
            }
        }
    }

    when (editing) {
        "url" -> TextInputDialog(
            title = "Gateway address (e.g. http://192.168.4.68:8765)",
            initial = url,
            imeAction = ImeAction.Done,
            onDone = {
                url = it.trim()
                editing = null
            },
            onDismiss = { editing = null },
        )
        "token" -> TextInputDialog(
            title = "Access token (the \"token\" in config.json)",
            initial = token,
            password = true,
            onDone = {
                token = it.trim()
                editing = null
            },
            onDismiss = { editing = null },
        )
    }

    LaunchedEffect(Unit) { runCatching { first.requestFocus() } }
}

@Composable
private fun SettingRow(label: String, value: String, focusRequester: FocusRequester?, onClick: () -> Unit) {
    FocusBox(
        onClick = onClick,
        modifier = Modifier.widthIn(min = 520.dp, max = 640.dp),
        focusedScale = 1.02f,
        borderWidth = 2.dp,
        focusRequester = focusRequester,
    ) { f ->
        Row(
            Modifier
                .background(if (f) HColors.SurfaceHi else HColors.Surface)
                .padding(horizontal = 20.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(label, color = HColors.TextDim, fontSize = 15.sp, modifier = Modifier.width(170.dp))
            Text(value, color = HColors.Text, fontSize = 16.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f))
            Text("Edit", color = if (f) HColors.Accent else HColors.TextDim, fontSize = 13.sp)
        }
    }
}
