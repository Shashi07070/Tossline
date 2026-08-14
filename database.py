"""
database.py — SQLite persistence layer.

Tables:
- processed_messages: dedup ledger (source_channel_id + source_message_id unique)
- kv_settings: dynamic runtime configuration (source/target channel, pattern, enabled flag)
- stats: running counters
- match_log: lightweight local audit trail (not sent anywhere)

All access goes through a single aiosqlite connection guarded by an asyncio.Lock,
since Telethon event handlers can fire concurrently.
"""

import asyncio
import json
import time
from typing import Any, Optional

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_channel_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    destination_message_id INTEGER,
    processed_at REAL NOT NULL,
    match_result TEXT NOT NULL,
    UNIQUE(source_channel_id, source_message_id)
);

CREATE TABLE IF NOT EXISTS kv_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS match_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source_message_id INTEGER,
    decision TEXT NOT NULL,
    reason TEXT
);
"""

DEFAULT_STATS = ["checked", "matched", "forwarded", "ignored", "duplicates_prevented"]


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def connect(self):
        self._conn = await aiosqlite.connect(self.path)
        await self._conn.executescript(SCHEMA)
        for key in DEFAULT_STATS:
            await self._conn.execute(
                "INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,)
            )
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    # ---------- Dedup ----------

    async def is_processed(self, source_channel_id: int, source_message_id: int) -> bool:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT 1 FROM processed_messages WHERE source_channel_id=? AND source_message_id=?",
                (source_channel_id, source_message_id),
            )
            row = await cur.fetchone()
            return row is not None

    async def mark_processed(
        self,
        source_channel_id: int,
        source_message_id: int,
        destination_message_id: Optional[int],
        match_result: str,
    ) -> bool:
        """Returns True if newly inserted, False if it already existed (race-safe dedup)."""
        async with self._lock:
            try:
                await self._conn.execute(
                    """INSERT INTO processed_messages
                       (source_channel_id, source_message_id, destination_message_id, processed_at, match_result)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_channel_id, source_message_id, destination_message_id, time.time(), match_result),
                )
                await self._conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    # ---------- Settings ----------

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            cur = await self._conn.execute("SELECT value FROM kv_settings WHERE key=?", (key,))
            row = await cur.fetchone()
            if row is None:
                return default
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                return row[0]

    async def set_setting(self, key: str, value: Any):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO kv_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            await self._conn.commit()

    # ---------- Stats ----------

    async def increment(self, key: str, amount: int = 1):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO stats (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + ?",
                (key, amount, amount),
            )
            await self._conn.commit()

    async def get_stats(self) -> dict:
        async with self._lock:
            cur = await self._conn.execute("SELECT key, value FROM stats")
            rows = await cur.fetchall()
            return {k: v for k, v in rows}

    # ---------- Match log (local only, never forwarded) ----------

    async def log_match(self, source_message_id: Optional[int], decision: str, reason: str = ""):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO match_log (ts, source_message_id, decision, reason) VALUES (?, ?, ?, ?)",
                (time.time(), source_message_id, decision, reason),
            )
            await self._conn.commit()

    async def recent_logs(self, limit: int = 20) -> list:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT ts, source_message_id, decision, reason FROM match_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return await cur.fetchall()
