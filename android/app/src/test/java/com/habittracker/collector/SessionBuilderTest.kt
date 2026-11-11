package com.habittracker.collector

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SessionBuilderTest {
    private val ME = setOf("com.habittracker.collector")
    private fun r(t: Long, p: String) = Ev(t, Ev.Type.RESUMED, p)
    private fun p(t: Long, pkg: String) = Ev(t, Ev.Type.PAUSED, pkg)

    @Test fun appSwitchesBecomeSessions() {
        val (s, open) = SessionBuilder.build(
            listOf(r(0, "a"), p(10_000, "a"), r(10_000, "b"), p(70_000, "b")), null, 100_000, ME)
        assertEquals(listOf(Session("a", 0, 10_000), Session("b", 10_000, 70_000)), s)
        assertNull(open)
    }

    @Test fun screenOffAndLockEndTheSession() {
        val (s, _) = SessionBuilder.build(
            listOf(r(0, "a"), Ev(30_000, Ev.Type.SCREEN_OFF), r(60_000, "b"), Ev(90_000, Ev.Type.LOCKED)),
            null, 200_000, ME)
        assertEquals(listOf(Session("a", 0, 30_000), Session("b", 60_000, 90_000)), s)
    }

    @Test fun appStillInFrontIsCarriedToNextRun() {
        val (s1, open1) = SessionBuilder.build(listOf(r(0, "yt")), null, 60_000, ME)
        assertEquals(listOf(Session("yt", 0, 60_000)), s1)
        assertEquals(OpenSession("yt", 60_000), open1)
        // next run: no events at all (still watching) -> continues from the carried-over start
        val (s2, open2) = SessionBuilder.build(emptyList(), open1, 120_000, ME)
        assertEquals(listOf(Session("yt", 60_000, 120_000)), s2)
        assertEquals(OpenSession("yt", 120_000), open2)
        // then the screen turns off
        val (s3, open3) = SessionBuilder.build(listOf(Ev(150_000, Ev.Type.SCREEN_OFF)), open2, 180_000, ME)
        assertEquals(listOf(Session("yt", 120_000, 150_000)), s3)
        assertNull(open3)
    }

    @Test fun longSessionsAreChunked() {
        val hour = 3_600_000L
        val (s, _) = SessionBuilder.build(listOf(r(0, "movie"), p(2 * hour, "movie")), null, 3 * hour, ME)
        assertEquals(3, s.size)
        assertEquals(s.sumOf { it.endMs - it.startMs }, 2 * hour)
        s.forEach { assert(it.endMs - it.startMs <= SessionBuilder.MAX_CHUNK_MS) }
    }

    @Test fun ownAppAndTinySessionsAreIgnored() {
        val (s, _) = SessionBuilder.build(
            listOf(r(0, "com.habittracker.collector"), r(5_000, "a"), r(5_500, "b"), p(9_000, "b")), null, 10_000, ME)
        assertEquals(listOf(Session("b", 5_500, 9_000)), s)  // 'a' lasted 0.5 s
    }

    @Test fun pauseOfAnotherAppDoesNotEndCurrent() {
        val (s, _) = SessionBuilder.build(listOf(r(0, "a"), r(1_000, "b"), p(1_200, "a"), p(5_000, "b")), null, 9_000, ME)
        assertEquals(listOf(Session("a", 0, 1_000), Session("b", 1_000, 5_000)), s)
    }
}
