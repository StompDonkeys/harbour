package au.matt.harbour.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.zIndex
import au.matt.harbour.data.Gateway
import au.matt.harbour.data.HomePage
import au.matt.harbour.data.MediaItem
import au.matt.harbour.data.ServiceInfo
import coil.compose.AsyncImage
import kotlinx.coroutines.delay

// ------------------------------------------------------------------ app-wide state

val LocalGateway = staticCompositionLocalOf<Gateway> { error("No gateway") }
val LocalToaster = staticCompositionLocalOf { Toaster() }
val LocalCache = staticCompositionLocalOf { ScreenCache() }

/** Keeps loaded pages around so Back doesn't refetch and lose your place. */
class ScreenCache {
    val homes = mutableMapOf<String, HomePage>()
    val browse = mutableMapOf<String, List<MediaItem>>()
    fun invalidate(s: ServiceInfo) {
        homes.remove(s.key)
        browse.remove(s.key)
    }
}

class Toaster {
    var text by mutableStateOf<String?>(null)
    var serial by mutableIntStateOf(0)
    fun show(t: String) {
        text = t
        serial++
    }
}

@Composable
fun ToastHost(toaster: Toaster, modifier: Modifier = Modifier) {
    var last by remember { mutableStateOf("") }
    val text = toaster.text
    if (text != null) last = text
    LaunchedEffect(toaster.serial) {
        if (toaster.text != null) {
            delay(3500)
            toaster.text = null
        }
    }
    AnimatedVisibility(
        visible = text != null,
        enter = fadeIn(tween(220)) + slideInVertically(tween(320)) { it / 2 },
        exit = fadeOut(tween(220)) + slideOutVertically(tween(260)) { it / 2 },
        modifier = modifier,
    ) {
        FrostPanel(Modifier.padding(28.dp).widthIn(max = 520.dp), shape = RoundedCornerShape(999.dp)) {
            Text(last, fontSize = 15.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(horizontal = 22.dp, vertical = 12.dp))
        }
    }
}

// ------------------------------------------------------------------ focus

private val LiftSpring = spring<Float>(dampingRatio = 0.72f, stiffness = Spring.StiffnessMediumLow)

/**
 * The core TV building block. Focused: it lifts (springy scale), casts a deep shadow and gets a
 * white edge — the Apple TV "parallax lift". OK clicks it; ☰ fires [onMenu].
 */
@Composable
fun FocusBox(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    shape: Shape = RoundedCornerShape(12.dp),
    focusedScale: Float = 1.08f,
    borderWidth: Dp = 2.dp,
    onFocused: (() -> Unit)? = null,
    onMenu: (() -> Unit)? = null,
    focusRequester: FocusRequester? = null,
    content: @Composable BoxScope.(focused: Boolean) -> Unit,
) {
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val scale by animateFloatAsState(if (focused) focusedScale else 1f, LiftSpring, label = "lift")
    val shadow by animateDpAsState(if (focused && focusedScale > 1.01f) 22.dp else 0.dp, tween(220), label = "shadow")
    val currentOnFocused by rememberUpdatedState(onFocused)
    val currentOnMenu by rememberUpdatedState(onMenu)
    LaunchedEffect(focused) { if (focused) currentOnFocused?.invoke() }

    var m = modifier
        .zIndex(if (focused) 1f else 0f)
        .graphicsLayer {
            scaleX = scale
            scaleY = scale
        }
    if (onMenu != null) {
        m = m.onPreviewKeyEvent { e ->
            if (e.key == Key.Menu) {
                if (e.type == KeyEventType.KeyUp) currentOnMenu?.invoke()
                true
            } else false
        }
    }
    if (focusRequester != null) m = m.focusRequester(focusRequester)
    Box(
        m.shadow(shadow, shape, clip = false, ambientColor = Color.Black, spotColor = Color.Black)
            .clip(shape)
            .border(borderWidth, if (focused && borderWidth > 0.dp) Color(0xE6FFFFFF) else Color.Transparent, shape)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick),
    ) {
        content(focused)
    }
}

