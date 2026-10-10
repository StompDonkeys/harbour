package au.matt.harbour

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import au.matt.harbour.data.Prefs
import au.matt.harbour.ui.HarbourApp
import au.matt.harbour.ui.HarbourTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(applicationContext)
        setContent {
            HarbourTheme {
                HarbourApp(prefs)
            }
        }
    }
}
