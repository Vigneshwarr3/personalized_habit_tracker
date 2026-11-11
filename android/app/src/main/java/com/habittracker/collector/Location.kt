package com.habittracker.collector

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.os.Build
import com.google.android.gms.location.ActivityRecognition
import com.google.android.gms.location.ActivityTransition
import com.google.android.gms.location.ActivityTransitionRequest
import com.google.android.gms.location.ActivityTransitionResult
import com.google.android.gms.location.DetectedActivity
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.google.android.gms.tasks.CancellationTokenSource
import com.google.android.gms.tasks.Tasks
import java.math.BigDecimal
import java.math.RoundingMode
import java.util.concurrent.TimeUnit

/** Pure helper (unit-tested): coordinates are rounded ON THE PHONE before they are stored or sent. */
object Coarse {
    fun round(v: Double, decimals: Int): Double = BigDecimal(v).setScale(decimals, RoundingMode.HALF_UP).toDouble()
}

/** Low-power location (Wi-Fi/cell, ~100 m) + Android's activity detection. No constant GPS. */
object PhoneLocation {
    fun granted(ctx: Context, perm: String) = ctx.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED

    fun hasLocation(ctx: Context) = granted(ctx, Manifest.permission.ACCESS_FINE_LOCATION)
    fun hasBackground(ctx: Context) =
        Build.VERSION.SDK_INT < 29 || granted(ctx, Manifest.permission.ACCESS_BACKGROUND_LOCATION)
    fun hasActivity(ctx: Context) =
        Build.VERSION.SDK_INT < 29 || granted(ctx, Manifest.permission.ACTIVITY_RECOGNITION)

    /** One fix, waiting at most [timeoutS] seconds. Null if unavailable. Call off the main thread. */
    @Suppress("MissingPermission")
    fun fix(ctx: Context, highAccuracy: Boolean = false, timeoutS: Long = 30): Location? {
        if (!hasLocation(ctx)) return null
        return try {
            val prio = if (highAccuracy) Priority.PRIORITY_HIGH_ACCURACY else Priority.PRIORITY_BALANCED_POWER_ACCURACY
            Tasks.await(LocationServices.getFusedLocationProviderClient(ctx)
                .getCurrentLocation(prio, CancellationTokenSource().token), timeoutS, TimeUnit.SECONDS)
        } catch (e: Exception) {
            null
        }
    }

    /** Ask Android to tell us when you start/stop driving, cycling, walking or being still (very low power). */
    @Suppress("MissingPermission")
    fun registerActivityUpdates(ctx: Context) {
        if (!hasActivity(ctx)) return
        val types = listOf(DetectedActivity.IN_VEHICLE, DetectedActivity.ON_BICYCLE, DetectedActivity.WALKING,
            DetectedActivity.RUNNING, DetectedActivity.STILL)
        val transitions = types.map {
            ActivityTransition.Builder().setActivityType(it)
                .setActivityTransition(ActivityTransition.ACTIVITY_TRANSITION_ENTER).build()
        }
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
        val pi = PendingIntent.getBroadcast(ctx, 7, Intent(ctx, ActivityReceiver::class.java), flags)
        try {
            ActivityRecognition.getClient(ctx).requestActivityTransitionUpdates(ActivityTransitionRequest(transitions), pi)
        } catch (_: Exception) {
        }
    }

    fun activityName(type: Int): String = when (type) {
        DetectedActivity.IN_VEHICLE -> "in_vehicle"
        DetectedActivity.ON_BICYCLE -> "on_bicycle"
        DetectedActivity.WALKING -> "walking"
        DetectedActivity.RUNNING -> "running"
        DetectedActivity.STILL -> "still"
        else -> "unknown"
    }
}

/** Remembers your current activity; when you stop after a ride, triggers one extra sync (= "just arrived"). */
class ActivityReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (!ActivityTransitionResult.hasResult(intent)) return
        val last = ActivityTransitionResult.extractResult(intent)?.transitionEvents?.lastOrNull() ?: return
        val prefs = Prefs(ctx)
        val before = prefs.activity
        val now = PhoneLocation.activityName(last.activityType)
        prefs.activity = now
        if (now == "still" && (before == "in_vehicle" || before == "on_bicycle")) SyncWorker.runNow(ctx)
    }
}
