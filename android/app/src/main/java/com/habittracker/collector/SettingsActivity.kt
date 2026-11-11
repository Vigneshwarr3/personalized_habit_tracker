package com.habittracker.collector

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import org.json.JSONObject
import android.graphics.Typeface
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.text.DateFormat
import java.util.Date

/** Setup + status: server URL, device token (create it on the dashboard), usage access, sync status. */
class SettingsActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        actionBar?.hide()
        prefs = Prefs(this)
        val pad = (16 * resources.displayMetrics.density).toInt()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        fun label(t: String) = TextView(this).apply {
            text = t; setTypeface(typeface, Typeface.BOLD); setPadding(0, pad, 0, pad / 4)
        }
        fun field(v: String, hint: String, secret: Boolean = false) = EditText(this).apply {
            setText(v); this.hint = hint; isSingleLine = true
            inputType = if (secret) InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            else InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        fun button(t: String, onClick: () -> Unit) = Button(this).apply {
            text = t; setOnClickListener { onClick() }
        }

        status = TextView(this).apply { setPadding(0, pad / 2, 0, pad) }
        val api = field(prefs.serverUrl, "https://your-server.your-tailnet.ts.net:8765")
        val device = field(prefs.device, "android-phone")
        val token = field(prefs.token, "ht_… (Dashboard → Privacy & devices → Create token)", secret = true)

        col.addView(TextView(this).apply {
            text = "Settings & sync"; textSize = 24f; setTypeface(typeface, Typeface.BOLD); setPadding(0, 0, 0, pad / 2)
        })
        col.addView(TextView(this).apply {
            text = "Records which app is in front (package name + time only) and syncs it to your own server " +
                "over Tailscale every ~15 minutes. Nothing else leaves the phone."
        })
        col.addView(status)
        col.addView(button("1. Grant usage access") {
            startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
        })
        col.addView(TextView(this).apply {
            text = "Switch greyed out (\"Restricted setting\")? Android blocks this for apps installed from a " +
                "browser. Tap the button below → ⋮ menu (top right) → \"Allow restricted settings\" → confirm " +
                "with your PIN/fingerprint. Then come back and tap step 1 again."
            setPadding(0, pad / 2, 0, 0)
        })
        col.addView(button("Open app info (to allow restricted settings)") {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        })
        col.addView(label("Location (for Office/Work, Commute, Travel)"))
        col.addView(TextView(this).apply {
            text = "One low-power location every ~15 min, rounded to about 100 m on this phone before it is sent. " +
                "Android needs \"Allow all the time\" because it is taken in the background."
        })
        col.addView(button("Allow location") { requestLocation() })
        col.addView(button("Allow physical activity (vehicle / walking / still)") {
            if (Build.VERSION.SDK_INT >= 29) requestPermissions(arrayOf(Manifest.permission.ACTIVITY_RECOGNITION), REQ_ACTIVITY)
        })
        col.addView(button("Save current location as Home") { savePlace("home") })
        col.addView(button("Save current location as Work") { savePlace("work") })

        col.addView(label("Server API (the dashboard opens from the same address, at /ui/)")); col.addView(api)
        col.addView(label("Device name (must match the token)")); col.addView(device)
        col.addView(label("Device token")); col.addView(token)
        col.addView(button("2. Save") {
            prefs.serverUrl = api.text.toString()
            prefs.device = device.text.toString()
            prefs.token = token.text.toString()
            if (prefs.cursorMs == 0L) prefs.cursorMs = System.currentTimeMillis()  // record from now on
            SyncWorker.schedule(this)
            SyncWorker.runNow(this)
            refresh()
        })
        col.addView(button("Sync now") { SyncWorker.runNow(this); status.postDelayed({ refresh() }, 3000) })
        col.addView(button("Check for update") { checkUpdate() })
        col.addView(button("Open dashboard") { finish() })
        col.addView(TextView(this).apply {
            text = "Pause tracking from the dashboard (Privacy & devices). Tracking stops on the phone at its next sync."
            setPadding(0, pad, 0, 0); visibility = View.VISIBLE
        })
        setContentView(ScrollView(this).apply { addView(col); padForSystemBars() })
        if (intent?.data?.getQueryParameter("update") == "1") checkUpdate()  // "Update app" in the dashboard
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun requestLocation() {
        if (!PhoneLocation.hasLocation(this)) {
            requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION),
                REQ_LOCATION)
        } else if (!PhoneLocation.hasBackground(this) && Build.VERSION.SDK_INT >= 29) {
            // Android shows its settings page here: choose "Allow all the time"
            requestPermissions(arrayOf(Manifest.permission.ACCESS_BACKGROUND_LOCATION), REQ_BACKGROUND)
        }
    }

    @Deprecated("framework callback")
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        @Suppress("DEPRECATION") super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_LOCATION && PhoneLocation.hasLocation(this)) requestLocation()  // step 2: background
        if (PhoneLocation.hasActivity(this)) PhoneLocation.registerActivityUpdates(this)
        refresh()
    }

    private fun savePlace(kind: String) {
        if (!PhoneLocation.hasLocation(this)) { requestLocation(); return }
        status.text = "Finding your location…"
        Thread {
            val msg = try {
                val loc = PhoneLocation.fix(this, highAccuracy = true, timeoutS = 30)
                if (loc == null) "Couldn't get a location fix. Try again near a window or outdoors."
                else {
                    val body = JSONObject().put("lat", Coarse.round(loc.latitude, prefs.decimals))
                        .put("lon", Coarse.round(loc.longitude, prefs.decimals)).toString()
                    Uploader(prefs).postJson("/places/$kind", body)
                    prefs.placeStatus = (prefs.placeStatus.split(",").filter { it.isNotBlank() && it != kind } + kind)
                        .joinToString(",")
                    "${kind.replaceFirstChar { it.uppercase() }} saved (±${loc.accuracy.toInt()} m)."
                }
            } catch (e: Exception) {
                "Couldn't save: ${e.message} (is Tailscale on?)"
            }
            runOnUiThread { refresh(); status.text = msg + "\n\n" + status.text }
        }.start()
    }

    private fun checkUpdate() {
        status.text = "Checking for an update…"
        Thread {
            val msg = try {
                val remote = Updater.fetchRemote(prefs)
                val mine = Updater.installedCode(this)
                when {
                    remote == null -> "No app published on the server yet."
                    remote.code <= mine -> "You have the latest version (${Updater.installedName(this)})."
                    else -> Updater.downloadAndInstall(this, prefs, remote)
                        ?: "Downloading version ${remote.name}… confirm the update when Android asks."
                }
            } catch (e: Exception) {
                "Update check failed: ${e.message} (is Tailscale on?)"
            }
            runOnUiThread { refresh(); status.text = msg + "\n\n" + status.text }
        }.start()
    }

    private fun refresh() {
        val fmt = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)
        val lines = mutableListOf<String>()
        lines += "App version ${Updater.installedName(this)}"
        lines += if (Usage.accessGranted(this)) "✓ Usage access granted" else "✗ Usage access NOT granted (step 1)"
        lines += if (prefs.configured) "✓ Server + token saved" else "✗ Server URL (https) and token needed (step 2)"
        lines += when {
            !PhoneLocation.hasLocation(this) -> "✗ Location not allowed"
            !PhoneLocation.hasBackground(this) -> "△ Location allowed only while using the app (choose \"Allow all the time\")"
            else -> "✓ Location allowed all the time"
        }
        lines += if (PhoneLocation.hasActivity(this)) "✓ Physical activity allowed" else "✗ Physical activity not allowed"
        val saved = prefs.placeStatus.split(",").filter { it.isNotBlank() }
        lines += if (saved.isEmpty()) "Home / Work not saved yet" else "Saved: " + saved.joinToString(", ")
        lines += "Last sync: " + if (prefs.lastSyncMs > 0) fmt.format(Date(prefs.lastSyncMs)) else "never"
        lines += "Waiting to upload: ${Buffer(this).count()}"
        if (prefs.serverPaused) lines += "⏸ Tracking is paused from the dashboard"
        prefs.lastError?.let { lines += "Last problem: $it" }
        status.text = lines.joinToString("\n")
    }

    companion object {
        private const val REQ_LOCATION = 11
        private const val REQ_BACKGROUND = 12
        private const val REQ_ACTIVITY = 13
    }
}