/** Diagonal highlight that slides onto focused artwork, like light catching glass. */
@Composable
fun BoxScope.Sheen(focused: Boolean) {
    val a by animateFloatAsState(if (focused) 1f else 0f, tween(260), label = "sheen")
    if (a > 0f) {
        Box(
            Modifier
                .matchParentSize()
                .graphicsLayer { alpha = a }
                .background(
                    Brush.linearGradient(
                        0f to Color.White.copy(alpha = 0.24f),
                        0.38f to Color.White.copy(alpha = 0f),
                    ),
                ),
        )
    }
}

/**
 * A button. Unfocused it's frosted glass; focused it turns solid white with dark text and lifts —
 * the tvOS look. `style`: "primary" (brighter glass), "danger" (red when focused).
 */
@Composable
fun TvButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    style: String? = null,
    focusRequester: FocusRequester? = null,
    onFocused: (() -> Unit)? = null,
    fill: Boolean = false,
    icon: ImageVector? = null,
) {
    val shape = RoundedCornerShape(14.dp)
    FocusBox(
        onClick = onClick,
        modifier = modifier,
        shape = shape,
        focusedScale = 1.06f,
        borderWidth = 0.dp,
        focusRequester = focusRequester,
        onFocused = onFocused,
    ) { f ->
        val bg = when {
            f && style == "danger" -> HColors.Danger
            f -> HColors.Text
            style == "danger" -> HColors.Danger.copy(alpha = 0.22f)
            style == "primary" -> HColors.GlassStrong
            else -> HColors.GlassFill
        }
        val fg = when {
            f && style == "danger" -> Color.White
            f -> Color(0xFF0A0A0B)
            else -> HColors.Text
        }
        Row(
            (if (fill) Modifier.fillMaxWidth() else Modifier)
                .background(bg)
                .border(1.dp, if (f) Color.Transparent else HColors.GlassEdge, shape)
                .padding(horizontal = if (text.isEmpty()) 12.dp else 20.dp, vertical = 11.dp),
            horizontalArrangement = if (fill) Arrangement.Center else Arrangement.Start,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (icon != null) {
                Icon(icon, contentDescription = text.ifEmpty { null }, tint = fg, modifier = Modifier.size(18.dp))
                if (text.isNotEmpty()) Spacer(Modifier.width(8.dp))
            }
            if (text.isNotEmpty()) {
                Text(text, color = fg, fontSize = 15.sp, fontWeight = if (f) FontWeight.Bold else FontWeight.SemiBold,
                    maxLines = 1, textAlign = if (fill) TextAlign.Center else null)
            }
        }
    }
}

// ------------------------------------------------------------------ media bits

