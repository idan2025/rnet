"""SQLite-backed peer registry.

Stores discovered nodes (from announce capability ads) and serves lookups by
dest hash, capability, and freshness. Pruning marks peers stale rather than
deleting them, so historical routing hints survive fragmentation.
"""
from __future__ import annotations

import time
from typing import List, Optional

from rnet.db.connection import Database
from rnet.protocol.capabilities import Bandwidth, CapabilityAdvertisement


class PeerRegistry:
    STALE_SECONDS = 600  # peer unseen this long is marked unreachable

    def __init__(self, db: Database, stale_seconds: int = STALE_SECONDS):
        self.db = db
        self.stale_seconds = stale_seconds

    def upsert_from_announce(self, adv: CapabilityAdvertisement, dest_hash: str,
                             rssi: Optional[int] = None, hops: Optional[int] = None,
                             now: Optional[int] = None) -> None:
        now = int(now if now is not None else time.time())
        caps = ",".join(adv.caps)
        row = self.db.query_one(
            "SELECT first_seen FROM peers WHERE dest_hash=?", (dest_hash,)
        )
        first_seen = row["first_seen"] if row else now
        self.db.execute(
            """INSERT OR REPLACE INTO peers
               (dest_hash, name, capabilities, fingerprint, last_seen,
                first_seen, reachable, rssi, hops)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                dest_hash,
                adv.name,
                caps,
                adv.fp,
                now,
                first_seen,
                1,
                rssi,
                hops,
            ),
        )

    def mark_unreachable(self, dest_hash: str) -> None:
        self.db.execute(
            "UPDATE peers SET reachable=0 WHERE dest_hash=?", (dest_hash,)
        )

    def get(self, dest_hash: str):
        return self.db.query_one(
            "SELECT * FROM peers WHERE dest_hash=?", (dest_hash,)
        )

    def list_all(self) -> List[dict]:
        rows = self.db.query(
            "SELECT * FROM peers ORDER BY last_seen DESC"
        )
        return [dict(r) for r in rows]

    def list_by_capability(self, cap: str) -> List[dict]:
        # comma-list LIKE match; tolerate start/middle/end of list
        rows = self.db.query(
            """SELECT * FROM peers WHERE
               capabilities = ? OR capabilities LIKE ? OR capabilities LIKE ? OR capabilities LIKE ?
               ORDER BY last_seen DESC""",
            (cap, f"{cap},%", f"%,{cap}", f"%,{cap},%"),
        )
        return [dict(r) for r in rows]

    def find_for_bandwidth(self, bw: Bandwidth) -> List[dict]:
        """Peers whose advertised max bandwidth can serve class ``bw``.

        Only considers reachable peers; used by adaptive delivery routing.
        """
        rows = self.db.query(
            """SELECT p.* FROM peers p
               WHERE p.reachable=1 AND p.last_seen >= ?
               ORDER BY p.last_seen DESC""",
            (int(time.time()) - self.stale_seconds,),
        )
        out = []
        for r in rows:
            # max_bw is not stored in peers table (it lives in the ad); we
            # re-derive from capabilities via CapabilitySet defaults. This is
            # a Phase-1 approximation; Phase 2 persists max_bw on the peer row.
            from rnet.protocol.capabilities import CapabilitySet

            caps = [c for c in (r["capabilities"] or "").split(",") if c]
            if not caps:
                continue
            cs = CapabilitySet(caps)
            if cs.max_bandwidth() >= bw:
                out.append(dict(r))
        return out

    def prune_stale(self, now: Optional[int] = None) -> int:
        now = int(now if now is not None else time.time())
        cutoff = now - self.stale_seconds
        cur = self.db.execute(
            "UPDATE peers SET reachable=0 WHERE last_seen < ? AND reachable=1",
            (cutoff,),
        )
        return cur.rowcount or 0