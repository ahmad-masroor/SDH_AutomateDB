""" Local cache manager.
A tiny SQLite key-value store.
    - "schema_metadata"  -> JSON dump of all TableInfo objects
    - "schema_graph"     -> pickled NetworkX graph (as bytes)
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

class CacheManager:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value BLOB NOT NULL,
                        value_type TEXT NOT NULL,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def set_json(self, key: str, value: Any) -> None:
        payload = json.dumps(value).encode("utf-8")
        self._set_raw(key, payload, "json")

    def get_json(self, key: str) -> Any | None:
        raw = self._get_raw(key)
        if raw is None:
            return None
        return json.loads(raw.decode("utf-8"))

    def set_bytes(self, key: str, value: bytes) -> None:
        self._set_raw(key, value, "bytes")

    def get_bytes(self, key: str) -> bytes | None:
        return self._get_raw(key)

    def _set_raw(self, key: str, value: bytes, value_type: str) -> None:
        # `with conn:` only commits/rolls back the transaction - it does NOT
        # close the connection. On Windows that keeps the file handle open,
        # which then blocks deletion (e.g. tempfile cleanup). Always close
        # explicitly via try/finally.
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO cache (key, value, value_type, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        value_type = excluded.value_type,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (key, value, value_type),
                )
        finally:
            conn.close()

    def _get_raw(self, key: str) -> bytes | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM cache WHERE key = ?;", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def keys(self) -> list[str]:
        conn = self._connect()
        try:
            return [r[0] for r in conn.execute("SELECT key FROM cache;").fetchall()]
        finally:
            conn.close()