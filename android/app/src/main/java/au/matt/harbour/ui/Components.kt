package au.matt.harbour.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
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
    AnimatedVisibility(visible = text != null, enter = fadeIn(), exit = fadeOut(), modifier = modifier) {
        Text(
            last,
            color = HColors.Text,
            fontSize = 15.sp,
            modifier = Modifier
                .padding(36.dp)
                .widthIn(max = 460.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(HColors.SurfaceHi)
                .padding(horizontal = 20.dp, vertical = 14.dp),
        )
    }
}

// ------------------------------------------------------------------ focus

/**
 * The core TV building block: something you can land on with the D-pad.
 * Grows a little and gets a white outline when focused, OK/centre clicks it,
 * and the remote's ☰ menu button fires [onMenu].
 */
@Composable
fun FocusBox(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    shape: Shape = RoundedCornerShape(10.dp),
    focusedScale: Float = 1.08f,
    borderWidth: Dp = 3.dp,
    onFocused: (() -> Unit)? = null,
    onMenu: (() -> Unit)? = null,
    focusRequester: FocusRequester? = null,
    content: @Composable BoxScope.(focused: Boolean) -> Unit,
) {
    val interaction = remember { MutableInteractionSource() }
    val focused by interaction.collectIsFocusedAsState()
    val scale by animateFloatAsState(if (focused) focusedScale else 1f, tween(140), label = "focusScale")
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
        m.clip(shape)
            .border(borderWidth, if (focused && borderWidth > 0.dp) Color.White else Color.Transparent, shape)
            .clickable(interactionSource = interaction, indication = null, onClick = onClick),
    ) {
        content(focused)
    }
}

@Composable
fun TvButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    style: String? = null,
    focusRequester: FocusRequester? = null,
    onFocused: (() -> Unit)? = null,
    fill: Boolean = false,
) {
    FocusBox(
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(50),
        focusedScale = 1.06f,
        borderWidth = 0.dp,
        focusRequester = focusRequester,
        onFocused = onFocused,
    ) { f ->
        val bg = when {
            f -> Color.White
            style == "danger" -> HColors.Danger.copy(alpha = 0.28f)
            style == "primary" -> HColors.Accent.copy(alpha = 0.35f)
            else -> Color.White.copy(alpha = 0.12f)
        }
        Text(
            text,
            color = if (f) Color.Black else HColors.Text,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            textAlign = if (fill) TextAlign.Center else null,
            modifier = (if (fill) Modifier.fillMaxWidth() else Modifier)
                .background(bg)
                .padding(horizontal = 22.dp, vertical = 10.dp),
        )
    }
}

// ------------------------------------------------------------------ media bits

@Composable
fun Pill(text: String, tone: String?, modifier: Modifier = Modifier) {
    Text(
        text,
        color = Color.White,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        maxLines = 1,
        modifier = modifier
            .clip(RoundedCornerShape(6.dp))
            .background(toneColor(tone).copy(alpha = 0.9f))
            .padding(horizontal = 7.dp, vertical = 2.dp),
    )
}

@Composable
fun ProgressStrip(progress: Float, modifier: Modifier = Modifier) {
    Box(
        modifier
            .fillMaxWidth()
            .height(4.dp)
            .background(Color.Black.copy(alpha = 0.6f)),
    ) {
        Box(
            Modifier
                .fillMaxWidth(progress.coerceIn(0f, 1f))
                .fillMaxHeight()
                .background(HColors.Accent),
        )
    }
}

@Composable
fun PosterCard(
    item: MediaItem,
    onClick: () -> Unit,
    onFocused: () -> Unit,
    modifier: Modifier = Modifier,
    width: Dp = 112.dp,
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
    ) { _ ->
        Box(
            Modifier
                .fillMaxSize()
                .background(HColors.SurfaceHi),
        ) {
            val poster = gw.img(item.poster)
            if (poster != null) {
                AsyncImage(
                    model = poster,
                    contentDescription = item.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            } else {
                Text(
                    item.title,
                    color = HColors.TextDim,
                    fontSize = 13.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(8.dp),
                )
            }
            if (showBadge && item.badge != null) {
                Pill(item.badge, item.badgeTone, Modifier.align(Alignment.TopStart).padding(5.dp))
            }
            if (item.progress != null) {
                ProgressStrip(item.progress, Modifier.align(Alignment.BottomCenter))
            }
        }
    }
}

