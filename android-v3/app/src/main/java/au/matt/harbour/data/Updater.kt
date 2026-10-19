package au.matt.harbour.data

import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

/** A newer Harbour build published as a GitHub release. */
data class UpdateInfo(val versionCode: Long, val versionName: String, val apkUrl: String, val sizeBytes: Long)

object Updater {
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()
    private val json = Json { ignoreUnknownKeys = true }

    fun installedVersionCode(context: Context): Long {
        val info = context.packageManager.getPackageInfo(context.packageName, 0)
        @Suppress("DEPRECATION")
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
    }

    fun installedVersionName(context: Context): String =
        runCatching { context.packageManager.getPackageInfo(context.packageName, 0).versionName }.getOrNull() ?: "?"

    /** Returns the newest Harbour V3 release (tags v3.*) if it's newer than what's installed, else null. */
    suspend fun check(context: Context, repo: String): UpdateInfo? = withContext(Dispatchers.IO) {
        val req = Request.Builder()
            .url("https://api.github.com/repos/$repo/releases?per_page=100")
            .header("Accept", "application/vnd.github+json")
            .build()
        val body = http.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw GatewayException("GitHub returned HTTP ${r.code}")
            r.body?.string().orEmpty()
        }
        val rel = json.parseToJsonElement(body).jsonArray.map { it.jsonObject }
            .firstOrNull { it["tag_name"]?.jsonPrimitive?.content?.startsWith("v3.") == true }
            ?: return@withContext null
        val tag = rel["tag_name"]?.jsonPrimitive?.content ?: return@withContext null
        // Tags are v3.0.<build number>; the versionCode is 300000 + the build number.
        val code = 300000 + (tag.substringAfterLast('.').toLongOrNull() ?: return@withContext null)
        val asset = rel["assets"]?.jsonArray
            ?.map { it.jsonObject }
            ?.firstOrNull { it["name"]?.jsonPrimitive?.content?.endsWith(".apk") == true }
            ?: return@withContext null
        val url = asset["browser_download_url"]?.jsonPrimitive?.content ?: return@withContext null
        val size = runCatching { asset["size"]?.jsonPrimitive?.int?.toLong() }.getOrNull() ?: 0L
        if (code > installedVersionCode(context)) UpdateInfo(code, tag.removePrefix("v"), url, size) else null
    }

    /** True if Android will let Harbour install APKs (the "Install unknown apps" switch). */
    fun canInstall(context: Context): Boolean =
        Build.VERSION.SDK_INT < 26 || context.packageManager.canRequestPackageInstalls()

    /** Opens the system screen for allowing installs; returns false if the device has none. */
    fun openInstallPermission(context: Context): Boolean = try {
        context.startActivity(
            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
        true
    } catch (e: ActivityNotFoundException) {
        false
    } catch (e: SecurityException) {
        false
    }

    suspend fun download(context: Context, info: UpdateInfo, onProgress: (Float) -> Unit): File =
        withContext(Dispatchers.IO) {
            val dir = File(context.cacheDir, "updates").apply { mkdirs() }
            dir.listFiles()?.forEach { it.delete() }
            val out = File(dir, "Harbour-${info.versionName}.apk")
            http.newCall(Request.Builder().url(info.apkUrl).build()).execute().use { r ->
                if (!r.isSuccessful) throw GatewayException("Download failed (HTTP ${r.code})")
                val body = r.body ?: throw GatewayException("Download failed (empty)")
                val total = body.contentLength().takeIf { it > 0 } ?: info.sizeBytes
                body.byteStream().use { input ->
                    out.outputStream().use { output ->
                        val buf = ByteArray(64 * 1024)
                        var done = 0L
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            output.write(buf, 0, n)
                            done += n
                            if (total > 0) onProgress(done.toFloat() / total)
                        }
                    }
                }
            }
            out
        }

    /** Hands the APK to Android's installer; the system then shows its own "Update?" screen. */
    fun install(context: Context, apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            session.openWrite("harbour.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val pi = PendingIntent.getBroadcast(
                context, sessionId, Intent(context, InstallResultReceiver::class.java), flags,
            )
            session.commit(pi.intentSender)
        }
    }
}

/** Receives the installer's progress; when Android needs the user to confirm, show its dialog. */
class InstallResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                if (confirm != null) {
                    context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }
            PackageInstaller.STATUS_SUCCESS -> Unit // the app restarts as the new version
            else -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "unknown error"
                Toast.makeText(
                    context,
                    "Update failed: $msg\nIf it says the signature doesn't match, uninstall Harbour once and reinstall.",
                    Toast.LENGTH_LONG,
                ).show()
            }
        }
    }
}

/** Saves any crash to a file; the next launch sends it to the gateway log. */
object CrashReporter {
    private const val FILE = "last_crash.txt"

    fun install(context: Context) {
        val app = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            runCatching {
                val text = buildString {
                    append("Harbour ").append(Updater.installedVersionName(app))
                    append(" on ").append(Build.MANUFACTURER).append(' ').append(Build.MODEL)
                    append(" (Android ").append(Build.VERSION.RELEASE).append(")\n")
                    append("Thread: ").append(thread.name).append('\n')
                    append(error.stackTraceToString())
                }
                File(app.filesDir, FILE).writeText(text)
            }
            previous?.uncaughtException(thread, error)
        }
    }

    fun pending(context: Context): String? =
        File(context.filesDir, FILE).takeIf { it.exists() }?.readText()

    fun clear(context: Context) {
        File(context.filesDir, FILE).delete()
    }
}
