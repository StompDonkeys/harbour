package au.matt.harbour.ui

import android.annotation.SuppressLint
import android.view.KeyEvent as AndroidKeyEvent
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem as M3Item
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import au.matt.harbour.data.trailer
import kotlinx.coroutines.delay

/**
 * Trailers play right here. The gateway finds the real video + audio streams behind the YouTube
 * trailer and relays them; Harbour's own player plays them. If that ever fails (YouTube changes
 * things), it falls back to YouTube's embedded player inside Harbour — still no leaving the app.
 */
@OptIn(UnstableApi::class)
@Composable
fun TrailerScreen(key: String, title: String, onExit: () -> Unit) {
    val gw = LocalGateway.current
    val context = LocalContext.current
    var web by remember { mutableStateOf(false) }
    var ready by remember { mutableStateOf(false) }
    var playing by remember { mutableStateOf(true) }
    var controls by remember { mutableStateOf(true) }
    var poke by remember { mutableIntStateOf(0) }
    var pos by remember { mutableLongStateOf(0L) }
    var dur by remember { mutableLongStateOf(0L) }
    var quality by remember { mutableStateOf<String?>(null) }

    BackHandler { onExit() }

    DisposableEffect(Unit) {
        val window = (context as? android.app.Activity)?.window
        window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        onDispose { window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
    }

    if (web) {
        TrailerWeb(key)
        return
    }

    val player = remember { ExoPlayer.Builder(context).build().apply { playWhenReady = true } }
    DisposableEffect(Unit) {
        val l = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) { playing = isPlaying }
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_READY) ready = true
                if (state == Player.STATE_ENDED) onExit()
            }
            override fun onPlayerError(error: PlaybackException) { web = true }
        }
        player.addListener(l)
        onDispose {
            player.removeListener(l)
            player.release()
        }
    }

    LaunchedEffect(key) {
        try {
            val t = gw.trailer(key)
            quality = t.height?.let { "${it}p" }
            val http = DefaultHttpDataSource.Factory()
                .setAllowCrossProtocolRedirects(true)
                .setConnectTimeoutMs(15_000)
                .setReadTimeoutMs(30_000)
            val factory = ProgressiveMediaSource.Factory(http)
            val video = factory.createMediaSource(M3Item.fromUri(gw.img(t.video)!!))
            val source = t.audio?.let { a ->
                MergingMediaSource(video, factory.createMediaSource(M3Item.fromUri(gw.img(a)!!)))
            } ?: video
            player.setMediaSource(source)
            player.prepare()
        } catch (e: Exception) {
            web = true
        }
    }
    LaunchedEffect(Unit) {
        while (true) {
            pos = player.currentPosition
            dur = player.duration.coerceAtLeast(0)
            delay(500)
        }
    }
    LaunchedEffect(poke, playing) {
        if (playing) {
            delay(3500)
            controls = false
        }
    }

    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .focusRequester(focus)
            .focusable()
            .onPreviewKeyEvent { e ->
                if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                val code = e.nativeKeyEvent.keyCode
                if (code == AndroidKeyEvent.KEYCODE_BACK || code == AndroidKeyEvent.KEYCODE_ESCAPE) {
                    return@onPreviewKeyEvent false
                }
                controls = true
                poke++
                when (code) {
                    AndroidKeyEvent.KEYCODE_DPAD_CENTER, AndroidKeyEvent.KEYCODE_ENTER,
                    AndroidKeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> {
                        if (player.isPlaying) player.pause() else player.play()
                        true
                    }
                    AndroidKeyEvent.KEYCODE_DPAD_RIGHT, AndroidKeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                        player.seekTo((player.currentPosition + 10_000).coerceAtMost(player.duration.coerceAtLeast(0)))
                        true
                    }
                    AndroidKeyEvent.KEYCODE_DPAD_LEFT, AndroidKeyEvent.KEYCODE_MEDIA_REWIND -> {
                        player.seekTo((player.currentPosition - 10_000).coerceAtLeast(0))
                        true
                    }
                    else -> true
                }
            },
    ) {
        AndroidView(
            factory = { ctx ->
                PlayerView(ctx).apply {
                    useController = false
                    resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                    setShutterBackgroundColor(android.graphics.Color.BLACK)
                    this.player = player
                    isFocusable = false
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
        if (!ready) {
            Column(Modifier.align(Alignment.Center), horizontalAlignment = Alignment.CenterHorizontally) {
                CircularProgressIndicator(color = Color.White, strokeWidth = 2.5.dp, modifier = Modifier.size(34.dp))
                Spacer(Modifier.height(12.dp))
                Text("Loading trailer…", color = HColors.TextDim, fontSize = 14.sp)
            }
        }
        if (!playing && ready) {
            Box(Modifier.align(Alignment.Center).size(84.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.45f)),
                contentAlignment = Alignment.Center) {
                Icon(Icons.Filled.PlayArrow, contentDescription = null, tint = Color.White, modifier = Modifier.size(46.dp))
            }
        }
        AnimatedVisibility(
            visible = controls && ready,
            enter = fadeIn(tween(200)),
            exit = fadeOut(tween(400)),
            modifier = Modifier.align(Alignment.BottomCenter),
        ) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.75f))))
                    .padding(start = 48.dp, end = 48.dp, top = 60.dp, bottom = 26.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("TRAILER", fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp,
                            color = HColors.TextDim)
                        Text(title, fontSize = 19.sp, fontWeight = FontWeight.Bold, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                    }
                    quality?.let { FormatTag(it.uppercase()) }
                }
                Spacer(Modifier.height(10.dp))
                Box(Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp)).background(Color.White.copy(alpha = 0.25f))) {
                    val f = if (dur > 0) (pos.toFloat() / dur).coerceIn(0f, 1f) else 0f
                    Box(Modifier.fillMaxWidth(f).height(4.dp).background(Color.White))
                }
                Spacer(Modifier.height(6.dp))
                Row {
                    Text(fmtClock(pos), fontSize = 12.sp, color = HColors.TextDim, modifier = Modifier.weight(1f))
                    Text("OK pause  ·  ◂ ▸ 10s  ·  Back to close", fontSize = 12.sp, color = HColors.TextDim)
                    Spacer(Modifier.weight(1f))
                    Text(fmtClock(dur), fontSize = 12.sp, color = HColors.TextDim)
                }
            }
        }
    }
}

/** YouTube's own embedded player, inside Harbour. */
@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun TrailerWeb(key: String) {
    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.mediaPlaybackRequiresUserGesture = false
                settings.userAgentString = settings.userAgentString.replace("; wv", "")
                webViewClient = WebViewClient()
                webChromeClient = WebChromeClient()
                setBackgroundColor(android.graphics.Color.BLACK)
                loadUrl(
                    "https://www.youtube-nocookie.com/embed/$key?autoplay=1&rel=0&modestbranding=1&playsinline=1",
                    mapOf("Referer" to "https://harbour.app/"),
                )
                requestFocus()
            }
        },
        modifier = Modifier.fillMaxSize().background(Color.Black),
    )
}