@Composable
fun LandscapeCard(
    item: MediaItem,
    onClick: () -> Unit,
    onFocused: () -> Unit,
    modifier: Modifier = Modifier,
    width: Dp = 220.dp,
    focusRequester: FocusRequester? = null,
) {
    val gw = LocalGateway.current
    FocusBox(
        onClick = onClick,
        modifier = modifier
            .width(width)
            .aspectRatio(16f / 9f),
        onFocused = onFocused,
        focusRequester = focusRequester,
    ) { _ ->
        Box(
            Modifier
                .fillMaxSize()
                .background(HColors.SurfaceHi),
        ) {
            val img = gw.img(item.backdrop ?: item.poster)
            if (img != null) {
                AsyncImage(
                    model = img,
                    contentDescription = item.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            Box(
                Modifier
                    .fillMaxSize()
                    .background(Brush.verticalGradient(0.3f to Color.Transparent, 1f to Color.Black.copy(alpha = 0.92f))),
            )
            Column(
                Modifier
                    .align(Alignment.BottomStart)
                    .padding(start = 10.dp, end = 10.dp, bottom = 10.dp),
            ) {
                Text(item.title, color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.Bold,
                    maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (item.subtitle != null) {
                    Text(item.subtitle, color = HColors.TextDim, fontSize = 12.sp,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            if (item.badge != null) {
                Pill(item.badge, item.badgeTone, Modifier.align(Alignment.TopStart).padding(6.dp))
            }
            if (item.progress != null) {
                ProgressStrip(item.progress, Modifier.align(Alignment.BottomCenter))
            }
        }
    }
}

/** Full-screen fanart in the top-right, fading into the background like Netflix. */
@Composable
fun Backdrop(url: String?, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize()) {
        Crossfade(targetState = url, animationSpec = tween(450), label = "backdrop") { u ->
            Box(Modifier.fillMaxSize()) {
                if (u != null) {
                    AsyncImage(
                        model = u,
                        contentDescription = null,
                        contentScale = ContentScale.Crop,
                        alignment = Alignment.TopEnd,
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .fillMaxWidth(0.74f)
                            .fillMaxHeight(0.8f),
                    )
                }
            }
        }
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.horizontalGradient(
                        0f to HColors.Bg,
                        0.3f to HColors.Bg.copy(alpha = 0.92f),
                        0.62f to Color.Transparent,
                    ),
                ),
        )
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        0f to Color.Transparent,
                        0.42f to Color.Transparent,
                        0.78f to HColors.Bg,
                    ),
                ),
        )
    }
}

@Composable
fun InfoPanel(item: MediaItem?, modifier: Modifier = Modifier) {
    Column(modifier.padding(start = 48.dp, top = 34.dp, end = 330.dp)) {
        if (item == null) return@Column
        Text(item.title, color = HColors.Text, fontSize = 34.sp, fontWeight = FontWeight.Bold,
            maxLines = 2, overflow = TextOverflow.Ellipsis, lineHeight = 38.sp)
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            if (item.subtitle != null) {
                Text(item.subtitle, color = HColors.TextDim, fontSize = 15.sp, maxLines = 1)
            }
            if (item.badge != null) Pill(item.badge, item.badgeTone)
        }
        if (item.overview != null) {
            Spacer(Modifier.height(10.dp))
            Text(item.overview, color = HColors.Text.copy(alpha = 0.85f), fontSize = 14.sp,
                maxLines = 3, overflow = TextOverflow.Ellipsis, lineHeight = 20.sp)
        }
    }
}

// ------------------------------------------------------------------ states & dialogs

@Composable
fun Loading(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = HColors.Accent)
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
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.widthIn(max = 560.dp)) {
            Text("Something went wrong", color = HColors.Text, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(10.dp))
            Text(message, color = HColors.TextDim, fontSize = 15.sp, textAlign = TextAlign.Center)
            Spacer(Modifier.height(24.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                TvButton("Try again", onRetry, style = "primary", focusRequester = fr)
                if (extraLabel != null && onExtra != null) TvButton(extraLabel, onExtra)
            }
        }
    }
    LaunchedEffect(Unit) { runCatching { fr.requestFocus() } }
}

@Composable
private fun DialogCard(content: @Composable () -> Unit) {
    Box(
        Modifier
            .widthIn(min = 380.dp, max = 560.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(HColors.Surface)
            .padding(28.dp),
    ) { content() }
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
                Text(title, color = HColors.Text, fontSize = 22.sp, fontWeight = FontWeight.Bold)
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
                Text(title, color = HColors.Text, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(14.dp))
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    singleLine = true,
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
                CircularProgressIndicator(color = HColors.Accent)
                Spacer(Modifier.width(18.dp))
                Text(label, color = HColors.Text, fontSize = 17.sp)
            }
        }
    }
}
