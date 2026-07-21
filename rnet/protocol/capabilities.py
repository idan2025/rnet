"""Capability advertisement + bandwidth classification (radio-first).

A node advertises its capabilities in the RNS announce ``app_data`` so peers
learn what services it offers without a separate exchange. Each capability
carries an optional **bandwidth requirement** so the network can adapt
delivery to the transport: a `high`-bandwidth app is not served over a 100-byte
LoRa link; a `low`-bandwidth app works everywhere.
"""
from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import msgpack

from rnet.errors import WireError


class Bandwidth(enum.IntEnum):
    """Bandwidth requirement an app/capability declares.

    Ordering is by hunger: LOW < MEDIUM < HIGH.
    """

    LOW = 0     # fits 100-byte packets, seconds/minutes latency (LoRa/radio)
    MEDIUM = 1  # needs ~kB/s, sub-minute latency (packet radio, slow Wi-Fi)
    HIGH = 2    # needs Wi-Fi/fiber, streaming-class (video, large sync)

    @classmethod
    def parse(cls, s: str) -> "Bandwidth":
        s = s.strip().lower()
        for b in cls:
            if b.name.lower() == s:
                return b
        raise WireError(f"unknown bandwidth class: {s!r}")


# All known capability tokens and their default bandwidth requirements.
# Extensible: unknown tokens default to MEDIUM.
_DEFAULTS: Dict[str, Bandwidth] = {
    "messaging": Bandwidth.LOW,
    "naming": Bandwidth.LOW,
    "relay": Bandwidth.LOW,
    "web": Bandwidth.MEDIUM,
    "search": Bandwidth.MEDIUM,
    "storage": Bandwidth.MEDIUM,
    "social": Bandwidth.MEDIUM,
    "apps": Bandwidth.MEDIUM,
}


class CapabilitySet:
    """Validated set of capability tokens with bandwidth metadata."""

    KNOWN = frozenset(_DEFAULTS.keys())

    def __init__(self, caps: Iterable[str]):
        tokens = []
        for c in caps:
            if not isinstance(c, str) or not c:
                raise WireError(f"bad capability token: {c!r}")
            tokens.append(c)
        # de-dup, preserve order
        seen = set()
        self.tokens = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                self.tokens.append(t)

    def bandwidth(self, cap: str) -> Bandwidth:
        return _DEFAULTS.get(cap, Bandwidth.MEDIUM)

    def max_bandwidth(self) -> Bandwidth:
        if not self.tokens:
            return Bandwidth.LOW
        return max(self.bandwidth(c) for c in self.tokens)

    def as_list(self) -> List[str]:
        return list(self.tokens)

    def __contains__(self, cap: str) -> bool:
        return cap in self.tokens

    def __iter__(self):
        return iter(self.tokens)

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"CapabilitySet({self.tokens})"


@dataclass
class CapabilityAdvertisement:
    """The blob carried in a node's RNS announce ``app_data``.

    Kept tiny (see ``NodeConfig.capadv_max_bytes``) because it rides every
    announce on constrained radio links. Free-form metadata lives in the
    Profile, fetched on demand.
    """

    version: int = 1
    name: str = ""
    caps: List[str] = field(default_factory=list)
    prof_sig: bytes = b""  # signature over the node's Profile (binds identity)
    fp: bytes = b""        # 8-byte identity fingerprint
    ts: int = 0
    # Highest bandwidth class this node can serve given its transports.
    # Peers use this to decide whether to route a high-bandwidth request here.
    max_bw: int = int(Bandwidth.LOW)
    # Low-power/sleepy node hint: 1 => expect long announce gaps, prefer S&F.
    low_power: int = 0

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {
                "v": self.version,
                "name": self.name,
                "caps": self.caps,
                "psig": self.prof_sig,
                "fp": self.fp,
                "ts": self.ts,
                "bw": int(self.max_bw),
                "lp": int(self.low_power),
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "CapabilityAdvertisement":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad capability ad: {exc}") from exc
        return cls(
            version=int(d.get("v", 1)),
            name=str(d.get("name", "")),
            caps=list(d.get("caps", [])),
            prof_sig=d.get("psig", b"") or b"",
            fp=d.get("fp", b"") or b"",
            ts=int(d.get("ts", 0)),
            max_bw=int(d.get("bw", int(Bandwidth.LOW))),
            low_power=int(d.get("lp", 0)),
        )

    def caps_set(self) -> CapabilitySet:
        return CapabilitySet(self.caps)

    def max_bandwidth(self) -> Bandwidth:
        return Bandwidth(self.max_bw)