package au.matt.harbour.ui

import android.app.Activity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import au.matt.harbour.rec.ScreenRecorder
import au.matt.harbour.rec.ScreenRecorder.State
import kotlinx.coroutines.delay

/** Settings → Demo recording. */
@Composable
fun RecordingSettings() {
    val context = LocalContext.current
    val toaster = LocalToaster.current
    val st = ScreenRecorder.state.value
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        val data = res.data
        if (res.resultCode == Activity.RESULT_OK && data != null) {
            ScreenRecorder.start(context, res.resultCode, data)
        } else {
            toaster.show("Recording cancelled")
        }
    }
    Text("Demo recording", color = HColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
    Text(
        "Records the screen while you use Harbour. Press ☰ (Menu) on the remote to stop — the video " +
            "is saved to your NAS. Video only, no sound.",
        color = HColors.TextDim, fontSize = 13.sp, modifier = Modifier.widthIn(max = 640.dp),
    )
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
        when (st) {
            is State.Recording, is State.Countdown ->
                TvButton("Stop & save recording", { ScreenRecorder.stop(context) }, style = "danger")
            State.Saving -> TvButton("Saving to NAS…", {})
            else -> TvButton("Start recording", {
                runCatching { ask.launch(ScreenRecorder.captureIntent(context)) }
                    .onFailure { toaster.show("⚠ This device doesn't allow screen recording") }
            }, style = "primary")
        }
        if (st is State.Failed && st.canRetry) {
            TvButton("Try saving again", { ScreenRecorder.retry(context) })
        }
    }
    when (st) {
        is State.Saved -> Text("✓ Saved to ${st.path}", color = HColors.Ok, fontSize = 14.sp)
        is State.Failed -> Text("⚠ ${st.message}", color = HColors.Warn, fontSize = 14.sp,
            modifier = Modifier.widthIn(max = 640.dp))
        else -> {}
    }
}

/** Countdown, a small REC dot while recording, and the saving / saved messages. Shown over everything. */
@Composable
fun BoxScope.RecordingOverlay() {
    val toaster = LocalToaster.current
    val st = ScreenRecorder.state.value
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(st) {
        while (st is State.Countdown || st is State.Recording) {
            now = System.currentTimeMillis()
            delay(200)
        }
    }
    LaunchedEffect(st) {
        when (st) {
            is State.Saved -> toaster.show("Recording saved to ${st.path}")
            is State.Failed -> toaster.show("⚠ ${st.message}")
            else -> {}
        }
    }
    when (st) {
        is State.Countdown -> {
            val n = (((st.startsAt - now) + 999) / 1000).coerceIn(1, 3)
            Box(Modifier.align(Alignment.Center).size(150.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.7f)),
                contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("$n", fontSize = 64.sp, fontWeight = FontWeight.ExtraBold, color = Color.White)
                    Text("Recording starts", fontSize = 12.sp, color = HColors.TextDim)
                }
            }
        }
        is State.Recording -> {
            val secs = ((now - st.since) / 1000).coerceAtLeast(0)
            // A bold label for the first few seconds, then just a small dot so the demo stays clean.
            Row(
                Modifier.align(Alignment.TopEnd).padding(14.dp)
                    .clip(RoundedCornerShape(50)).background(Color.Black.copy(alpha = if (secs < 4) 0.55f else 0f))
                    .padding(horizontal = if (secs < 4) 10.dp else 0.dp, vertical = 5.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(Modifier.size(if (secs < 4) 10.dp else 8.dp).clip(CircleShape)
                    .background(HColors.Danger.copy(alpha = if (secs < 4) 1f else 0.7f)))
                if (secs < 4) {
                    Spacer(Modifier.width(7.dp))
                    Text("REC · ☰ to stop", fontSize = 12.sp, fontWeight = FontWeight.Bold, color = Color.White)
                }
            }
        }
        State.Saving -> {
            Text("Saving recording to your NAS…", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = Color.White,
                modifier = Modifier.align(Alignment.TopEnd).padding(14.dp).clip(RoundedCornerShape(50))
                    .background(Color.Black.copy(alpha = 0.7f)).padding(horizontal = 12.dp, vertical = 6.dp))
        }
        else -> {}
    }
}
