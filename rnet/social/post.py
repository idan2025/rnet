"""Signed, content-addressed social posts.

A :class:`Post` is signed by its author identity and content-addressed: the
post's id is ``hash_data(signed_bytes)``, and the signed bytes are stored as a
CAS block so posts replicate like any other content and survive node loss.
Attachments reference CAS manifest hashes. ``reply_to`` makes threads
(forums). ``community`` optionally addresses a post to a community.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

import RNS

from rnet.errors import SignatureError, WireError
from rnet.identity.util import fingerprint
from rnet.storage.cas import hash_data


@dataclass
class Post:
    version: int = 1
    author: str = ""          # author fingerprint hex
    ts: int = 0
    id: bytes = b""           # 16-byte random id
    body: str = ""
    reply_to: bytes = b""     # parent post hash or empty
    attachments: List[bytes] = field(default_factory=list)  # CAS manifest hashes
    community: str = ""       # community dest hash or empty
    sig: bytes = b""          # author signature over canonical_bytes()

    def canonical_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                "v": self.version,
                "author": self.author,
                "ts": self.ts,
                "id": self.id,
                "body": self.body,
                "reply_to": self.reply_to,
                "attachments": self.attachments,
                "community": self.community,
            },
            use_bin_type=True,
        )

    def to_bytes(self) -> bytes:
        """Full signed bytes (what gets content-addressed + stored in CAS)."""
        import msgpack

        d = msgpack.unpackb(self.canonical_bytes(), raw=False)
        d["sig"] = self.sig
        return msgpack.packb(d, use_bin_type=True)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Post":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad post: {exc}") from exc
        return cls(
            version=int(d.get("v", 1)),
            author=str(d.get("author", "")),
            ts=int(d.get("ts", 0)),
            id=d.get("id", b"") or b"",
            body=str(d.get("body", "")),
            reply_to=d.get("reply_to", b"") or b"",
            attachments=list(d.get("attachments", [])),
            community=str(d.get("community", "")),
            sig=d.get("sig", b"") or b"",
        )

    def sign(self, author_identity: RNS.Identity) -> None:
        self.author = fingerprint(author_identity).hex()
        if not self.id:
            self.id = os.urandom(16)
        self.sig = author_identity.sign(self.canonical_bytes())

    def verify(self, author_identity: RNS.Identity) -> None:
        if fingerprint(author_identity) != bytes.fromhex(self.author):
            raise SignatureError("post author does not match signer fingerprint")
        if not author_identity.validate(self.sig, self.canonical_bytes()):
            raise SignatureError("invalid post signature")

    def verify_pubkey(self, pubkey: bytes) -> None:
        ident = RNS.Identity(create_keys=False)
        ident.load_public_key(pubkey)
        self.verify(ident)

    @property
    def hash(self) -> bytes:
        """Content id = hash of the signed bytes."""
        return hash_data(self.to_bytes())

    def to_dict(self) -> dict:
        return {
            "hash": self.hash.hex(),
            "author": self.author,
            "ts": self.ts,
            "body": self.body,
            "reply_to": self.reply_to.hex() if self.reply_to else "",
            "attachments": [a.hex() for a in self.attachments],
            "community": self.community,
        }


@dataclass
class Follow:
    """A signed follow record: ``follower`` follows ``followed``."""

    follower: str = ""        # fingerprint hex
    followed: str = ""        # fingerprint hex
    ts: int = 0
    sig: bytes = b""          # follower signature over canonical_bytes()

    def canonical_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {"follower": self.follower, "followed": self.followed, "ts": self.ts},
            use_bin_type=True,
        )

    def to_bytes(self) -> bytes:
        import msgpack

        d = msgpack.unpackb(self.canonical_bytes(), raw=False)
        d["sig"] = self.sig
        return msgpack.packb(d, use_bin_type=True)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Follow":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad follow: {exc}") from exc
        return cls(follower=str(d.get("follower", "")),
                   followed=str(d.get("followed", "")),
                   ts=int(d.get("ts", 0)),
                   sig=d.get("sig", b"") or b"")

    def sign(self, follower_identity: RNS.Identity) -> None:
        self.follower = fingerprint(follower_identity).hex()
        self.sig = follower_identity.sign(self.canonical_bytes())

    def verify(self, follower_identity: RNS.Identity) -> None:
        if fingerprint(follower_identity) != bytes.fromhex(self.follower):
            raise SignatureError("follow follower does not match signer")
        if not follower_identity.validate(self.sig, self.canonical_bytes()):
            raise SignatureError("invalid follow signature")