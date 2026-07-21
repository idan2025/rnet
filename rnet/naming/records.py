"""``.rns`` name records: signed, decentralized ownership.

A :class:`NameRecord` binds a name (e.g. ``example``) to an owning identity
(its destination hash + fingerprint), a hosting node, and the services offered.
Ownership is proven by a signature from the owner identity. Records carry a
monotonic ``seq`` so a higher-seq record replaces a lower one for the same
name, and a ``ttl`` for cache expiry. Transfers set ``prev`` to the previous
owner's fingerprint; a valid transfer is signed by the new owner and chains
to the prior record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import RNS

from rnet.errors import SignatureError, WireError
from rnet.identity.util import fingerprint


@dataclass
class NameRecord:
    version: int = 1
    name: str = ""           # without .rns
    owner: str = ""          # owner identity dest hash (hex)
    fp: bytes = b""          # 8-byte owner fingerprint
    node: str = ""           # hosting node dest hash (hex)
    services: List[dict] = field(default_factory=list)  # [{cap, dest}]
    seq: int = 0             # monotonic; higher replaces lower
    ts: int = 0
    ttl: int = 86400
    prev: bytes = b""        # previous owner fingerprint (transfers), 8 bytes or empty
    sig: bytes = b""         # owner signature over canonical_bytes()

    def canonical_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                "v": self.version,
                "name": self.name,
                "owner": self.owner,
                "fp": self.fp,
                "node": self.node,
                "services": self.services,
                "seq": self.seq,
                "ts": self.ts,
                "ttl": self.ttl,
                "prev": self.prev,
            },
            use_bin_type=True,
        )

    def to_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                **msgpack.unpackb(self.canonical_bytes(), raw=False),
                "sig": self.sig,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "NameRecord":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad name record: {exc}") from exc
        return cls(
            version=int(d.get("v", 1)),
            name=str(d.get("name", "")),
            owner=str(d.get("owner", "")),
            fp=d.get("fp", b"") or b"",
            node=str(d.get("node", "")),
            services=list(d.get("services", [])),
            seq=int(d.get("seq", 0)),
            ts=int(d.get("ts", 0)),
            ttl=int(d.get("ttl", 86400)),
            prev=d.get("prev", b"") or b"",
            sig=d.get("sig", b"") or b"",
        )

    def expires_at(self) -> int:
        return self.ts + self.ttl

    def sign(self, owner_identity: RNS.Identity) -> None:
        self.fp = fingerprint(owner_identity)
        self.sig = owner_identity.sign(self.canonical_bytes())

    def verify(self, owner_identity: RNS.Identity) -> None:
        if fingerprint(owner_identity) != self.fp:
            raise SignatureError("name record fp does not match owner identity")
        # The owner id is derived from the signing fingerprint; a record where
        # owner != fp.hex() is a forgery (someone signed for a name they don't own).
        if self.owner != self.fp.hex():
            raise SignatureError("name record owner does not match signer fingerprint")
        if not owner_identity.validate(self.sig, self.canonical_bytes()):
            raise SignatureError("invalid name record signature")

    def verify_pubkey(self, pubkey: bytes) -> None:
        ident = RNS.Identity(create_keys=False)
        ident.load_public_key(pubkey)
        self.verify(ident)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "owner": self.owner,
            "fp": self.fp.hex(),
            "node": self.node,
            "services": list(self.services),
            "seq": self.seq,
            "ts": self.ts,
            "ttl": self.ttl,
            "prev": self.prev.hex() if self.prev else "",
        }


def is_transfer(new_record: NameRecord, old_record: Optional[NameRecord]) -> bool:
    """A transfer sets ``prev`` to the prior owner's fingerprint."""
    if old_record is None:
        return bool(new_record.prev)
    return new_record.prev == old_record.fp and new_record.owner != old_record.owner