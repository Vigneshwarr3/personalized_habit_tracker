package com.habittracker.collector

import android.content.Context

/** App-private settings (server URL, device token, sync cursor). */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("habit_tracker", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = sp.getString("server_url", DEFAULT_API) ?: DEFAULT_API
        set(v) = sp.edit().putString("server_url", v.trim().trimEnd('/')).apply()
    var dashboardUrl: String
        get() = sp.getString("dashboard_url", DEFAULT_DASHBOARD) ?: DEFAULT_DASHBOARD
        set(v) = sp.edit().putString("dashboard_url", v.trim()).apply()
    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) = sp.edit().putString("token", v.trim()).apply()
    var device: String
        get() = sp.getString("device", "android-phone") ?: "android-phone"
        set(v) = sp.edit().putString("device", v.trim()).apply()

    var cursorMs: Long
        get() = sp.getLong("cursor_ms", 0L)
        set(v) = sp.edit().putLong("cursor_ms", v).apply()
    var open: OpenSession?
        get() {
            val pkg = sp.getString("open_pkg", null) ?: return null
            return OpenSession(pkg, sp.getLong("open_start_ms", 0L))
        }
        set(v) {
            sp.edit().apply {
                if (v == null) remove("open_pkg").remove("open_start_ms")
                else putString("open_pkg", v.pkg).putLong("open_start_ms", v.startMs)
            }.apply()
        }

    var serverPaused: Boolean
        get() = sp.getBoolean("paused", false)
        set(v) = sp.edit().putBoolean("paused", v).apply()
    var lastSyncMs: Long
        get() = sp.getLong("last_sync_ms", 0L)
        set(v) = sp.edit().putLong("last_sync_ms", v).apply()
    var lastError: String?
        get() = sp.getString("last_error", null)
        set(v) = sp.edit().putString("last_error", v).apply()

    var activity: String
        get() = sp.getString("activity", "unknown") ?: "unknown"
        set(v) = sp.edit().putString("activity", v).apply()
    var decimals: Int   // coordinate rounding from the server's Settings (3 = ~100 m, 2 = ~1 km)
        get() = sp.getInt("decimals", 3)
        set(v) = sp.edit().putInt("decimals", v).apply()
    var placeStatus: String
        get() = sp.getString("place_status", "") ?: ""
        set(v) = sp.edit().putString("place_status", v).apply()

    val configured: Boolean get() = token.isNotBlank() && serverUrl.startsWith("https://")

    companion object {
        const val DEFAULT_API = "https://your-server.your-tailnet.ts.net:8765"
        const val DEFAULT_DASHBOARD = "https://your-server.your-tailnet.ts.net:8501"
    }
}
