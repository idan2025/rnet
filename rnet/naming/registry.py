"""SQLite cache of ``.rns`` name records."""
from __future__ import annotations

import time
from typing import List, Optional

from rnet.db.connection import Database
from rnet.naming.records import NameRecord


class NameRegistry:
    """Persistent name-record cache with TTL + highest-seq-wins semantics."""

    def __init__(self, db: Database):
        self.db = db

    def put(self, record: NameRecord, cached_at: Optional[int] = None) -> bool:
        """Insert if (name, seq) is new or higher than the cached seq.

        Returns True if the record was accepted (newer than cache).
        """
        cached_at = int(cached_at if cached_at is not None else time.time())
        current = self.get(record.name)
        if current is not None and current.seq >= record.seq:
            return False
        self.db.execute(
            """INSERT OR REPLACE INTO name_records
               (name, seq, owner, node, services, ttl, expires_at, sig, cached_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                record.name,
                record.seq,
                record.owner,
                record.node,
                record.to_bytes(),  # store full record blob in `services` col
                record.ttl,
                record.expires_at(),
                record.sig,
                cached_at,
            ),
        )
        return True

    def get(self, name: str) -> Optional[NameRecord]:
        """Return the highest-seq non-expired record for ``name``, or None."""
        now = int(time.time())
        row = self.db.query_one(
            """SELECT * FROM name_records WHERE name=?
               ORDER BY seq DESC LIMIT 1""",
            (name,),
        )
        if not row:
            return None
        # full record blob stored in services column
        return NameRecord.from_bytes(bytes(row["services"]))

    def get_raw(self, name: str):
        """Return the raw DB row (for expiry inspection)."""
        return self.db.query_one(
            "SELECT * FROM name_records WHERE name=? ORDER BY seq DESC LIMIT 1",
            (name,),
        )

    def list(self) -> List[dict]:
        rows = self.db.query(
            "SELECT name, seq, owner, node, expires_at FROM name_records ORDER BY name"
        )
        return [dict(r) for r in rows]

    def expire(self, now: Optional[int] = None) -> int:
        now = int(now if now is not None else time.time())
        cur = self.db.execute(
            "DELETE FROM name_records WHERE expires_at < ?", (now,)
        )
        return cur.rowcount or 0

    def is_stale(self, name: str, now: Optional[int] = None) -> bool:
        now = int(now if now is not None else time.time())
        row = self.get_raw(name)
        if not row:
            return True
        return row["expires_at"] <= now