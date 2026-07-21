"""SQLite operations for the messaging subsystem: inbox, outbox, mailbox."""
from __future__ import annotations

import time
from typing import List, Optional

from rnet.db.connection import Database


class InboxStore:
    def __init__(self, db: Database):
        self.db = db

    def put(self, message_id: str, sender: str, recipient: str, kind: int,
            ts: int, body: bytes, ciphertext: bytes = b"", signature: bytes = b"",
            verified: bool = True) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO inbox
               (id, sender, recipient, kind, ts, body, ciphertext,
                received_at, signature, verified)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (message_id, sender, recipient, kind, ts, body, ciphertext,
             int(time.time()), signature, 1 if verified else 0),
        )

    def get(self, message_id: str):
        return self.db.query_one("SELECT * FROM inbox WHERE id=?", (message_id,))

    def list(self, recipient: Optional[str] = None, unread_only: bool = False):
        if recipient is None:
            sql = "SELECT * FROM inbox ORDER BY received_at DESC"
            return self.db.query(sql)
        if unread_only:
            return self.db.query(
                "SELECT * FROM inbox WHERE recipient=? AND read_at IS NULL "
                "ORDER BY received_at DESC",
                (recipient,),
            )
        return self.db.query(
            "SELECT * FROM inbox WHERE recipient=? ORDER BY received_at DESC",
            (recipient,),
        )

    def mark_read(self, message_id: str) -> None:
        self.db.execute(
            "UPDATE inbox SET read_at=? WHERE id=?", (int(time.time()), message_id)
        )

    def unread_count(self, recipient: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM inbox WHERE recipient=? AND read_at IS NULL",
            (recipient,),
        )
        return int(row["n"]) if row else 0


class OutboxStore:
    def __init__(self, db: Database):
        self.db = db

    def queue(self, message_id: str, recipient: str, envelope: bytes,
              max_attempts: int = 12, next_try: Optional[int] = None) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO outbox
               (id, recipient, envelope, attempts, max_attempts, next_try,
                delivered, ack_received, created_at, last_error)
               VALUES (?,?,?,?,0,?,0,0,?,NULL)""",
            (message_id, recipient, envelope, max_attempts,
             int(next_try if next_try is not None else time.time()),
             int(time.time())),
        )

    def pending(self, now: Optional[int] = None) -> List:
        now = int(now if now is not None else time.time())
        return self.db.query(
            "SELECT * FROM outbox WHERE delivered=0 AND next_try<=? "
            "ORDER BY next_try ASC",
            (now,),
        )

    def mark_attempt(self, message_id: str, error: Optional[str] = None,
                     base_delay: float = 30.0) -> None:
        row = self.db.query_one(
            "SELECT attempts, max_attempts FROM outbox WHERE id=?",
            (message_id,),
        )
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        import math
        delay = base_delay * (2 ** min(attempts, 8))
        next_try = int(time.time()) + int(delay)
        self.db.execute(
            "UPDATE outbox SET attempts=?, next_try=?, last_error=? WHERE id=?",
            (attempts, next_try, error, message_id),
        )

    def mark_delivered(self, message_id: str) -> None:
        self.db.execute(
            "UPDATE outbox SET delivered=1, delivered_at=? WHERE id=?",
            (int(time.time()), message_id),
        )

    def mark_acked(self, message_id: str) -> None:
        self.db.execute(
            "UPDATE outbox SET ack_received=1 WHERE id=?", (message_id,)
        )

    def get(self, message_id: str):
        return self.db.query_one("SELECT * FROM outbox WHERE id=?", (message_id,))


class MailboxStore:
    def __init__(self, db: Database):
        self.db = db

    def deposit(self, recipient: str, sender: str, item: bytes,
                ttl: int, now: Optional[int] = None) -> int:
        now = int(now if now is not None else time.time())
        cur = self.db.execute(
            """INSERT INTO mailbox (recipient, sender, item, received_at, expires_at, delivered)
               VALUES (?,?,?,?,?,0)""",
            (recipient, sender, item, now, now + ttl),
        )
        return int(cur.lastrowid)

    def pending_for(self, recipient: str, now: Optional[int] = None) -> List:
        now = int(now if now is not None else time.time())
        return self.db.query(
            "SELECT * FROM mailbox WHERE recipient=? AND delivered=0 AND expires_at>? "
            "ORDER BY received_at ASC",
            (recipient, now),
        )

    def mark_delivered(self, mailbox_id: int) -> None:
        self.db.execute(
            "UPDATE mailbox SET delivered=1 WHERE id=?", (mailbox_id,)
        )

    def expire(self, now: Optional[int] = None) -> int:
        now = int(now if now is not None else time.time())
        cur = self.db.execute(
            "DELETE FROM mailbox WHERE expires_at<?", (now,)
        )
        return cur.rowcount or 0