@Composable
fun Pill(text: String, tone: String?, modifier: Modifier = Modifier) {
    val outline = tone == null || tone == "muted"
    Text(
        text.uppercase(),
        color = if (outline) HColors.Text else Color.White,
        fontSize = 10.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.6.sp,
        maxLines = 1,
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(if (outline) Color.Black.copy(alpha = 0.45f) else toneColor(tone).copy(alpha = 0.92f))
            .border(1.dp, if (outline) HColors.GlassEdge else Color.Transparent, RoundedCornerShape(6.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/** An outlined format tag like 4K / DOLBY VISION / ATMOS. */
@Composable
fun FormatTag(text: String) {
    Text(
        text,
        color = HColors.Text,
        fontSize = 10.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.6.sp,
        modifier = Modifier
            .border(1.dp, HColors.Text.copy(alpha = 0.45f), RoundedCornerShape(4.dp))
            .padding(horizontal = 5.dp, vertical = 1.dp),
    )
}

@Composable
fun ProgressStrip(progress: Float, modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxWidth()
            .padding(horizontal = 10.dp, vertical = 8.dp)
            .height(3.dp)
            .clip(RoundedCornerShape(3.dp))
            .background(Color.White.copy(alpha = 0.28f)),
    ) {
        Box(
            Modifier
                .fillMaxWidth(progress.coerceIn(0f, 1f))
                .fillMaxHeight()
                .background(HColors.Text),
        )
    }
}

@Composable
fun PosterCard(
    item: MediaItem,
    onClick: () -> Unit,
    onFocused: () -> Unit,
    modifier: Modifier = Modifier,
    width: Dp = 104.dp,
    focusRequester: FocusRequester? = null,
    showBadge: Boolean = true,
) {
    val gw = LocalGateway.current
    FocusBox(
        onClick = onClick,
        modifier = modifier
            .width(width)
            .aspectRatio(2f / 3f),
        onFocused = onFocused,
        focusRequester = focusRequester,
    ) { f ->
        Box(
            Modifier
                .fillMaxSize()
                .background(HColors.Surface),
        ) {
            val poster = gw.img(item.poster)
            if (poster != null) {
                AsyncImage(model = poster, contentDescription = item.title, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize())
            } else {
                Text(item.title, color = HColors.TextDim, fontSize = 13.sp, textAlign = TextAlign.Center,
                    modifier = Modifier.align(Alignment.Center).padding(8.dp))
            }
            if (!f) Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = 0.12f)))
            Sheen(f)
            if (showBadge && item.badge != null) {
                Pill(item.badge, item.badgeTone, Modifier.align(Alignment.TopStart).padding(6.dp))
            }
            if (item.progress != null) ProgressStrip(item.progress, Modifier.align(Alignment.BottomCenter))
        }
    }
}

/** 16:9 card with its title underneath (tvOS style). */
@Composable
fun LandscapeCard(
    item: MediaItem,
    onClick: () -> Unit,
    onFocused: () -> Unit,
    modifier: Modifier = Modifier,
    width: Dp = 208.dp,
    focusRequester: FocusRequester? = null,
) {
    val gw = LocalGateway.current
    var focused by remember { mutableStateOf(false) }
    Column(modifier.width(width)) {
        FocusBox(
            onClick = onClick,
            modifier = Modifier
                .width(width)
                .aspectRatio(16f / 9f),
            shape = RoundedCornerShape(14.dp),
            focusedScale = 1.07f,
            onFocused = {
                focused = true
                onFocused()
            },
            focusRequester = focusRequester,
        ) { f ->
            LaunchedEffect(f) { if (!f) focused = false }
            Box(
                Modifier
                    .fillMaxSize()
                    .background(HColors.Surface),
            ) {
                val img = gw.img(item.backdrop ?: item.poster)
                if (img != null) {
                    AsyncImage(model = img, contentDescription = item.title, contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize())
                }
                if (!f) Box(Modifier.matchParentSize().background(Color.Black.copy(alpha = 0.14f)))
                Sheen(f)
                if (item.badge != null) {
                    Pill(item.badge, item.badgeTone, Modifier.align(Alignment.TopStart).padding(8.dp))
                }
                if (item.progress != null) ProgressStrip(item.progress, Modifier.align(Alignment.BottomCenter))
            }
        }
        Spacer(Modifier.height(if (focused) 12.dp else 8.dp))
        Text(item.title, fontSize = 14.sp, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis,
            color = if (focused) HColors.Text else HColors.Text.copy(alpha = 0.86f),
            modifier = Modifier.padding(start = 2.dp))
        if (item.subtitle != null) {
            Text(item.subtitle, fontSize = 12.sp, color = HColors.TextDim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(start = 2.dp))
        }
    }
}

/** Full-bleed artwork with the left and bottom edges darkened so text reads. Cross-fades between titles. */
@Composable
fun Backdrop(url: String?, modifier: Modifier = Modifier, dim: Float = 1f) {
    val sink = LocalBackdropSink.current
    SideEffect { sink(url) }
    Box(modifier.fillMaxSize().background(HColors.Bg)) {
        Crossfade(targetState = url, animationSpec = tween(700), label = "backdrop") { u ->
            if (u != null) {
                AsyncImage(model = u, contentDescription = null, contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize())
            }
        }
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.horizontalGradient(
                        0f to HColors.Bg.copy(alpha = 0.90f * dim),
                        0.42f to HColors.Bg.copy(alpha = 0.45f * dim),
                        0.72f to Color.Transparent,
                    ),
                ),
        )
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        0f to Color.Transparent,
                        0.38f to Color.Transparent,
                        0.72f to HColors.Bg.copy(alpha = 0.82f * dim),
                        1f to HColors.Bg,
                    ),
                ),
        )
    }
}

