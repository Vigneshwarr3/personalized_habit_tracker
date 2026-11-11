package com.habittracker.collector

import org.junit.Assert.assertEquals
import org.junit.Test

class CoarseTest {
    @Test fun roundsToAbout100mOr1km() {
        assertEquals(39.768, Coarse.round(39.76841, 3), 0.0)
        assertEquals(-86.158, Coarse.round(-86.15812, 3), 0.0)
        assertEquals(39.77, Coarse.round(39.76841, 2), 0.0)
        assertEquals(-86.16, Coarse.round(-86.15812, 2), 0.0)
    }
}
