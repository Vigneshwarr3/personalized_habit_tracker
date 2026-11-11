package com.habittracker.collector

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * In-app updates from YOUR server (no Play Store): GET /app/android/version, download /app/android/apk with the
 * device token, check its SHA-256, then hand it to Android's installer, which asks you to confirm.
 */
object Updater {
    data class Remote(val code: Long, val name: String, val sha256: String)

    fun installedCode(ctx: Context): Long {
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
    }

    fun installedName(ctx: Context): String = ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?"

    private fun open(prefs: Prefs, path: String): HttpURLConnection =
        (URL(prefs.serverUrl + path).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 60_000
            setRequestProperty("Authorization", "Bearer ${prefs.token}")
        }

    fun fetchRemote(prefs: Prefs): Remote? {
        val c = open(prefs, "/app/android/version")
        try {
            if (c.responseCode == 404) return null
            if (c.responseCode != 200) throw IOException("server returned HTTP ${c.responseCode}")
            val j = JSONObject(c.inputStream.bufferedReader().readText())
            return Remote(j.getLong("version_code"), j.optString("version_name"), j.getString("sha256"))
        } finally {
            c.disconnect()
        }
    }

    /** Downloads + verifies + starts the system install flow. Returns null on success, else an error message. */
    fun downloadAndInstall(ctx: Context, prefs: Prefs, remote: Remote): String? {
        val file = File(ctx.cacheDir, "update.apk")
        val c = open(prefs, "/app/android/apk")
        try {
            if (c.responseCode != 200) return "download failed (HTTP ${c.responseCode})"
            c.inputStream.use { inp -> file.outputStream().use { inp.copyTo(it) } }
        } finally {
            c.disconnect()
        }
        val digest = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
        if (!digest.equals(remote.sha256, ignoreCase = true)) return "downloaded file is corrupted (checksum mismatch)"

        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
        val sessionId = installer.createSession(params)
        installer.openSession(sessionId).use { session ->
            session.openWrite("habit-tracker", 0, file.length()).use { out ->
                file.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val pending = PendingIntent.getBroadcast(ctx, sessionId, Intent(ctx, InstallReceiver::class.java), flags)
            session.commit(pending.intentSender)
        }
        return null
    }
}

/** Android reports install progress here; when it needs your confirmation we open its "Update app?" screen. */
class InstallReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = if (Build.VERSION.SDK_INT >= 33)
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                else @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
                confirm?.let { ctx.startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            }
            PackageInstaller.STATUS_SUCCESS -> Prefs(ctx).lastError = null
            else -> Prefs(ctx).lastError =
                "update not installed: " + (intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "unknown")
        }
    }
}
