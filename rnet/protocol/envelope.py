"""Message envelopes, bodies, and delivery receipts.

An :class:`Envelope` is the on-wire message container: it names sender and
recipient by destination hash, carries a per-message id and nonce, and a
ciphertext body encrypted to the recipient identity. The cleartext
:class:`Body` inside may reference CAS attachments (Phase 2). Envelopes are
wrapped in signed frames at the transport layer (see :mod:`rnet.protocol.wire`).

Radio-first note: the body ciphertext may exceed a single RNS packet. For
low-bandwidth transports the messenger fragments large bodies into CAS
chunks and ships a small envelope whose body references the chunk manifest;
for tiny messages the whole body fits inline. See ``docs/RADIO_FIRST.md``.
"""
from __future__ import annotations

import enum
import os
from dataclasses import dataclass, field
from typing import List, Optional

import msgpack

from rnet.errors import MessageError, WireError


class MessageKind(enum.IntEnum):
    DM = 0
    GROUP = 1
    CHANNEL = 2


@dataclass
class Body:
    """Cleartext message body (inside the encrypted envelope)."""

    text: str = ""
    files: List[dict] = field(default_factory=list)  # [{hash, size, name}]
    reply: bytes = b""  # message id being replied to (16 bytes or empty)
    # Bandwidth hint from the sender so relays/recipient can schedule delivery.
    # 0=low,1=medium,2=high (matches rnet.protocol.capabilities.Bandwidth).
    bw: int = 0

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {
                "text": self.text,
                "files": self.files,
                "reply": self.reply,
                "bw": int(self.bw),
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Body":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise MessageError(f"bad message body: {exc}") from exc
        return cls(
            text=str(d.get("text", "")),
            files=list(d.get("files", [])),
            reply=d.get("reply", b"") or b"",
            bw=int(d.get("bw", 0)),
        )


@dataclass
class Envelope:
    """On-wire message container."""

    version: int = 1
    sender: str = ""        # sender dest hash (hex)
    recipient: str = ""     # recipient dest hash (hex) or group hash
    kind: int = int(MessageKind.DM)
    id: bytes = b""         # 16-byte message id
    ts: int = 0
    ciphertext: bytes = b""  # body encrypted to recipient identity
    nonce: bytes = b""       # per-message nonce (16 bytes)

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {
                "v": self.version,
                "from": self.sender,
                "to": self.recipient,
                "kind": int(self.kind),
                "id": self.id,
                "ts": self.ts,
                "ct": self.ciphertext,
                "n": self.nonce,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Envelope":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad envelope: {exc}") from exc
        return cls(
            version=int(d.get("v", 1)),
            sender=str(d.get("from", "")),
            recipient=str(d.get("to", "")),
            kind=int(d.get("kind", int(MessageKind.DM))),
            id=d.get("id", b"") or b"",
            ts=int(d.get("ts", 0)),
            ciphertext=d.get("ct", b"") or b"",
            nonce=d.get("n", b"") or b"",
        )

    def message_id_hex(self) -> str:
        return self.id.hex()

    @staticmethod
    def new_id() -> bytes:
        return os.urandom(16)


@dataclass
class Receipt:
    """Signed delivery confirmation for a message id."""

    version: int = 1
    message_id: bytes = b""  # 16 bytes
    by: str = ""              # acknowledging identity dest hash (hex)
    ts: int = 0
    sig: bytes = b""          # signature over (message_id || by || ts)

    def signing_bytes(self) -> bytes:
        return self.message_id + self.by.encode("ascii") + str(self.ts).encode("ascii")

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {
                "v": self.version,
                "mid": self.message_id,
                "by": self.by,
                "ts": self.ts,
                "sig": self.sig,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Receipt":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad receipt: {exc}") from exc
        return cls(
            version=int(d.get("v", 1)),
            message_id=d.get("mid", b"") or b"",
            by=str(d.get("by", "")),
            ts=int(d.get("ts", 0)),
            sig=d.get("sig", b"") or b"",
        )