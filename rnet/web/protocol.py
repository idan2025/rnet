"""RHTTP — Reticulum Hypertext Transfer Protocol.

Request/response structures optimized for RNS: msgpack, signed responses,
content-addressed bodies (small bodies inline; large bodies referenced by a
CAS manifest hash the client fetches separately), and byte-range resume.

Rides RNS link requests to a peer's ``rnet.http`` destination (see
:mod:`rnet.web.transport`). Framed as :class:`FrameType.RHTTP_REQ` /
``RHTTP_RES`` at the wire layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import RNS

from rnet.errors import WireError
from rnet.storage.cas import hash_data


# Methods
GET = "GET"
POST = "POST"
META = "META"  # metadata-only (headers + size + hash, no body)

# Status codes (subset mirroring HTTP semantics)
OK = 200
NOT_FOUND = 404
BAD_REQUEST = 400
FORBIDDEN = 403
RANGE_NOT_SATISFIABLE = 416
INTERNAL_ERROR = 500

# Bodies at or below this size are sent inline; larger bodies are referenced
# by a CAS manifest hash the client fetches via the storage network.
INLINE_BODY_MAX = 16 * 1024


@dataclass
class RHTTPRequest:
    method: str = GET
    path: str = "/"
    query: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    range: Optional[list] = None  # [start, end] inclusive, for resume

    def to_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                "m": self.method,
                "p": self.path,
                "q": self.query,
                "h": self.headers,
                "b": self.body,
                "r": self.range,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "RHTTPRequest":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad RHTTP request: {exc}") from exc
        return cls(
            method=str(d.get("m", GET)),
            path=str(d.get("p", "/")),
            query=dict(d.get("q", {}) or {}),
            headers=dict(d.get("h", {}) or {}),
            body=d.get("b", b"") or b"",
            range=d.get("r", None),
        )


@dataclass
class RHTTPResponse:
    status: int = OK
    headers: Dict[str, str] = field(default_factory=dict)
    content_hash: bytes = b""  # 32 bytes: hash of inline body OR CAS manifest hash
    size: int = 0
    body: bytes = b""          # inline body, empty when content is CAS-referenced
    manifest: bytes = b""      # msgpack(Manifest) for CAS-referenced bodies
    sig: bytes = b""           # host signature over signing_bytes()

    def signing_bytes(self) -> bytes:
        """Bytes the host identity signs: status || content_hash || size."""
        return (
            int(self.status).to_bytes(2, "big")
            + self.content_hash
            + int(self.size).to_bytes(8, "big")
        )

    def sign(self, host_identity: RNS.Identity) -> None:
        self.sig = host_identity.sign(self.signing_bytes())

    def verify(self, host_identity: RNS.Identity) -> bool:
        return host_identity.validate(self.sig, self.signing_bytes())

    def verify_pubkey(self, pubkey: bytes) -> bool:
        ident = RNS.Identity(create_keys=False)
        ident.load_public_key(pubkey)
        return self.verify(ident)

    def to_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {
                "s": int(self.status),
                "h": self.headers,
                "ch": self.content_hash,
                "sz": int(self.size),
                "b": self.body,
                "m": self.manifest,
                "sig": self.sig,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "RHTTPResponse":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad RHTTP response: {exc}") from exc
        return cls(
            status=int(d.get("s", OK)),
            headers=dict(d.get("h", {}) or {}),
            content_hash=d.get("ch", b"") or b"",
            size=int(d.get("sz", 0)),
            body=d.get("b", b"") or b"",
            manifest=d.get("m", b"") or b"",
            sig=d.get("sig", b"") or b"",
        )


def response_for_bytes(body: bytes, ctype: str = "application/octet-stream",
                       status: int = OK, headers: Optional[Dict[str, str]] = None) -> RHTTPResponse:
    """Build an inline response: content_hash = hash of the body."""
    h = dict(headers or {})
    h.setdefault("Content-Type", ctype)
    return RHTTPResponse(
        status=status,
        headers=h,
        content_hash=hash_data(body),
        size=len(body),
        body=body,
    )