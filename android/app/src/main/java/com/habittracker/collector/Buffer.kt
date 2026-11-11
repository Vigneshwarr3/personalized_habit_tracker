package com.habittracker.collector

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import java.util.UUID

/**
 * Offline queue on the phone. Heartbeats wait here until the server confirms it stored them.
 * A batch keeps its id across retries so the server can ignore a batch it already has.
 */
class Buffer(ctx: Context) : SQLiteOpenHelper(ctx, "buffer.db", null, 2) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE pending (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, batch TEXT)")
        db.execSQL("CREATE TABLE pending_loc (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) db.execSQL("CREATE TABLE IF NOT EXISTS pending_loc (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
    }

    fun addLocation(payload: String) {
        writableDatabase.insert("pending_loc", null, ContentValues().apply { put("payload", payload) })
    }

    /** Up to 500 waiting location fixes (id, json). The server ignores duplicates, so a retry is harmless. */
    fun locations(): List<Pair<Long, String>> =
        readableDatabase.rawQuery("SELECT id, payload FROM pending_loc ORDER BY id LIMIT 500", null).use {
            val l = mutableListOf<Pair<Long, String>>(); while (it.moveToNext()) l += it.getLong(0) to it.getString(1); l
        }

    fun ackLocations(maxId: Long) {
        writableDatabase.delete("pending_loc", "id <= ?", arrayOf(maxId.toString()))
    }

    fun add(payloads: List<String>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (p in payloads) db.insert("pending", null, ContentValues().apply { put("payload", p) })
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun count(): Long = readableDatabase.compileStatement("SELECT COUNT(*) FROM pending").simpleQueryForLong()

    fun nextBatch(limit: Int = 500): Pair<String, List<String>>? {
        val db = writableDatabase
        var bid: String? = db.rawQuery("SELECT batch FROM pending WHERE batch IS NOT NULL LIMIT 1", null)
            .use { if (it.moveToFirst()) it.getString(0) else null }
        if (bid == null) {
            val ids = db.rawQuery("SELECT id FROM pending ORDER BY id LIMIT $limit", null).use {
                val l = mutableListOf<Long>(); while (it.moveToNext()) l += it.getLong(0); l
            }
            if (ids.isEmpty()) return null
            bid = UUID.randomUUID().toString().replace("-", "")
            db.execSQL("UPDATE pending SET batch=? WHERE id IN (${ids.joinToString(",")})", arrayOf(bid))
        }
        val items = db.rawQuery("SELECT payload FROM pending WHERE batch=? ORDER BY id", arrayOf(bid)).use {
            val l = mutableListOf<String>(); while (it.moveToNext()) l += it.getString(0); l
        }
        return bid to items
    }

    /** Server confirmed the batch -> it no longer needs to wait on the phone. */
    fun ack(batchId: String) {
        writableDatabase.delete("pending", "batch=?", arrayOf(batchId))
    }
}
