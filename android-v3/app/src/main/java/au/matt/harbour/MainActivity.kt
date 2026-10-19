package au.matt.harbour

import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import au.matt.harbour.data.CrashReporter
import au.matt.harbour.data.Prefs
import au.matt.harbour.rec.ScreenRecorder
import au.matt.harbour.ui.HarbourApp
import au.matt.harbour.ui.HarbourTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        CrashReporter.install(this)
        val prefs = Prefs(applicationContext)
        setContent {
            HarbourTheme {
                HarbourApp(prefs)
            }
        }
    }

    /** While recording a demo, ☰ (Menu) on the remote stops it, from anywhere in the app. */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (ScreenRecorder.isRecording && event.keyCode == KeyEvent.KEYCODE_MENU) {
            if (event.action == KeyEvent.ACTION_UP) ScreenRecorder.stop(this)
            return true
        }
        return super.dispatchKeyEvent(event)
    }
}
