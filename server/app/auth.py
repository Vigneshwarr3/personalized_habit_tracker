"""Bearer-token auth.

* Admin token (env HT_ADMIN_TOKEN): dashboard + you; can call everything.
* Device tokens (one per collector, stored as SHA-256 hashes): may only send heartbeats and read /status.
Every client needs a token, including localhost: behind `tailscale serve` all traffic arrives from 127.0.0.1,
so "trust localhost" would trust the whole tailnet.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    role: str              # "admin" | "device"
    device: str | None = None
    source: str | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(conn: sqlite3.Connection, admin_token: str | None, token: str | None) -> Principal | None:
    if not token:
        return None
    if admin_token and hmac.compare_digest(token, admin_token):
        return Principal("admin")
    row = conn.execute(
        "SELECT name, source FROM devices WHERE token_hash=? AND revoked_at IS NULL", (hash_token(token),)
    ).fetchone()
    if row is None:
        return None
    return Principal("device", row["name"], row["source"])


def create_device(conn: sqlite3.Connection, name: str, source: str) -> str:
    token = "ht_" + secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO devices(name, source, token_hash, created_at) VALUES(?,?,?,?)",
        (name, source, hash_token(token), time.time()),
    )
    return token


def revoke_device(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute("UPDATE devices SET revoked_at=? WHERE name=? AND revoked_at IS NULL", (time.time(), name))
    return cur.rowcount > 0
