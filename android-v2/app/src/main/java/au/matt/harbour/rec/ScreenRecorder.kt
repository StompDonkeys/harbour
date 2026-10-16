package au.matt.harbour.rec

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.DisplayMetrics
import android.view.WindowManager
import androidx.compose.runtime.mutableStateOf
import au.matt.harbour.data.Prefs
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Demo recordings: Android's screen capture (MediaProjection) records Harbour to an MP4 on the
 * TV, then the gateway saves it to the NAS through File Station. Video only — Fire OS can't
 * capture app audio this way.
 */
object ScreenRecorder {
    sealed interface State {
        data object Idle : State
        /** Counting down so the "start recording?" prompt isn't in the video. */
        data class Countdown(val startsAt: Long) : State
        data class Recording(val since: Long) : State
        data object Saving : State
        data class Saved(val path: String) : State
        data class Failed(val message: String, val canRetry: Boolean) : State
    }

    const val COUNTDOWN_MS = 3_200L
    val state = mutableStateOf<State>(State.Idle)
    val isRecording: Boolean get() = state.value is State.Recording || state.value is State.Countdown

    internal var pending: File? = null

    fun captureIntent(context: Context): Intent =
        (context.getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager).createScreenCaptureIntent()

    fun start(context: Context, resultCode: Int, data: Intent) {
        val i = Intent(context, RecorderService::class.java)
            .setAction(RecorderService.ACTION_START)
            .putExtra(RecorderService.EXTRA_CODE, resultCode)
            .putExtra(RecorderService.EXTRA_DATA, data)
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
    }

    fun stop(context: Context) {
        context.startService(Intent(context, RecorderService::class.java).setAction(RecorderService.ACTION_STOP))
    }

    fun retry(context: Context) {
        val f = pending ?: return
        state.value = State.Saving
        Thread { upload(context.applicationContext, f) }.start()
    }

    /** Send the file to the gateway, which writes it into the NAS (blocking; call off the main thread). */
    internal fun upload(context: Context, file: File) {
        val main = Handler(Looper.getMainLooper())
        try {
            val prefs = Prefs(context)
            val base = prefs.url.trim().trimEnd('/').let {
                if (it.startsWith("http://") || it.startsWith("https://")) it else "http://$it"
            }
            val name = URLEncoder.encode(file.name, "UTF-8").replace("+", "%20")
            val client = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .writeTimeout(120, TimeUnit.SECONDS)
                .readTimeout(15, TimeUnit.MINUTES)
                .build()
            val req = Request.Builder()
                .url("$base/api/recording?name=$name")
                .header("X-Token", prefs.token)
                .post(file.asRequestBody("video/mp4".toMediaType()))
                .build()
            client.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                val obj = runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull()
                if (!resp.isSuccessful) {
                    val msg = obj?.get("error")?.jsonPrimitive?.content ?: "Gateway returned HTTP ${resp.code}"
                    throw IllegalStateException(msg)
                }
                val path = obj?.get("path")?.jsonPrimitive?.content ?: file.name
                file.delete()
                pending = null
                main.post { state.value = State.Saved(path) }
            }
        } catch (e: Exception) {
            pending = file
            main.post { state.value = State.Failed("Couldn't save to the NAS: ${e.message}", canRetry = true) }
        }
    }
}

class RecorderService : Service() {
    companion object {
        const val ACTION_START = "au.matt.harbour.rec.START"
        const val ACTION_STOP = "au.matt.harbour.rec.STOP"
        const val EXTRA_CODE = "code"
        const val EXTRA_DATA = "data"
        private const val CHANNEL = "harbour-recording"
    }

