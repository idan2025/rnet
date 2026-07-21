"""Application-layer message fragmentation for constrained radio links.

RNS already chunks large transfers over an *established link* (resource
transfer). But on intermittent, sleeping, 100-byte-packet links a single link
may never stay up long enough to complete a resource. RNet adds an
app-layer fragment protocol so a payload can be:

  - split into small, independently sendable fragments,
  - delivered out of order across multiple short link windows / relays,
  - reassembled by the receiver from whatever fragments arrived,
  - **resumed** by requesting only the missing fragment ids.

A :class:`FragmentHeader` identifies a transfer (random id), the total
fragment count, and the per-fragment index. The receiver buffers fragments in
the ``transfers`` table (Phase 2 schema field; Phase 1 keeps an in-memory
reassembler used by tests and the messenger) and, once complete, yields the
original payload.

Fragments are small: default 80 bytes payload, so a signed fragment frame
fits a 100-byte packet after msgpack + signature overhead on the smallest
transports. Larger transports raise ``fragment_size`` via :class:`FragmentSpec`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import msgpack

from rnet.errors import WireError


@dataclass
class FragmentSpec:
    """Tunable fragmentation for a given transport class."""

    fragment_size: int = 80  # payload bytes per fragment (fits 100-byte packet)


@dataclass
class FragmentHeader:
    transfer_id: bytes  # 16 bytes
    index: int          # 0-based
    total: int          # total fragment count
    payload: bytes      # this fragment's bytes
    final: bool = False  # set on the last fragment for fast-path detection

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {
                "tid": self.transfer_id,
                "i": self.index,
                "n": self.total,
                "p": self.payload,
                "f": 1 if self.final else 0,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "FragmentHeader":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad fragment header: {exc}") from exc
        return cls(
            transfer_id=d.get("tid", b"") or b"",
            index=int(d.get("i", 0)),
            total=int(d.get("n", 0)),
            payload=d.get("p", b"") or b"",
            final=bool(d.get("f", 0)),
        )


def fragment(payload: bytes, spec: Optional[FragmentSpec] = None,
             transfer_id: Optional[bytes] = None) -> List[FragmentHeader]:
    """Split ``payload`` into ordered fragments."""
    spec = spec or FragmentSpec()
    if spec.fragment_size < 16:
        raise WireError("fragment_size too small (min 16)")
    tid = transfer_id or os.urandom(16)
    size = spec.fragment_size
    chunks = [payload[i:i + size] for i in range(0, len(payload), size)] or [b""]
    total = len(chunks)
    return [
        FragmentHeader(
            transfer_id=tid,
            index=i,
            total=total,
            payload=chunk,
            final=(i == total - 1),
        )
        for i, chunk in enumerate(chunks)
    ]


class Reassembler:
    """Collect fragments for one or more transfers and yield completed payloads."""

    def __init__(self) -> None:
        # transfer_id -> {index: payload, total: int}
        self._buffers: Dict[bytes, dict] = {}

    def add(self, hdr: FragmentHeader) -> Optional[bytes]:
        """Accept a fragment. Return the full payload when complete, else None."""
        buf = self._buffers.setdefault(
            hdr.transfer_id, {"parts": {}, "total": hdr.total}
        )
        if hdr.total != buf["total"]:
            # Reject inconsistent total claims (defensive; shouldn't happen).
            if hdr.total > buf["total"]:
                buf["total"] = hdr.total
        buf["parts"][hdr.index] = hdr.payload
        if len(buf["parts"]) == buf["total"] and all(
            i in buf["parts"] for i in range(buf["total"])
        ):
            data = b"".join(buf["parts"][i] for i in range(buf["total"]))
            del self._buffers[hdr.transfer_id]
            return data
        return None

    def missing(self, transfer_id: bytes) -> List[int]:
        """Indices not yet received for a transfer (for resume requests)."""
        buf = self._buffers.get(transfer_id)
        if not buf:
            return list(range(0))
        return [i for i in range(buf["total"]) if i not in buf["parts"]]

    def forget(self, transfer_id: bytes) -> None:
        self._buffers.pop(transfer_id, None)