/** The big title block at the top of a screen. */
@Composable
fun InfoPanel(item: MediaItem?, modifier: Modifier = Modifier, label: String? = null) {
    Column(modifier.padding(start = 48.dp, end = 360.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (item == null) return@Column
        if (label != null) {
            Text(label.uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp,
                color = HColors.TextDim)
        }
        Text(item.title, fontSize = 40.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.8).sp,
            lineHeight = 42.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (item.subtitle != null) {
                Text(item.subtitle, color = HColors.Text.copy(alpha = 0.8f), fontSize = 14.sp, maxLines = 1)
            }
            if (item.badge != null) Pill(item.badge, item.badgeTone)
        }
        if (item.overview != null) {
            Text(item.overview, color = HColors.Text.copy(alpha = 0.82f), fontSize = 14.sp, lineHeight = 20.sp,
                maxLines = 3, overflow = TextOverflow.Ellipsis)
        }
    }
}

// ------------------------------------------------------------------ states & dialogs

@Composable
fun Loading(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = HColors.Text, strokeWidth = 2.5.dp, modifier = Modifier.size(34.dp))
    }
}

@Composable
fun ErrorPanel(
    message: String,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
    extraLabel: String? = null,
    onExtra: (() -> Unit)? = null,
) {
    val fr = remember { FocusRequester() }
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        FrostPanel(Modifier.widthIn(max = 560.dp)) {
            Column(Modifier.padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text("Something went wrong", fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(10.dp))
                Text(message, color = HColors.TextDim, fontSize = 15.sp, textAlign = TextAlign.Center)
                Spacer(Modifier.height(24.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    TvButton("Try again", onRetry, style = "primary", focusRequester = fr)
                    if (extraLabel != null && onExtra != null) TvButton(extraLabel, onExtra)
                }
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { fr.requestFocus() } }
}

@Composable
private fun DialogCard(content: @Composable () -> Unit) {
    FrostPanel(Modifier.widthIn(min = 380.dp, max = 560.dp)) {
        Box(Modifier.padding(28.dp)) { content() }
    }
}

@Composable
fun ConfirmDialog(
    title: String,
    message: String,
    confirmLabel: String,
    danger: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val cancelFr = remember { FocusRequester() }
    val okFr = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        DialogCard {
            Column {
                Text(title, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(10.dp))
                Text(message, color = HColors.TextDim, fontSize = 15.sp, lineHeight = 21.sp)
                Spacer(Modifier.height(24.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    TvButton(confirmLabel, onConfirm, style = if (danger) "danger" else "primary", focusRequester = okFr)
                    TvButton("Cancel", onDismiss, focusRequester = cancelFr)
                }
            }
        }
    }
    // Destructive actions start on Cancel so a stray OK press is harmless.
    LaunchedEffect(Unit) {
        delay(60)
        runCatching { if (danger) cancelFr.requestFocus() else okFr.requestFocus() }
    }
}

@Composable
fun TextInputDialog(
    title: String,
    initial: String,
    onDone: (String) -> Unit,
    onDismiss: () -> Unit,
    password: Boolean = false,
    imeAction: ImeAction = ImeAction.Done,
) {
    var value by remember { mutableStateOf(initial) }
    val fr = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    Dialog(onDismissRequest = onDismiss) {
        DialogCard {
            Column {
                Text(title, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(14.dp))
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = HColors.Text,
                        unfocusedBorderColor = HColors.GlassEdge,
                        cursorColor = HColors.Text,
                    ),
                    visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
                    keyboardOptions = KeyboardOptions(imeAction = imeAction),
                    keyboardActions = KeyboardActions(
                        onDone = { onDone(value) },
                        onSearch = { onDone(value) },
                        onGo = { onDone(value) },
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .focusRequester(fr),
                )
                Spacer(Modifier.height(18.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    TvButton("OK", { onDone(value) }, style = "primary")
                    TvButton("Cancel", onDismiss)
                }
            }
        }
    }
    LaunchedEffect(Unit) {
        delay(80)
        runCatching { fr.requestFocus() }
        keyboard?.show()
    }
}

@Composable
fun BusyDialog(label: String) {
    Dialog(
        onDismissRequest = {},
        properties = DialogProperties(dismissOnBackPress = false, dismissOnClickOutside = false),
    ) {
        DialogCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(color = HColors.Text, strokeWidth = 2.5.dp, modifier = Modifier.size(28.dp))
                Spacer(Modifier.width(18.dp))
                Text(label, fontSize = 17.sp)
            }
        }
    }
}