    private val main = Handler(Looper.getMainLooper())
    private var projection: MediaProjection? = null
    private var display: VirtualDisplay? = null
    private var recorder: MediaRecorder? = null
    private var file: File? = null
    private var started = false
    private var finishing = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                goForeground()
                val code = intent.getIntExtra(EXTRA_CODE, 0)
                @Suppress("DEPRECATION")
                val data: Intent? = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(EXTRA_DATA, Intent::class.java)
                else intent.getParcelableExtra(EXTRA_DATA)
                if (data == null) {
                    fail("Screen recording wasn't allowed")
                } else {
                    try {
                        begin(code, data)
                    } catch (e: Exception) {
                        fail("Couldn't start recording: ${e.message}")
                    }
                }
            }
            ACTION_STOP -> finish()
        }
        return START_NOT_STICKY
    }

    private fun goForeground() {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Screen recording", NotificationManager.IMPORTANCE_LOW))
        }
        @Suppress("DEPRECATION")
        val n: Notification = (if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL) else Notification.Builder(this))
            .setSmallIcon(android.R.drawable.presence_video_online)
            .setContentTitle("Harbour is recording the screen")
            .setContentText("Press ☰ on the remote to stop")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
        } else {
            startForeground(1, n)
        }
    }

    private fun begin(code: Int, data: Intent) {
        val mpm = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        val proj = mpm.getMediaProjection(code, data) ?: throw IllegalStateException("no projection")
        projection = proj
        proj.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                main.post { finish() }
            }
        }, main)

        // The TV's UI resolution (1920×1080 on a Fire TV), kept even for the encoder.
        val dm = DisplayMetrics()
        @Suppress("DEPRECATION")
        (getSystemService(Context.WINDOW_SERVICE) as WindowManager).defaultDisplay.getRealMetrics(dm)
        var w = dm.widthPixels
        var h = dm.heightPixels
        if (w > 1920) {
            h = h * 1920 / w
            w = 1920
        }
        w -= w % 2
        h -= h % 2

        val dir = getExternalFilesDir(Environment.DIRECTORY_MOVIES) ?: filesDir
        dir.mkdirs()
        val stamp = SimpleDateFormat("yyyy-MM-dd HH.mm.ss", Locale.US).format(Date())
        val out = File(dir, "Harbour $stamp.mp4")
        file = out

        @Suppress("DEPRECATION")
        val rec = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else MediaRecorder()
        rec.setVideoSource(MediaRecorder.VideoSource.SURFACE)
        rec.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
        rec.setVideoEncoder(MediaRecorder.VideoEncoder.H264)
        rec.setVideoSize(w, h)
        rec.setVideoFrameRate(30)
        rec.setVideoEncodingBitRate(10_000_000)
        rec.setOutputFile(out.absolutePath)
        rec.prepare()
        recorder = rec

        ScreenRecorder.state.value = ScreenRecorder.State.Countdown(System.currentTimeMillis() + ScreenRecorder.COUNTDOWN_MS)
        main.postDelayed({
            if (finishing) return@postDelayed
            try {
                display = proj.createVirtualDisplay("harbour-recording", w, h, dm.densityDpi,
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR, rec.surface, null, null)
                rec.start()
                started = true
                ScreenRecorder.state.value = ScreenRecorder.State.Recording(System.currentTimeMillis())
            } catch (e: Exception) {
                fail("Couldn't start recording: ${e.message}")
            }
        }, ScreenRecorder.COUNTDOWN_MS)
    }

    private fun finish() {
        if (finishing) return
        finishing = true
        var ok = started
        try {
            if (started) recorder?.stop()
        } catch (e: RuntimeException) {
            ok = false  // stopped before any frames were written
        }
        runCatching { recorder?.release() }
        runCatching { display?.release() }
        runCatching { projection?.stop() }
        recorder = null
        display = null
        projection = null
        val f = file
        if (!ok || f == null || !f.exists() || f.length() == 0L) {
            f?.delete()
            ScreenRecorder.state.value =
                if (started) ScreenRecorder.State.Failed("The recording was too short to save", canRetry = false)
                else ScreenRecorder.State.Idle
            stopSelfNow()
            return
        }
        ScreenRecorder.state.value = ScreenRecorder.State.Saving
        Thread {
            ScreenRecorder.upload(applicationContext, f)
            main.post { stopSelfNow() }
        }.start()
    }

    private fun fail(msg: String) {
        finishing = true
        runCatching { recorder?.release() }
        runCatching { display?.release() }
        runCatching { projection?.stop() }
        file?.delete()
        ScreenRecorder.state.value = ScreenRecorder.State.Failed(msg, canRetry = false)
        stopSelfNow()
    }

    private fun stopSelfNow() {
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE) else {
            @Suppress("DEPRECATION") stopForeground(true)
        }
        stopSelf()
    }

    override fun onDestroy() {
        if (!finishing) finish()
        super.onDestroy()
    }
}
