package au.matt.harbour.ui

import android.view.KeyEvent as AndroidKeyEvent
import android.view.WindowManager
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.C
import androidx.media3.common.MediaItem as M3Item
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import au.matt.harbour.data.PlayInfo
import au.matt.harbour.data.play
import au.matt.harbour.data.plexProgress
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.Locale

/** One audio or subtitle choice in the player's pickers. */
private data class TrackChoice(val label: String, val group: Tracks.Group?, val index: Int)

@OptIn(UnstableApi::class)
@Composable
fun PlayerScreen(ref: String, onExit: () -> Unit) {
    val gw = LocalGateway.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var currentRef by remember { mutableStateOf(ref) }
    var info by remember { mutableStateOf<PlayInfo?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var triedTranscode by remember { mutableStateOf(false) }
    var loadKey by remember { mutableIntStateOf(0) }

    var playing by remember { mutableStateOf(false) }
    var buffering by remember { mutableStateOf(true) }
    var position by remember { mutableLongStateOf(0L) }
    var duration by remember { mutableLongStateOf(0L) }
    var buffered by remember { mutableLongStateOf(0L) }
    var controls by remember { mutableStateOf(true) }
    var poke by remember { mutableIntStateOf(0) }
    var picker by remember { mutableStateOf<String?>(null) }   // "audio" | "subs"
    var options by remember { mutableStateOf(false) }   // the Audio / Subtitles row (press Down)
    var tracks by remember { mutableStateOf(Tracks.EMPTY) }

    val player = remember {
        val rf = DefaultRenderersFactory(context)
            .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER)
            .setEnableDecoderFallback(true)
        ExoPlayer.Builder(context, rf).build().apply {
            playWhenReady = true
            trackSelectionParameters = trackSelectionParameters.buildUpon()
                .setPreferredAudioLanguage(Locale.getDefault().language)
                .build()
        }
    }

    // Keep the screen on while the player is open.
    DisposableEffect(Unit) {
        val window = (context as? android.app.Activity)?.window
        window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        onDispose { window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
    }

    fun report(state: String) {
        val i = info ?: return
        if (i.ratingKey.isBlank()) return   // a NAS file, not a Plex item
        val pos = player.currentPosition
        val dur = player.duration.takeIf { it > 0 } ?: i.duration
        scope.launch { runCatching { gw.plexProgress(i.ratingKey, state, pos, dur) } }
    }

    DisposableEffect(player) {
        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                playing = isPlaying
                report(if (isPlaying) "playing" else "paused")
            }

            override fun onPlaybackStateChanged(state: Int) {
                buffering = state == Player.STATE_BUFFERING
                if (state == Player.STATE_ENDED) {
                    report("stopped")
                    val next = info?.next
                    if (next != null) {
                        currentRef = next.ref
                        triedTranscode = false
                        loadKey++
                    } else {
                        onExit()
                    }
                }
            }

            override fun onTracksChanged(t: Tracks) {
                tracks = t
            }

            override fun onPlayerError(e: PlaybackException) {
                // Direct play didn't work (codec or container the Fire TV can't handle) → ask Plex to transcode.
                if (!triedTranscode) {
                    triedTranscode = true
                    loadKey++
                } else {
                    error = "Couldn't play this: ${e.errorCodeName}"
                }
            }
        }
        player.addListener(listener)
        onDispose {
            report("stopped")
            player.removeListener(listener)
            player.release()
        }
    }

    // Load (or reload) the stream.
    LaunchedEffect(currentRef, loadKey) {
        error = null
        buffering = true
        try {
            val p = gw.play(currentRef, transcode = triedTranscode)
            info = p
            val item = M3Item.Builder().setUri(p.url).apply {
                if (p.hls) setMimeType(MimeTypes.APPLICATION_M3U8)
            }.build()
            player.setMediaItem(item, if (p.hls) 0L else p.offset)
            player.prepare()
            player.play()
            controls = true
            poke++
        } catch (e: Exception) {
            error = e.message ?: "Couldn't start playback"
        }
    }

    // Clock + periodic progress report.
    LaunchedEffect(player) {
        var tick = 0
        while (true) {
            position = player.currentPosition
            duration = player.duration.takeIf { it > 0 } ?: (info?.duration ?: 0L)
            buffered = player.bufferedPosition
            if (++tick % 20 == 0 && player.isPlaying) report("playing")
            delay(500)
        }
    }

    // Auto-hide the controls.
    LaunchedEffect(poke, playing, picker, options) {
        if (playing && picker == null && !options) {
            delay(4500)
            controls = false
        }
    }

    fun seekBy(ms: Long) {
        val target = (player.currentPosition + ms).coerceIn(0, (player.duration.takeIf { it > 0 } ?: Long.MAX_VALUE))
        player.seekTo(target)
        position = target
        controls = true
        poke++
    }

    var flash by remember { mutableIntStateOf(0) }       // brief play/pause glyph

    BackHandler {
        when {
            picker != null -> picker = null
            options -> options = false
            controls -> controls = false
            else -> onExit()
        }
    }

    val surfaceFocus = remember { FocusRequester() }
    val firstOption = remember { FocusRequester() }

    fun togglePlay() {
        if (player.isPlaying) player.pause() else player.play()
        controls = true
        flash++
        poke++
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Color.Black)
            .focusRequester(surfaceFocus)
            .focusable()
            .onPreviewKeyEvent { e ->
                if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                val code = e.nativeKeyEvent.keyCode
                // Back belongs to the BackHandler: hide the overlay first, then leave the player.
                if (code == AndroidKeyEvent.KEYCODE_BACK || code == AndroidKeyEvent.KEYCODE_ESCAPE) {
                    return@onPreviewKeyEvent false
                }
                val fast = e.nativeKeyEvent.repeatCount > 3
                poke++
                when {
                    code == AndroidKeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> { togglePlay(); true }
                    code == AndroidKeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> { seekBy(30_000); true }
                    code == AndroidKeyEvent.KEYCODE_MEDIA_REWIND -> { seekBy(-10_000); true }
                    options -> when (code) {
                        AndroidKeyEvent.KEYCODE_DPAD_UP -> {
                            options = false
                            runCatching { surfaceFocus.requestFocus() }
                            true
                        }
                        else -> false   // let the option buttons handle it
                    }
                    code == AndroidKeyEvent.KEYCODE_DPAD_RIGHT -> { seekBy(if (fast) 60_000 else 10_000); true }
                    code == AndroidKeyEvent.KEYCODE_DPAD_LEFT -> { seekBy(if (fast) -60_000 else -10_000); true }
                    code == AndroidKeyEvent.KEYCODE_DPAD_CENTER || code == AndroidKeyEvent.KEYCODE_ENTER -> {
                        togglePlay(); true
                    }
                    code == AndroidKeyEvent.KEYCODE_DPAD_DOWN -> {
                        controls = true
                        options = true
                        true
                    }
                    else -> { controls = true; true }
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

        val i = info
        if (i != null && position == 0L && buffering) {
            AsyncImage(model = gw.hero(i.backdrop), contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize())
            Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.45f)))
        }
        if (buffering && error == null) {
            CircularProgressIndicator(color = HColors.Text, strokeWidth = 3.dp,
                modifier = Modifier.align(Alignment.Center).size(44.dp))
        }

        // Paused: a quiet glyph in the middle. Play/pause press: a brief flash.
        var showGlyph by remember { mutableStateOf(false) }
        LaunchedEffect(flash) {
            if (flash > 0) {
                showGlyph = true
                delay(700)
                showGlyph = false
            }
        }
        AnimatedVisibility(
            visible = (!playing && !buffering && error == null && i != null) || showGlyph,
            enter = fadeIn(tween(150)),
            exit = fadeOut(tween(300)),
            modifier = Modifier.align(Alignment.Center),
        ) {
            Box(
                Modifier.size(84.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.45f)),
                contentAlignment = Alignment.Center,
            ) {
                if (playing) {
                    Row(horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                        Box(Modifier.width(9.dp).height(30.dp).clip(RoundedCornerShape(3.dp)).background(Color.White))
                        Box(Modifier.width(9.dp).height(30.dp).clip(RoundedCornerShape(3.dp)).background(Color.White))
                    }
                } else {
                    androidx.compose.material3.Icon(Icons.Filled.PlayArrow, contentDescription = "Paused",
                        tint = Color.White, modifier = Modifier.size(46.dp))
                }
            }
        }

        val err = error
        if (err != null) {
            ErrorPanel(err, onRetry = { triedTranscode = false; loadKey++ }, extraLabel = "Close", onExtra = onExit)
        }

        // Up next card near the end of an episode.
        val next = i?.next
        val remaining = duration - position
        AnimatedVisibility(
            visible = next != null && duration > 0 && remaining in 1..30_000,
            enter = fadeIn() + slideInVertically { -it },
            exit = fadeOut(),
            modifier = Modifier.align(Alignment.TopEnd).padding(28.dp),
        ) {
            if (next != null) {
                CompositionLocalProvider(LocalBackdrop provides gw.img(i.backdrop)) {
                    GlassPanel(Modifier.width(280.dp), shape = RoundedCornerShape(18.dp)) {
                        Row(Modifier.padding(10.dp), verticalAlignment = Alignment.CenterVertically) {
                            AsyncImage(model = gw.img(next.thumb), contentDescription = null, contentScale = ContentScale.Crop,
                                modifier = Modifier.width(96.dp).height(54.dp).clip(RoundedCornerShape(9.dp)))
                            Spacer(Modifier.width(12.dp))
                            Column {
                                Text("UP NEXT IN ${(remaining / 1000).coerceAtLeast(0)}s", fontSize = 10.sp,
                                    fontWeight = FontWeight.Bold, letterSpacing = 1.2.sp, color = HColors.TextDim)
                                Text(listOfNotNull(next.subtitle, next.title).joinToString(" · "), fontSize = 13.sp,
                                    fontWeight = FontWeight.Bold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            }
                        }
                    }
                }
            }
        }

        // Controls: title + a thin progress line over a soft shade. No panel, no clutter.
        AnimatedVisibility(
            visible = controls && i != null && err == null,
            enter = fadeIn(tween(250)),
            exit = fadeOut(tween(400)),
            modifier = Modifier.align(Alignment.BottomCenter),
        ) {
            if (i != null) {
                Column(
                    Modifier
                        .fillMaxWidth()
                        .background(
                            androidx.compose.ui.graphics.Brush.verticalGradient(
                                0f to Color.Transparent, 0.35f to Color.Black.copy(alpha = 0.35f),
                                1f to Color.Black.copy(alpha = 0.78f),
                            ),
                        )
                        .padding(start = 48.dp, end = 48.dp, top = 90.dp, bottom = 26.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Row(verticalAlignment = Alignment.Bottom) {
                        Column(Modifier.weight(1f)) {
                            if (i.subtitle != null) {
                                Text((i.title ?: "").uppercase(), fontSize = 11.sp, fontWeight = FontWeight.Bold,
                                    letterSpacing = 1.3.sp, color = HColors.TextDim, maxLines = 1)
                            }
                            Text(i.subtitle ?: i.title ?: "", fontSize = 19.sp, fontWeight = FontWeight.Bold,
                                maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { i.badges.forEach { FormatTag(it) } }
                    }
                    ProgressLine(position, duration, buffered)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(fmtClock(position), fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                        Spacer(Modifier.weight(1f))
                        val endsAt = remember(position / 60000, duration) {
                            val end = System.currentTimeMillis() + (duration - position)
                            java.text.SimpleDateFormat("h:mm a", Locale.getDefault()).format(java.util.Date(end))
                        }
                        Text("−${fmtClock((duration - position).coerceAtLeast(0))}  ·  ends $endsAt",
                            fontSize = 13.sp, color = HColors.TextDim)
                    }
                    AnimatedVisibility(
                        visible = options,
                        enter = fadeIn(tween(200)) + slideInVertically(tween(260)) { it / 2 },
                        exit = fadeOut(tween(150)),
                    ) {
                        LaunchedEffect(Unit) {
                            delay(40)
                            runCatching { firstOption.requestFocus() }
                        }
                        Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            TvButton("Audio · ${audioLabel(tracks)}", { picker = "audio" }, focusRequester = firstOption)
                            TvButton("Subtitles · ${subLabel(tracks)}", { picker = "subs" }, icon = Icons.Filled.List)
                            TvButton("Restart", { player.seekTo(0); poke++ }, icon = Icons.Filled.Refresh)
                            if (i.next != null) {
                                TvButton("Next episode", {
                                    report("stopped")
                                    options = false
                                    currentRef = i.next.ref
                                    triedTranscode = false
                                    loadKey++
                                }, icon = Icons.Filled.PlayArrow)
                            }
                        }
                    }
                    if (!options) {
                        Text("▾  Audio & subtitles", fontSize = 11.sp, color = HColors.TextFaint)
                    }
                }
            }
        }
    }

    LaunchedEffect(controls, options) {
        if (!options) runCatching { surfaceFocus.requestFocus() }
        if (!controls) options = false
    }

    picker?.let { kind ->
        val type = if (kind == "audio") C.TRACK_TYPE_AUDIO else C.TRACK_TYPE_TEXT
        val choices = buildList {
            if (type == C.TRACK_TYPE_TEXT) add(TrackChoice("Off", null, -1))
            tracks.groups.filter { it.type == type }.forEach { g ->
                for (t in 0 until g.length) if (g.isTrackSupported(t)) add(TrackChoice(trackName(g, t), g, t))
            }
        }
        MenuDialog(
            title = if (type == C.TRACK_TYPE_AUDIO) "Audio" else "Subtitles",
            items = choices.map { c -> (if (c.group != null && c.group.isTrackSelected(c.index)) "✓  " else "") + c.label to null },
            onPick = { idx ->
                picker = null
                val c = choices[idx]
                val params = player.trackSelectionParameters.buildUpon()
                if (c.group == null) {
                    params.setTrackTypeDisabled(C.TRACK_TYPE_TEXT, true)
                } else {
                    params.setTrackTypeDisabled(type, false)
                        .setOverrideForType(TrackSelectionOverride(c.group.mediaTrackGroup, c.index))
                }
                player.trackSelectionParameters = params.build()
            },
            onDismiss = { picker = null },
        )
    }
}

@Composable
private fun ProgressLine(position: Long, duration: Long, buffered: Long) {
    val frac = if (duration > 0) (position.toFloat() / duration).coerceIn(0f, 1f) else 0f
    val bfrac = if (duration > 0) (buffered.toFloat() / duration).coerceIn(0f, 1f) else 0f
    Box(Modifier.fillMaxWidth().height(12.dp), contentAlignment = Alignment.CenterStart) {
        Box(Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp)).background(Color.White.copy(alpha = 0.25f)))
        Box(Modifier.fillMaxWidth(bfrac).height(4.dp).clip(RoundedCornerShape(2.dp)).background(Color.White.copy(alpha = 0.40f)))
        Box(Modifier.fillMaxWidth(frac).height(4.dp).clip(RoundedCornerShape(2.dp)).background(HColors.Text))
        Row(Modifier.fillMaxWidth()) {
            Spacer(Modifier.fillMaxWidth(frac))
            Box(Modifier.offset(x = (-6).dp).size(12.dp).clip(CircleShape).background(HColors.Text))
        }
    }
}

