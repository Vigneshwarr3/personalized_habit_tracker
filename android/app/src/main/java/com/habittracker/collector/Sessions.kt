package com.habittracker.collector

/**
 * Pure logic (no Android APIs, unit-tested): turns the phone's foreground/screen events into
 * "app X was in front from t1 to t2" sessions. Screen off / lock / shutdown end the current session.
 */
data class Ev(val timeMs: Long, val type: Type, val pkg: String? = null) {
    enum class Type { RESUMED, PAUSED, SCREEN_OFF, SCREEN_ON, LOCKED, UNLOCKED, SHUTDOWN }
}

data class Session(val pkg: String, val startMs: Long, val endMs: Long)

/** An app that is still in front when we stop reading; carried over to the next run. */
data class OpenSession(val pkg: String, val startMs: Long)

object SessionBuilder {
    /** Server accepts intervals up to 1 h; long sessions are sent in 50-min pieces (the server re-joins them). */
    const val MAX_CHUNK_MS = 50L * 60 * 1000
    const val MIN_SESSION_MS = 1000L

    fun build(events: List<Ev>, open: OpenSession?, nowMs: Long, ignore: Set<String>): Pair<List<Session>, OpenSession?> {
        val out = mutableListOf<Session>()
        var curPkg: String? = open?.pkg
        var curStart: Long = open?.startMs ?: 0L

        fun close(t: Long) {
            val pkg = curPkg ?: return
            var a = curStart
            while (t - a >= MIN_SESSION_MS) {
                val b = minOf(t, a + MAX_CHUNK_MS)
                out += Session(pkg, a, b)
                a = b
            }
            curPkg = null
        }

        for (e in events.sortedBy { it.timeMs }) {
            when (e.type) {
                Ev.Type.RESUMED -> if (e.pkg != null && e.pkg != curPkg) {
                    close(e.timeMs)
                    if (e.pkg !in ignore) {
                        curPkg = e.pkg
                        curStart = e.timeMs
                    }
                }
                Ev.Type.PAUSED -> if (e.pkg == curPkg) close(e.timeMs)
                Ev.Type.SCREEN_OFF, Ev.Type.LOCKED, Ev.Type.SHUTDOWN -> close(e.timeMs)
                Ev.Type.SCREEN_ON, Ev.Type.UNLOCKED -> Unit  // the app that comes to front sends RESUMED
            }
        }
        val stillOpen = curPkg
        return if (stillOpen != null) {
            close(nowMs)
            out to OpenSession(stillOpen, nowMs)
        } else {
            out to null
        }
    }
}