/** A vertical list of full-width buttons in a dialog — used for "what do you want to do with this?". */
@Composable
fun MenuDialog(
    title: String,
    subtitle: String? = null,
    items: List<Pair<String, String?>>,   // label to style
    onPick: (Int) -> Unit,
    onDismiss: () -> Unit,
) {
    val first = remember { FocusRequester() }
    Dialog(onDismissRequest = onDismiss) {
        DialogCard {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(title, fontSize = 18.sp, fontWeight = FontWeight.Bold, maxLines = 3, overflow = TextOverflow.Ellipsis)
                if (subtitle != null) {
                    Text(subtitle, color = HColors.TextDim, fontSize = 13.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
                }
                Spacer(Modifier.height(4.dp))
                items.forEachIndexed { i, (label, style) ->
                    TvButton(label, { onPick(i) }, Modifier.fillMaxWidth(), style = style,
                        focusRequester = if (i == 0) first else null, fill = true)
                }
                TvButton("Cancel", onDismiss, Modifier.fillMaxWidth(), fill = true)
            }
        }
    }
    LaunchedEffect(Unit) {
        delay(60)
        runCatching { first.requestFocus() }
    }
}

fun fmtBytes(bytes: Long): String {
    var b = bytes.toDouble()
    val units = listOf("B", "KB", "MB", "GB", "TB")
    var i = 0
    while (b >= 1024 && i < units.lastIndex) {
        b /= 1024
        i++
    }
    return if (i <= 2) "%.0f %s".format(b, units[i]) else "%.1f %s".format(b, units[i])
}

fun fmtSpeed(bytesPerSec: Long): String = if (bytesPerSec <= 0) "—" else fmtBytes(bytesPerSec) + "/s"

fun fmtEta(seconds: Long): String {
    if (seconds < 0) return "∞"
    val h = seconds / 3600
    val m = (seconds % 3600) / 60
    val s = seconds % 60
    return when {
        h >= 24 -> "${h / 24}d ${h % 24}h"
        h > 0 -> "${h}h ${m}m"
        m > 0 -> "${m}m ${s}s"
        else -> "${s}s"
    }
}

fun fmtClock(ms: Long): String {
    val t = (ms / 1000).coerceAtLeast(0)
    val h = t / 3600
    val m = (t % 3600) / 60
    val s = t % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

/**
 * Fades the top edge of a scrolling list, so rows that slide up under the hero melt away
 * instead of leaving a hard-cut sliver of artwork.
 */
fun Modifier.fadeTop(height: Dp = 14.dp): Modifier = this
    .graphicsLayer(compositingStrategy = androidx.compose.ui.graphics.CompositingStrategy.Offscreen)
    .drawWithContent {
        drawContent()
        drawRect(
            Brush.verticalGradient(listOf(Color.Transparent, Color.Black), startY = 0f, endY = height.toPx()),
            blendMode = androidx.compose.ui.graphics.BlendMode.DstIn,
        )
    }