private fun trackName(g: Tracks.Group, i: Int): String {
    val f = g.getTrackFormat(i)
    val lang = f.language?.let { Locale(it).displayLanguage.ifBlank { it } }
    val codec = when (f.sampleMimeType) {
        MimeTypes.AUDIO_TRUEHD -> "TrueHD"
        MimeTypes.AUDIO_E_AC3, MimeTypes.AUDIO_E_AC3_JOC -> if (f.sampleMimeType == MimeTypes.AUDIO_E_AC3_JOC) "DD+ Atmos" else "DD+"
        MimeTypes.AUDIO_AC3 -> "Dolby Digital"
        MimeTypes.AUDIO_DTS, MimeTypes.AUDIO_DTS_HD -> "DTS"
        MimeTypes.AUDIO_AAC -> "AAC"
        MimeTypes.AUDIO_OPUS -> "Opus"
        MimeTypes.APPLICATION_SUBRIP, MimeTypes.TEXT_SSA -> null
        else -> null
    }
    val ch = when (f.channelCount) {
        8 -> "7.1"; 6 -> "5.1"; 2 -> "Stereo"; 1 -> "Mono"; else -> null
    }
    val extra = listOfNotNull(codec, ch, f.label).joinToString(" ")
    return listOfNotNull(lang ?: "Unknown", extra.ifBlank { null }).joinToString(" · ")
}

private fun audioLabel(t: Tracks): String {
    val g = t.groups.firstOrNull { it.type == C.TRACK_TYPE_AUDIO && it.isSelected } ?: return "Auto"
    val i = (0 until g.length).firstOrNull { g.isTrackSelected(it) } ?: return "Auto"
    return trackName(g, i)
}

private fun subLabel(t: Tracks): String {
    val g = t.groups.firstOrNull { it.type == C.TRACK_TYPE_TEXT && it.isSelected } ?: return "Off"
    val i = (0 until g.length).firstOrNull { g.isTrackSelected(it) } ?: return "Off"
    return g.getTrackFormat(i).language?.let { Locale(it).displayLanguage } ?: "On"
}
