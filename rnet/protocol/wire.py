"""Wire framing: msgpack frames, optional zlib, signed frames, anti-replay.

A :class:`Frame` is the unit that rides RNS packets / link requests. Large
payloads are zlib-compressed. A :class:`SignedFrame` appends a 64-byte
Ed25519 signature so receivers can authenticate the sender identity before
processing the payload. Anti-replay is enforced by the receiver using the
frame's monotonic ``n`` and ``ts`` (see :mod:`rnet.protocol.replay`).
"""
from __future__ import annotations

import enum
import zlib
from dataclasses import dataclass, field
from typing import Optional

import msgpack

from rnet.errors import WireError

PROTOCOL_VERSION = 1
# Payloads at or above this size are zlib-compressed in the frame.
COMPRESS_THRESHOLD = 256

# Packet priority classes for radio-first, bandwidth-constrained links.
# Lower number = higher priority (dequeued first by the core send loop).
PRIORITY_CONTROL = 0   # receipts, acks, announces, link control
PRIORITY_NORMAL = 1    # DMs, profiles, small requests
PRIORITY_BULK = 2      # CAS chunks, large transfers, search bulk results


class FrameType(enum.IntEnum):
    PROFILE = 0
    MESSAGE = 1
    CAPADV = 2
    RECEIPT = 3
    NAME_REC = 4
    RHTTP_REQ = 5
    RHTTP_RES = 6
    CAS_MAN = 7
    SEARCH_Q = 8
    SEARCH_R = 9


@dataclass
class Frame:
    version: int
    type: int
    seq: int          # monotonic per-sender sequence (anti-replay)
    ts: int           # sender timestamp (unix seconds)
    payload: bytes    # type-specific, possibly compressed
    compressed: bool = False
    priority: int = 1  # 0=control, 1=normal, 2=bulk (radio-first scheduling)

    def type_enum(self) -> FrameType:
        return FrameType(self.type)


@dataclass
class SignedFrame:
    """A Frame plus the 64-byte signature over its packed bytes."""

    frame: Frame
    sig: bytes  # 64 bytes
    fp: bytes = b""  # 8-byte sender fingerprint (convenience; verified via sig)


# ---------------------------------------------------------------------------
# compression
# ---------------------------------------------------------------------------
def compress_if_big(data: bytes) -> tuple:
    """Return (payload, compressed_flag). Compress payloads >= threshold."""
    if len(data) >= COMPRESS_THRESHOLD:
        return zlib.compress(data, 9), True
    return data, False


def decompress(payload: bytes, compressed: bool) -> bytes:
    if not compressed:
        return payload
    try:
        return zlib.decompress(payload)
    except zlib.error as exc:  # pragma: no cover - corrupt payload
        raise WireError(f"frame payload decompress failed: {exc}") from exc


# ---------------------------------------------------------------------------
# (un)signed) frame packing
# ---------------------------------------------------------------------------
def _pack_frame_dict(f: Frame) -> bytes:
    return msgpack.packb(
        {
            "v": f.version,
            "t": int(f.type),
            "n": f.seq,
            "ts": f.ts,
            "p": f.payload,
            "c": 1 if f.compressed else 0,
            "pr": int(f.priority),
        },
        use_bin_type=True,
    )


def pack_frame(f: Frame) -> bytes:
    return _pack_frame_dict(f)


def unpack_frame(raw: bytes) -> Frame:
    try:
        d = msgpack.unpackb(raw, raw=False)
    except Exception as exc:
        raise WireError(f"bad frame bytes: {exc}") from exc
    if d.get("v") != PROTOCOL_VERSION:
        raise WireError(f"unsupported frame version {d.get('v')}")
    try:
        ftype = int(d["t"])
    except (KeyError, TypeError) as exc:
        raise WireError(f"bad frame type: {exc}") from exc
    return Frame(
        version=int(d["v"]),
        type=ftype,
        seq=int(d["n"]),
        ts=int(d["ts"]),
        payload=d["p"],
        compressed=bool(d.get("c", 0)),
        priority=int(d.get("pr", 1)),
    )


def pack_signed_frame(sf: SignedFrame) -> bytes:
    """Pack as msgpack([frame_bytes, sig, fp])."""
    return msgpack.packb(
        [pack_frame(sf.frame), sf.sig, sf.fp], use_bin_type=True
    )


def unpack_signed_frame(raw: bytes) -> SignedFrame:
    try:
        d = msgpack.unpackb(raw, raw=False)
    except Exception as exc:
        raise WireError(f"bad signed frame: {exc}") from exc
    if not isinstance(d, (list, tuple)) or len(d) < 2:
        raise WireError("signed frame must be a 2- or 3-element array")
    frame = unpack_frame(d[0])
    sig = d[1]
    fp = d[2] if len(d) > 2 else b""
    if len(sig) != 64:
        raise WireError(f"signature must be 64 bytes, got {len(sig)}")
    return SignedFrame(frame=frame, sig=sig, fp=fp)


def build_signed_frame(
    type_: FrameType,
    seq: int,
    ts: int,
    payload: bytes,
    sign_fn,
    fp: bytes = b"",
    priority: int = 1,
) -> SignedFrame:
    """Helper: compress payload, build Frame, sign it, return SignedFrame.

    ``sign_fn`` is ``callable(bytes)->bytes`` (typically
    ``identity.sign``). The signature is over the packed (compressed) frame so
    the exact bytes the receiver verifies are the exact bytes on the wire.
    ``priority`` schedules sends on constrained radio links (0 control,
    1 normal, 2 bulk).
    """
    payload, compressed = compress_if_big(payload)
    f = Frame(
        version=PROTOCOL_VERSION,
        type=int(type_),
        seq=seq,
        ts=ts,
        payload=payload,
        compressed=compressed,
        priority=priority,
    )
    sig = sign_fn(pack_frame(f))
    return SignedFrame(frame=f, sig=sig, fp=fp)