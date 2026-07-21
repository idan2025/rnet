"""Thread-safe SQLite database wrapper with versioned migrations."""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from typing import Any, Iterable, List, Optional, Sequence

from rnet.db.schema import (
    SCHEMA_VERSION,
    assert_known_version,
    migrations_to_apply,
)
from rnet.errors import SchemaError


class Database:
    """A single-connection SQLite wrapper guarded by a reentrant lock.

    Access is serialized so the asyncio loop thread can call into it safely
    while RNS callbacks may also touch it. check_same_thread=False lets any
    thread hold the lock and execute; the lock guarantees one writer at a
    time.
    """

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        # WAL for better concurrent-read behavior and crash safety.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    # -- schema -----------------------------------------------------------
    def _migrate(self) -> None:
        with self._lock:
            cur = self._conn.execute("PRAGMA user_version")
            current = cur.fetchone()[0]
            assert_known_version(current)
            pending = migrations_to_apply(current)
            if not pending:
                return
            # Take a one-time backup before the first migration on this file.
            if current == 0 and os.path.exists(self.path):
                try:
                    shutil.copy2(self.path, self.path + ".bak")
                except OSError:
                    pass
            for version, statements in pending:
                self._conn.execute("BEGIN")
                try:
                    for stmt in statements:
                        self._conn.execute(stmt)
                    self._conn.execute(f"PRAGMA user_version = {version}")
                    self._conn.execute("COMMIT")
                except Exception as exc:  # pragma: no cover - migration failure
                    self._conn.execute("ROLLBACK")
                    raise SchemaError(f"migration to v{version} failed: {exc}") from exc

    # -- execution --------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, params: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchone()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def now(self) -> int:
        return int(time.time())

    def close(self) -> None:
        with self._lock:
            self._conn.close()