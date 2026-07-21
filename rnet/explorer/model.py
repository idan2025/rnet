"""Network explorer model: nodes, services, availability, (best-effort) latency.

Reads the peer registry the node maintains, computes a summary, and exposes
per-peer recency / RSSI / hop counts. Latency is best-effort: when RNS link
stats are available the caller can attach RTT samples; otherwise latency is
unknown (None). Testable headless.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from rnet.db.connection import Database
from rnet.discovery.registry import PeerRegistry


class ExplorerModel:
    def __init__(self, db: Database, registry: Optional[PeerRegistry] = None,
                 stale_seconds: int = 600):
        self.db = db
        self.registry = registry or PeerRegistry(db, stale_seconds=stale_seconds)
        # Optional dest_hash -> rtt_ms samples, set by the caller from live links.
        self.rtt_samples: Dict[str, float] = {}

    def set_rtt(self, dest_hash: str, rtt_ms: float) -> None:
        self.rtt_samples[dest_hash] = rtt_ms

    def peers(self) -> List[dict]:
        rows = self.registry.list_all()
        now = int(time.time())
        out = []
        for r in rows:
            d = dict(r)
            d["age"] = now - int(r["last_seen"])
            d["rtt_ms"] = self.rtt_samples.get(r["dest_hash"])
            d["caps_list"] = [c for c in (r["capabilities"] or "").split(",") if c]
            # Reachable = flagged reachable AND seen within the stale window.
            d["reachable"] = 1 if (int(r["reachable"]) == 1
                                   and d["age"] <= self.registry.stale_seconds) else 0
            out.append(d)
        return out

    def capability_histogram(self) -> Dict[str, int]:
        hist: Dict[str, int] = {}
        for p in self.peers():
            for c in p["caps_list"]:
                hist[c] = hist.get(c, 0) + 1
        return hist

    def summary(self) -> dict:
        peers = self.peers()
        reachable = [p for p in peers if p["reachable"]]
        return {
            "nodes": len(peers),
            "reachable": len(reachable),
            "capabilities": self.capability_histogram(),
            "peers": peers,
            "rtt_samples": dict(self.rtt_samples),
        }

    def services(self) -> List[dict]:
        """One row per (peer, capability) for service-oriented views."""
        out = []
        for p in self.peers():
            for c in p["caps_list"]:
                out.append({
                    "cap": c, "dest": p["dest_hash"], "name": p["name"],
                    "reachable": p["reachable"], "rtt_ms": p["rtt_ms"],
                })
        return out