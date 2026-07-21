"""Anti-replay sliding window.

Backed by the ``replay_window`` SQLite table. A frame (sender, seq, ts) is
accepted only if:
  - ``ts`` is within ``±clock_skew`` of the receiver's clock (unless
    ``clock_skew`` is 0, which disables time checks for nodes without a
    reliable clock), and
  - ``seq`` is newer than the sender's stored high-water minus the window
    width, and not already seen.

The window is rebuilt per sender from the DB on check; this is cheap because
rows are pruned to ``window`` rows per sender on each accepted write.
"""
from __future__ import annotations

import time
from typing import Optional

from rnet.db.connection import Database
from rnet.errors import ReplayError
from rnet.protocol.wire import Frame


class ReplayWindow:
    def __init__(self, db: Database, window: int = 64, clock_skew: int = 300):
        self.db = db
        self.window = window
        self.clock_skew = clock_skew

    def check(self, sender: str, frame: Frame, now: Optional[int] = None) -> None:
        """Raise ReplayError if the frame is a replay or stale."""
        now = int(now if now is not None else time.time())
        if self.clock_skew > 0:
            if abs(frame.ts - now) > self.clock_skew:
                raise ReplayError(
                    f"frame ts {frame.ts} outside clock skew of {now}"
                )
        rows = self.db.query(
            "SELECT seq FROM replay_window WHERE sender=? ORDER BY seq DESC",
            (sender,),
        )
        seen = {r["seq"] for r in rows}
        high = max(seen) if seen else -1
        low = high - self.window
        if frame.seq <= low:
            raise ReplayError(
                f"frame seq {frame.seq} below window (high={high})"
            )
        if frame.seq in seen:
            raise ReplayError(f"frame seq {frame.seq} already seen")

    def remember(self, sender: str, frame: Frame, now: Optional[int] = None) -> None:
        """Record an accepted frame and prune old rows for the sender."""
        now = int(now if now is not None else time.time())
        self.db.execute(
            "INSERT OR REPLACE INTO replay_window (sender, seq, seen_at) VALUES (?,?,?)",
            (sender, frame.seq, now),
        )
        # Prune to the last `window` rows for this sender.
        self.db.execute(
            """DELETE FROM replay_window WHERE sender=? AND seq < (
                   SELECT MIN(seq) FROM (
                       SELECT seq FROM replay_window WHERE sender=?
                       ORDER BY seq DESC LIMIT ?
                   )
               )""",
            (sender, sender, self.window),
        )

    def check_and_remember(self, sender: str, frame: Frame, now: Optional[int] = None) -> None:
        self.check(sender, frame, now=now)
        self.remember(sender, frame, now=now)