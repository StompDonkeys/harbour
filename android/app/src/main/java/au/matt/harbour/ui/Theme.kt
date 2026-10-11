package au.matt.harbour.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object HColors {
    val Bg = Color(0xFF0B0D10)
    val Surface = Color(0xFF16191E)
    val SurfaceHi = Color(0xFF242932)
    val Text = Color(0xFFF2F4F7)
    val TextDim = Color(0xFFA3AAB5)
    val Accent = Color(0xFF2FB6A0)
    val Ok = Color(0xFF2E9E5B)
    val Warn = Color(0xFFD68A1F)
    val Info = Color(0xFF3B7FD0)
    val Danger = Color(0xFFE5484D)
    val Muted = Color(0xFF5B6270)
}

fun toneColor(tone: String?): Color = when (tone) {
    "ok" -> HColors.Ok
    "warn" -> HColors.Warn
    "info" -> HColors.Info
    "danger" -> HColors.Danger
    else -> HColors.Muted
}

fun iconFor(icon: String?): String = when (icon) {
    "home" -> "🏠"
    "movie" -> "🎬"
    "4k" -> "🎞️"
    "tv" -> "📺"
    "discover" -> "✨"
    "files" -> "📁"
    "downloads" -> "⬇️"
    "settings" -> "⚙️"
    else -> "▪️"
}

@Composable
fun HarbourTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = HColors.Accent,
            onPrimary = Color.Black,
            background = HColors.Bg,
            onBackground = HColors.Text,
            surface = HColors.Surface,
            onSurface = HColors.Text,
            surfaceVariant = HColors.SurfaceHi,
            onSurfaceVariant = HColors.TextDim,
        ),
        content = content,
    )
}
