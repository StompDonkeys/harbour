package au.matt.harbour.ui

import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import au.matt.harbour.R

/** Apple TV–style dark glass palette: near-black ground, white text, colour only where it means something. */
object HColors {
    val Bg = Color(0xFF050506)
    val Surface = Color(0xFF16161A)
    val SurfaceHi = Color(0xFF26262C)
    val Text = Color(0xFFF5F5F7)
    val TextDim = Color(0xA6EBEBF5)          // ~65% white
    val TextFaint = Color(0x73EBEBF5)        // ~45% white
    val Accent = Color(0xFFF5F5F7)
    val Ok = Color(0xFF34C759)
    val Warn = Color(0xFFFF9F0A)
    val Info = Color(0xFF0A84FF)
    val Danger = Color(0xFFFF453A)
    val Muted = Color(0xFF636366)

    // glass
    val GlassFill = Color(0x1FFFFFFF)        // 12% white
    val GlassStrong = Color(0x3DFFFFFF)      // 24% white
    val GlassEdge = Color(0x29FFFFFF)        // 16% white
    val GlassDark = Color(0x8C141418)        // tinted panel over blurred art
}

val Figtree = FontFamily(
    Font(R.font.figtree_regular, FontWeight.Normal),
    Font(R.font.figtree_medium, FontWeight.Medium),
    Font(R.font.figtree_semibold, FontWeight.SemiBold),
    Font(R.font.figtree_bold, FontWeight.Bold),
    Font(R.font.figtree_extrabold, FontWeight.ExtraBold),
)

fun toneColor(tone: String?): Color = when (tone) {
    "ok" -> HColors.Ok
    "warn" -> HColors.Warn
    "info" -> HColors.Info
    "danger" -> HColors.Danger
    else -> HColors.Muted
}

/** Kept for screens that still show a symbol beside a label. */
fun iconFor(icon: String?): String = when (icon) {
    "home" -> "⌂"
    "movie" -> "▶"
    "4k" -> "4K"
    "tv" -> "▭"
    "discover" -> "✦"
    "files" -> "▤"
    "downloads" -> "↓"
    "settings" -> "⚙"
    else -> "•"
}

@Composable
fun HarbourTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = HColors.Text,
            onPrimary = Color.Black,
            background = HColors.Bg,
            onBackground = HColors.Text,
            surface = HColors.Surface,
            onSurface = HColors.Text,
            surfaceVariant = HColors.SurfaceHi,
            onSurfaceVariant = HColors.TextDim,
        ),
    ) {
        ProvideTextStyle(LocalTextStyle.current.copy(fontFamily = Figtree, color = HColors.Text), content)
    }
}
