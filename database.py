"""
database.py — SQLite persistence layer (multi-service architecture).

IMPORTANT: this file was migrated from the original single-source/target
"toss pattern" version. Migration is additive/non-destructive:
  - Old tables (`processed_messages`, `kv_settings`, `stats`, `match_log`)
    are left in place (never dropped) so no existing data is lost.
  - New tables (`services`, `blacklist`, `processed_messages_v2`,
    `event_log`) are created alongside them for the new multi-service,
    filter-based forwarding model.
  - `stats` (global counters) is reused as-is with a new set of keys
    (received/forwarded/blocked/duplicates_prevented/errors) instead of
    the old toss-matcher keys — old keys, if present, are simply unused now.

Tables (new):
- services: one row per source→target forwarding service
- blacklist: global admin-controlled blocked words/phrases
- processed_messages_v2: dedup ledger, keyed by (service_id, source_channel_id, source_message_id)
- event_log: local audit trail (received/forwarded/blocked/duplicate/error) — never sent to any channel
- stats: global running counters (reused from the original schema)

All access goes through a single aiosqlite connection guarded by an asyncio.Lock,
since Telethon event handlers can fire concurrently.
"""

import asyncio
import time
from typing import Any, Optional

import aiosqlite

SCHEMA = """
-- Legacy tables kept for backward compatibility / no data loss.
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

-- New multi-service architecture tables.
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_channel_id INTEGER NOT NULL,
    target_channel_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    forwarded_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    last_forwarded_at REAL,
    last_error TEXT,
    UNIQUE(source_channel_id, target_channel_id)
);

CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL UNIQUE COLLATE NOCASE,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_messages_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL,
    source_channel_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    destination_message_id INTEGER,
    processed_at REAL NOT NULL,
    result TEXT NOT NULL,
    UNIQUE(service_id, source_channel_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS idx_processed_v2_lookup
    ON processed_messages_v2(service_id, source_channel_id, source_message_id);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    service_id INTEGER,
    source_message_id INTEGER,
    event TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_log_ts ON event_log(ts);
"""

