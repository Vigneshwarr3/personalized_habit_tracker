package com.habittracker.collector

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.Process
import androidx.work.BackoffPolicy
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

object Usage {
    fun accessGranted(ctx: Context): Boolean {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= 29)
            ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName)
        else
            @Suppress("DEPRECATION") ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), ctx.packageName)
        return mode == AppOpsManager.MODE_ALLOWED
    }

    /** Foreground/screen events between two times, from Android's own usage log (no polling, no service). */
    fun events(ctx: Context, fromMs: Long, toMs: Long): List<Ev> {
        val usm = ctx.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val it = usm.queryEvents(fromMs, toMs) ?: return emptyList()
        val out = mutableListOf<Ev>()
        val e = UsageEvents.Event()
        while (it.hasNextEvent()) {
            it.getNextEvent(e)
            val type = when (e.eventType) {
                1 -> Ev.Type.RESUMED        // ACTIVITY_RESUMED (MOVE_TO_FOREGROUND)
                2 -> Ev.Type.PAUSED         // ACTIVITY_PAUSED (MOVE_TO_BACKGROUND)
                15 -> Ev.Type.SCREEN_ON     // SCREEN_INTERACTIVE
                16 -> Ev.Type.SCREEN_OFF    // SCREEN_NON_INTERACTIVE
                17 -> Ev.Type.LOCKED        // KEYGUARD_SHOWN
                18 -> Ev.Type.UNLOCKED      // KEYGUARD_HIDDEN
                26 -> Ev.Type.SHUTDOWN      // DEVICE_SHUTDOWN
                else -> null
            } ?: continue
            out += Ev(e.timeStamp, type, e.packageName)
        }
        return out
    }
}

/** Readable app names ("Instagram" for com.instagram.android), looked up once per app. */
class AppLabels(private val ctx: Context) {
    private val cache = HashMap<String, String?>()
    fun label(pkg: String): String? = cache.getOrPut(pkg) {
        try {
            val pm = ctx.packageManager
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString().takeIf { it.isNotBlank() && it != pkg }
        } catch (e: Exception) { null }
    }
}

/** Heartbeat JSON for the server: the app's package name and its name ("Instagram"). No content, no URLs. */
fun heartbeatJson(s: Session, device: String, label: String? = null): String = JSONObject().apply {
    put("ts", s.startMs / 1000.0)
    put("device", device)
    put("source", "android")
    put("app", s.pkg)
    if (label != null) put("title", label)
    put("idle", false)
    put("media", false)
    put("interval_s", (s.endMs - s.startMs) / 1000.0)
}.toString()

class Uploader(private val prefs: Prefs) {
    /** Sends every buffered batch. Returns the server's pause flag. Throws on network/auth errors. */
    fun flushLocations(buffer: Buffer) {
        while (true) {
            val rows = buffer.locations()
            if (rows.isEmpty()) break
            post("/locations", JSONObject().put("points", JSONArray(rows.map { JSONObject(it.second) })).toString())
            buffer.ackLocations(rows.last().first)
        }
    }

    fun fetchDecimals(): Int? = try {
        val c = (URL(prefs.serverUrl + "/location-settings").openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000; readTimeout = 15_000
            setRequestProperty("Authorization", "Bearer ${prefs.token}")
        }
        try { if (c.responseCode == 200) JSONObject(c.inputStream.bufferedReader().readText()).getInt("decimals") else null }
        finally { c.disconnect() }
    } catch (e: Exception) { null }

    fun postJson(path: String, json: String): JSONObject = post(path, json)

    fun flush(buffer: Buffer): Boolean {
        var paused = prefs.serverPaused
        while (true) {
            val (bid, items) = buffer.nextBatch() ?: break
            val body = JSONObject().put("batch_id", bid).put("heartbeats", JSONArray(items.map { JSONObject(it) }))
            val resp = post("/heartbeats", body.toString())
            paused = resp.optBoolean("paused", false)
            buffer.ack(bid)
        }
        return paused
    }

    private fun post(path: String, json: String): JSONObject {
        val conn = (URL(prefs.serverUrl + path).openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 15_000
            readTimeout = 30_000
            doOutput = true
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("Authorization", "Bearer ${prefs.token}")
            setRequestProperty("User-Agent", "habit-tracker-android/1.0")
        }
        try {
            conn.outputStream.use { it.write(json.toByteArray()) }
            val code = conn.responseCode
            if (code == 401 || code == 403) throw IOException("server rejected the token ($code)")
            if (code !in 200..299) throw IOException("server returned HTTP $code")
            return JSONObject(conn.inputStream.bufferedReader().readText())
        } finally {
            conn.disconnect()
        }
    }
}

/** Runs every ~15 min (WorkManager; Android batches it with other work to save battery). */
class SyncWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result = synchronized(LOCK) {
        val ctx = applicationContext
        val prefs = Prefs(ctx)
        if (!prefs.configured) return Result.success()
        if (!Usage.accessGranted(ctx)) {
            prefs.lastError = "Usage access is not granted"
            return Result.success()
        }
        val now = System.currentTimeMillis()
        val buffer = Buffer(ctx)
        if (prefs.cursorMs == 0L) prefs.cursorMs = now
        if (prefs.serverPaused) {
            prefs.cursorMs = now      // paused: nothing is recorded for this period
            prefs.open = null
        } else {
            val events = Usage.events(ctx, prefs.cursorMs, now)
            val (sessions, open) = SessionBuilder.build(events, prefs.open, now, setOf(ctx.packageName))
            val labels = AppLabels(ctx)
            buffer.add(sessions.map { heartbeatJson(it, prefs.device, labels.label(it.pkg)) })
            prefs.cursorMs = now
            prefs.open = open
            // one low-power location fix, rounded HERE before it is stored or sent
            if (PhoneLocation.hasLocation(ctx) && PhoneLocation.hasBackground(ctx)) {
                PhoneLocation.registerActivityUpdates(ctx)
                PhoneLocation.fix(ctx)?.let { loc ->
                    buffer.addLocation(JSONObject().apply {
                        put("ts", loc.time / 1000.0)
                        put("lat", Coarse.round(loc.latitude, prefs.decimals))
                        put("lon", Coarse.round(loc.longitude, prefs.decimals))
                        put("accuracy_m", loc.accuracy.toDouble())
                        put("activity", prefs.activity)
                    }.toString())
                }
            }
        }
        return try {
            val up = Uploader(prefs)
            up.fetchDecimals()?.let { prefs.decimals = it }
            up.flushLocations(buffer)
            prefs.serverPaused = up.flush(buffer)
            prefs.lastSyncMs = now
            prefs.lastError = null
            Result.success()
        } catch (e: Exception) {
            prefs.lastError = "${e.javaClass.simpleName}: ${e.message} (will retry; ${buffer.count()} waiting)"
            Result.retry()
        }
    }

    companion object {
        private val LOCK = Any()
        private const val NAME = "habit-tracker-sync"

        fun schedule(ctx: Context) {
            val req = PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, req)
        }

        fun runNow(ctx: Context) {
            WorkManager.getInstance(ctx).enqueue(OneTimeWorkRequestBuilder<SyncWorker>().build())
        }
    }
}
