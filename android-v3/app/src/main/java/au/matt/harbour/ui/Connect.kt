package au.matt.harbour.ui

import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.data.Discovery
import au.matt.harbour.data.Gateway
import au.matt.harbour.data.pairStart
import au.matt.harbour.data.pairStatus
import kotlinx.coroutines.delay

private sealed interface ConnectState {
    data object Idle : ConnectState
    data object Searching : ConnectState
    data class Found(val list: List<Discovery.Found>) : ConnectState
    data class Pairing(val gw: Discovery.Found, val code: String) : ConnectState
    data class Failed(val message: String) : ConnectState
}

/**
 * The easy way in: find the gateway on the network, show a code, approve it on the setup page.
 * No addresses or keys typed on a remote.
 */
@Composable
fun ConnectPanel(autoStart: Boolean, currentUrl: String, onConnected: (url: String, token: String) -> Unit) {
    val context = LocalContext.current
    var state by remember { mutableStateOf<ConnectState>(if (autoStart) ConnectState.Searching else ConnectState.Idle) }
    var searchRun by remember { mutableIntStateOf(if (autoStart) 1 else 0) }
    var pairWith by remember { mutableStateOf<Discovery.Found?>(null) }
    val firstBtn = remember { FocusRequester() }
    val deviceName = remember {
        runCatching { android.provider.Settings.Global.getString(context.contentResolver, "device_name") }.getOrNull()
            ?.takeIf { it.isNotBlank() } ?: Build.MODEL ?: "Harbour TV"
    }

    LaunchedEffect(searchRun) {
        if (searchRun == 0) return@LaunchedEffect
        state = ConnectState.Searching
        val hint = currentUrl.removePrefix("http://").removePrefix("https://").substringBefore(":").substringBefore("/")
            .ifBlank { null }
        val found = runCatching { Discovery.scan(hint) }.getOrDefault(emptyList())
        state = when {
            found.isEmpty() -> ConnectState.Failed("No Harbour gateway answered on this network. Check it's running " +
                "(Container Manager), or enter its address below.")
            found.size == 1 && found[0].ready && autoStart -> {
                pairWith = found[0]
                ConnectState.Found(found)
            }
            else -> ConnectState.Found(found)
        }
    }

    // Pairing: ask for a code, then wait for approval on the setup page.
    LaunchedEffect(pairWith) {
        val g = pairWith ?: return@LaunchedEffect
        try {
            val api = Gateway(g.url, "")
            val p = api.pairStart(deviceName)
            state = ConnectState.Pairing(g, p.code)
            val until = System.currentTimeMillis() + p.expires * 1000L
            while (System.currentTimeMillis() < until) {
                delay(2000)
                val s = runCatching { api.pairStatus(p.id) }.getOrNull() ?: continue
                when (s.status) {
                    "approved" -> {
                        val tok = s.token
                        if (tok != null) {
                            onConnected(g.url, tok)
                            return@LaunchedEffect
                        }
                    }
                    "denied" -> {
                        state = ConnectState.Failed("Pairing was declined on the setup page.")
                        pairWith = null
                        return@LaunchedEffect
                    }
                    "expired" -> break
                }
            }
            state = ConnectState.Failed("The code expired. Try pairing again.")
        } catch (e: Exception) {
            state = ConnectState.Failed(e.message ?: "Couldn't start pairing")
        }
        pairWith = null
    }

    GlassPanel(Modifier.widthIn(max = 720.dp), shape = RoundedCornerShape(20.dp)) {
        Column(Modifier.padding(22.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            when (val st = state) {
                ConnectState.Idle -> {
                    Text("Connect to your gateway", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    Text("Find the gateway on your network and pair this TV with a code — no typing keys.",
                        color = HColors.TextDim, fontSize = 14.sp)
                    TvButton("Find and pair", { searchRun++ }, style = "primary", focusRequester = firstBtn)
                }
                ConnectState.Searching -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(color = HColors.Text, strokeWidth = 2.5.dp, modifier = Modifier.size(24.dp))
                    Spacer(Modifier.width(14.dp))
                    Text("Looking for your Harbour gateway on this network…", fontSize = 16.sp)
                }
                is ConnectState.Found -> {
                    Text(if (st.list.size == 1) "Found your gateway" else "Found ${st.list.size} gateways",
                        fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    st.list.forEachIndexed { i, g ->
                        if (g.ready) {
                            TvButton("Pair with ${g.host}  ·  gateway ${g.version}", { pairWith = g },
                                style = "primary", focusRequester = if (i == 0) firstBtn else null)
                        } else {
                            Text("${g.host} isn't set up yet — on your phone or computer open  ${g.url}/setup  " +
                                "to set it up, then search again.", color = HColors.Warn, fontSize = 14.sp)
                        }
                    }
                    TvButton("Search again", { searchRun++ })
                }
                is ConnectState.Pairing -> {
                    Text("Approve this TV", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    Text(st.code, fontSize = 56.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = 10.sp,
                        fontFamily = FontFamily.Monospace, color = HColors.Info)
                    Text("On your phone or computer, open  ${st.gw.url}/setup  and approve the device showing this code.",
                        fontSize = 15.sp, color = HColors.Text)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(color = HColors.TextDim, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(10.dp))
                        Text("Waiting for approval…", color = HColors.TextDim, fontSize = 13.sp)
                    }
                }
                is ConnectState.Failed -> {
                    Text(st.message, color = HColors.Warn, fontSize = 15.sp)
                    TvButton("Search again", { searchRun++ }, style = "primary", focusRequester = firstBtn)
                }
            }
        }
    }
    val settled = state is ConnectState.Found || state is ConnectState.Failed
    LaunchedEffect(settled) {
        if (settled && (autoStart || searchRun > 0)) {
            delay(80)
            runCatching { firstBtn.requestFocus() }
        }
    }
    Spacer(Modifier.height(4.dp))
}
