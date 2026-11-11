package com.habittracker.collector

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import java.net.HttpURLConnection
import java.net.URL

/** Keeps content clear of the status/navigation bars (Android 15+ draws apps edge to edge). */
fun View.padForSystemBars() {
    setOnApplyWindowInsetsListener { v, insets ->
        if (Build.VERSION.SDK_INT >= 30) {
            val b = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            v.setPadding(b.left, b.top, b.right, b.bottom)
        } else {
            @Suppress("DEPRECATION")
            v.setPadding(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
        }
        insets
    }
}

/**
 * Home screen = the dashboard. Before showing it we wait (up to 20 s) until the server answers: after the phone
 * was idle, Tailscale needs a few seconds to reconnect, and the dashboard would otherwise flash
 * "Are you connected to the internet?". The page menu (☰) comes from the dashboard itself.
 */
class MainActivity : Activity() {
    private lateinit var prefs: Prefs
    private lateinit var web: WebView
    private lateinit var connectingView: LinearLayout
    private lateinit var message: TextView
    private lateinit var spinner: ProgressBar
    private lateinit var retry: Button
    private var pausedAt = 0L
    @Volatile private var attempt = 0

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        actionBar?.hide()
        prefs = Prefs(this)
        SyncWorker.schedule(this)
        val pad = (24 * resources.displayMetrics.density).toInt()

        web = WebView(this).apply {
            settings.javaScriptEnabled = true      // the dashboard needs JS + websockets
            settings.domStorageEnabled = true
            settings.userAgentString = settings.userAgentString + " HabitTrackerApp/" + Updater.installedName(context)
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val url = request.url
                    if (url.scheme == "habittracker") {
                        // e.g. habittracker://settings?update=1 from the dashboard's ☰ menu / "Update app" button
                        if (url.host == "settings")
                            startActivity(Intent(this@MainActivity, SettingsActivity::class.java).setData(url))
                        return true
                    }
                    return !url.toString().startsWith(prefs.dashboardUrl)  // stay in the app only for the dashboard
                }

                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) = Unit

                override fun onPageFinished(view: WebView, url: String?) {
                    connectingView.visibility = View.GONE
                }

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) showProblem("Can't reach your server (${error.description}).")
                }
            }
        }

        // downloads (e.g. a file offered by the dashboard) are handed to the phone's browser
        web.setDownloadListener { url, _, _, _, _ ->
            try { startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url))) } catch (_: Exception) {}
        }

        spinner = ProgressBar(this)
        message = TextView(this).apply {
            textSize = 16f; gravity = Gravity.CENTER; setPadding(0, pad / 2, 0, pad / 2)
        }
        retry = Button(this).apply { text = "Try again"; setOnClickListener { connect() } }
        val settingsBtn = Button(this).apply {
            text = "Settings & sync"
            setOnClickListener { startActivity(Intent(this@MainActivity, SettingsActivity::class.java)) }
        }
        connectingView = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(pad, pad, pad, pad)
            isClickable = true
            setBackgroundColor(resolveBackground())
            addView(spinner); addView(message); addView(retry); addView(settingsBtn)
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(resolveBackground())
            addView(web, FrameLayout.LayoutParams(-1, -1))
            addView(connectingView, FrameLayout.LayoutParams(-1, -1))
            padForSystemBars()
        }
        setContentView(root)
        if (!prefs.configured) startActivity(Intent(this, SettingsActivity::class.java))
        connect()
    }

    private fun resolveBackground(): Int {
        val a = obtainStyledAttributes(intArrayOf(android.R.attr.colorBackground))
        val c = a.getColor(0, 0xFFFFFFFF.toInt()); a.recycle(); return c
    }

    /** Show "Connecting…", wake the Tailscale path with a health check, then load the dashboard. */
    private fun connect() {
        val my = ++attempt
        connectingView.visibility = View.VISIBLE
        spinner.visibility = View.VISIBLE
        retry.visibility = View.GONE
        message.text = "Connecting to your server…"
        Thread {
            val deadline = System.currentTimeMillis() + 20_000
            var ok = false
            while (!ok && System.currentTimeMillis() < deadline && my == attempt) {
                ok = try {
                    val c = URL(prefs.dashboardUrl.trimEnd('/') + "/_stcore/health").openConnection() as HttpURLConnection
                    c.connectTimeout = 5_000; c.readTimeout = 5_000
                    try { c.responseCode == 200 } finally { c.disconnect() }
                } catch (e: Exception) {
                    Thread.sleep(1_000); false
                }
            }
            runOnUiThread {
                if (my != attempt) return@runOnUiThread
                if (ok) web.loadUrl(prefs.dashboardUrl) else showProblem("Can't reach your server.")
            }
        }.start()
    }

    private fun showProblem(what: String) {
        connectingView.visibility = View.VISIBLE
        spinner.visibility = View.GONE
        retry.visibility = View.VISIBLE
        message.text = "$what\n\nIs Tailscale on and connected on this phone?"
    }

    override fun onPause() {
        super.onPause()
        pausedAt = System.currentTimeMillis()
    }

    override fun onResume() {
        super.onResume()
        // after a while in the background the dashboard's live connection is gone: reconnect cleanly
        if (pausedAt > 0 && System.currentTimeMillis() - pausedAt > 120_000) connect()
    }

    @Deprecated("Activity back handling")
    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else @Suppress("DEPRECATION") super.onBackPressed()
    }
}