# New global counter keys used by the multi-service system.
DEFAULT_STATS = ["received", "forwarded", "blocked", "duplicates_prevented", "errors"]


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

    # ================= Services =================

    async def add_service(self, source_channel_id: int, target_channel_id: int) -> int:
        async with self._lock:
            cur = await self._conn.execute(
                """INSERT INTO services (source_channel_id, target_channel_id, enabled, created_at)
                   VALUES (?, ?, 1, ?)""",
                (source_channel_id, target_channel_id, time.time()),
            )
            await self._conn.commit()
            return cur.lastrowid

    async def get_service(self, service_id: int) -> Optional[dict]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT id, source_channel_id, target_channel_id, enabled, created_at, "
                "forwarded_count, blocked_count, last_forwarded_at, last_error "
                "FROM services WHERE id=?",
                (service_id,),
            )
            row = await cur.fetchone()
            return self._service_row_to_dict(row) if row else None

    async def list_services(self) -> list[dict]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT id, source_channel_id, target_channel_id, enabled, created_at, "
                "forwarded_count, blocked_count, last_forwarded_at, last_error "
                "FROM services ORDER BY id"
            )
            rows = await cur.fetchall()
            return [self._service_row_to_dict(r) for r in rows]

    async def list_enabled_services(self) -> list[dict]:
        services = await self.list_services()
        return [s for s in services if s["enabled"]]

    async def set_service_enabled(self, service_id: int, enabled: bool) -> bool:
        async with self._lock:
            cur = await self._conn.execute(
                "UPDATE services SET enabled=? WHERE id=?", (1 if enabled else 0, service_id)
            )
            await self._conn.commit()
            return cur.rowcount > 0

    async def remove_service(self, service_id: int) -> bool:
        async with self._lock:
            cur = await self._conn.execute("DELETE FROM services WHERE id=?", (service_id,))
            await self._conn.commit()
            return cur.rowcount > 0

    async def set_all_services_enabled(self, enabled: bool):
        async with self._lock:
            await self._conn.execute("UPDATE services SET enabled=?", (1 if enabled else 0,))
            await self._conn.commit()

    async def record_service_forward(self, service_id: int):
        async with self._lock:
            await self._conn.execute(
                "UPDATE services SET forwarded_count = forwarded_count + 1, last_forwarded_at=? WHERE id=?",
                (time.time(), service_id),
            )
            await self._conn.commit()

    async def record_service_block(self, service_id: int):
        async with self._lock:
            await self._conn.execute(
                "UPDATE services SET blocked_count = blocked_count + 1 WHERE id=?",
                (service_id,),
            )
            await self._conn.commit()

    async def record_service_error(self, service_id: int, error: str):
        async with self._lock:
            await self._conn.execute(
                "UPDATE services SET last_error=? WHERE id=?", (error[:500], service_id)
            )
            await self._conn.commit()

    @staticmethod
    def _service_row_to_dict(row) -> dict:
        return {
            "id": row[0],
            "source_channel_id": row[1],
            "target_channel_id": row[2],
            "enabled": bool(row[3]),
            "created_at": row[4],
            "forwarded_count": row[5],
            "blocked_count": row[6],
            "last_forwarded_at": row[7],
            "last_error": row[8],
        }

    # ================= Blacklist =================

    async def add_blacklist_term(self, term: str) -> bool:
        term = term.strip()
        if not term:
            return False
        async with self._lock:
            try:
                await self._conn.execute(
                    "INSERT INTO blacklist (term, added_at) VALUES (?, ?)", (term, time.time())
                )
                await self._conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False  # already present

    async def remove_blacklist_term(self, term: str) -> bool:
        async with self._lock:
            cur = await self._conn.execute(
                "DELETE FROM blacklist WHERE term = ? COLLATE NOCASE", (term.strip(),)
            )
            await self._conn.commit()
            return cur.rowcount > 0

    async def clear_blacklist(self) -> int:
        async with self._lock:
            cur = await self._conn.execute("DELETE FROM blacklist")
            await self._conn.commit()
            return cur.rowcount

    async def list_blacklist(self) -> list[str]:
        async with self._lock:
            cur = await self._conn.execute("SELECT term FROM blacklist ORDER BY term COLLATE NOCASE")
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    # ================= Dedup (multi-service) =================

    async def is_processed(self, service_id: int, source_channel_id: int, source_message_id: int) -> bool:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT 1 FROM processed_messages_v2 "
                "WHERE service_id=? AND source_channel_id=? AND source_message_id=?",
                (service_id, source_channel_id, source_message_id),
            )
            row = await cur.fetchone()
            return row is not None

    async def mark_processed(
        self,
        service_id: int,
        source_channel_id: int,
        source_message_id: int,
        destination_message_id: Optional[int],
        result: str,
    ) -> bool:
        """Returns True if newly inserted, False if it already existed (race-safe dedup)."""
        async with self._lock:
            try:
                await self._conn.execute(
                    """INSERT INTO processed_messages_v2
                       (service_id, source_channel_id, source_message_id, destination_message_id, processed_at, result)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (service_id, source_channel_id, source_message_id, destination_message_id, time.time(), result),
                )
                await self._conn.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    # ================= Settings (kept for compatibility) =================

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            cur = await self._conn.execute("SELECT value FROM kv_settings WHERE key=?", (key,))
            row = await cur.fetchone()
            if row is None:
                return default
            return row[0]

    async def set_setting(self, key: str, value: str):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO kv_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )
            await self._conn.commit()

    # ================= Global stats =================

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

    # ================= Event log (local only, never forwarded) =================

    async def log_event(
        self,
        event: str,
        service_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        detail: str = "",
    ):
        async with self._lock:
            await self._conn.execute(
                "INSERT INTO event_log (ts, service_id, source_message_id, event, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (time.time(), service_id, source_message_id, event, detail),
            )
            await self._conn.commit()

    async def recent_events(self, limit: int = 20, service_id: Optional[int] = None) -> list:
        async with self._lock:
            if service_id is not None:
                cur = await self._conn.execute(
                    "SELECT ts, service_id, source_message_id, event, detail FROM event_log "
                    "WHERE service_id=? ORDER BY id DESC LIMIT ?",
                    (service_id, limit),
                )
            else:
                cur = await self._conn.execute(
                    "SELECT ts, service_id, source_message_id, event, detail FROM event_log "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            return await cur.fetchall()

    async def recent_errors(self, limit: int = 20) -> list:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT ts, service_id, source_message_id, detail FROM event_log "
                "WHERE event='error' ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return await cur.fetchall()
