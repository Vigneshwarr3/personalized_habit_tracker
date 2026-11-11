package com.habittracker.collector

import org.junit.Assert.assertEquals
import org.junit.Test

/** The dashboard is the web app the API serves at /ui/, so its address follows the server URL. */
class DashboardUrlTest {
    @Test fun followsTheServerUrl() {
        assertEquals("https://host.ts.net:8765/ui/", Prefs.dashboardUrlFor("https://host.ts.net:8765"))
    }

    @Test fun toleratesTrailingSlashesAndSpaces() {
        assertEquals("https://host.ts.net:8765/ui/", Prefs.dashboardUrlFor(" https://host.ts.net:8765// "))
    }
}
