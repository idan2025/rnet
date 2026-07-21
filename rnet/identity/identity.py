"""Identity management: RNS identities, signed profiles, keystore.

An *identity* is an ``RNS.Identity`` (Ed25519-style signing + ECDH keypair
under the hood). RNet layers a signed **Profile** on top so a name, display
name, capabilities, and bio are verifiably bound to the identity that claims
them. Private key material lives only in RNS keyfiles on disk; the SQLite
keystore stores *references* (path + metadata), never the private key.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import List, Optional

import RNS

from rnet.errors import IdentityError, SignatureError
from rnet.identity.keystore import IdentityStore
from rnet.identity.util import fingerprint  # noqa: F401


@dataclass
class Profile:
    """Public, signed metadata bound to an identity.

    Serialized canonically via ``to_bytes`` before signing so the signature
    is reproducible by any verifier.
    """

    version: int = 1
    name: str = ""
    display: str = ""
    capabilities: List[str] = field(default_factory=list)
    bio: str = ""
    avatar_hash: bytes = b""  # 32 bytes or empty
    created: int = 0
    node: str = ""  # node dest hash (hex) or ""
    fp: bytes = b""  # 8-byte identity fingerprint

    def __post_init__(self) -> None:
        if len(self.name) > 64:
            raise IdentityError("profile name exceeds 64 chars")
        if len(self.display) > 128:
            raise IdentityError("profile display exceeds 128 chars")
        if len(self.bio) > 1024:
            raise IdentityError("profile bio exceeds 1024 chars")
        if self.avatar_hash and len(self.avatar_hash) != 32:
            raise IdentityError("avatar_hash must be 32 bytes or empty")

    def to_bytes(self) -> bytes:
        """Canonical msgpack representation (what gets signed)."""
        import msgpack

        return msgpack.packb(
            {
                "v": self.version,
                "name": self.name,
                "display": self.display,
                "caps": self.capabilities,
                "bio": self.bio,
                "avatar": self.avatar_hash,
                "created": self.created,
                "node": self.node,
                "fp": self.fp,
            },
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Profile":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise IdentityError(f"bad profile bytes: {exc}") from exc
        return cls(
            version=d.get("v", 1),
            name=d.get("name", ""),
            display=d.get("display", ""),
            capabilities=list(d.get("caps", [])),
            bio=d.get("bio", ""),
            avatar_hash=d.get("avatar", b"") or b"",
            created=int(d.get("created", 0)),
            node=d.get("node", "") or "",
            fp=d.get("fp", b"") or b"",
        )

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "name": self.name,
            "display": self.display,
            "capabilities": list(self.capabilities),
            "bio": self.bio,
            "avatar_hash": self.avatar_hash.hex() if self.avatar_hash else "",
            "created": self.created,
            "node": self.node,
            "fp": self.fp.hex(),
        }


@dataclass
class SignedProfile:
    """A Profile plus the signature over its canonical bytes."""

    profile_bytes: bytes
    sig: bytes  # 64-byte Ed25519 signature
    fp: bytes  # 8-byte fingerprint of the signing identity

    def to_bytes(self) -> bytes:
        import msgpack

        return msgpack.packb(
            {"profile": self.profile_bytes, "sig": self.sig, "fp": self.fp},
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "SignedProfile":
        import msgpack

        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise IdentityError(f"bad signed profile: {exc}") from exc
        return cls(
            profile_bytes=d["profile"],
            sig=d["sig"],
            fp=d.get("fp", b"") or b"",
        )

    def profile(self) -> Profile:
        return Profile.from_bytes(self.profile_bytes)


class IdentityManager:
    """Create, load, and verify identities + profiles.

    Owns an :class:`IdentityStore` (SQLite keystore) and a keys directory.
    """

    def __init__(self, store: IdentityStore, keys_dir: str):
        self.store = store
        self.keys_dir = keys_dir
        os.makedirs(keys_dir, exist_ok=True)

    # -- own identities ---------------------------------------------------
    def create(self, name: str, is_node: bool = False) -> RNS.Identity:
        """Generate a new identity, save its keyfile, register in the store."""
        ident = RNS.Identity()
        keyfile = os.path.join(self.keys_dir, f"{name}.key")
        # to_file raises if the file exists; pick a unique path.
        if os.path.exists(keyfile):
            keyfile = os.path.join(
                self.keys_dir, f"{name}-{fingerprint(ident).hex()[:8]}.key"
            )
        ident.to_file(keyfile)
        self.store.register_own(ident, name, keyfile, is_node=is_node)
        return ident

    def load(self, dest_hash_hex: str) -> Optional[RNS.Identity]:
        """Load an owned identity from its keyfile by dest hash."""
        row = self.store.get_own(dest_hash_hex)
        if not row:
            return None
        ident = RNS.Identity.from_file(row["keyfile"])
        if ident is None:
            raise IdentityError(f"keyfile unreadable: {row['keyfile']}")
        return ident

    def load_by_name(self, name: str) -> Optional[RNS.Identity]:
        row = self.store.get_own_by_name(name)
        if not row:
            return None
        ident = RNS.Identity.from_file(row["keyfile"])
        if ident is None:
            raise IdentityError(f"keyfile unreadable: {row['keyfile']}")
        return ident

    def list_own(self):
        return self.store.list_own()

    # -- profile signing --------------------------------------------------
    def make_profile(
        self,
        identity: RNS.Identity,
        name: str,
        display: str = "",
        capabilities: Optional[List[str]] = None,
        bio: str = "",
        node_dest_hash: str = "",
        avatar_hash: bytes = b"",
        created: Optional[int] = None,
    ) -> SignedProfile:
        """Build and sign a Profile for ``identity``."""
        prof = Profile(
            version=1,
            name=name,
            display=display,
            capabilities=list(capabilities or []),
            bio=bio,
            avatar_hash=avatar_hash,
            created=int(created if created is not None else time.time()),
            node=node_dest_hash,
            fp=fingerprint(identity),
        )
        raw = prof.to_bytes()
        sig = identity.sign(raw)
        return SignedProfile(profile_bytes=raw, sig=sig, fp=prof.fp)

    @staticmethod
    def verify_profile(signed: SignedProfile, identity: RNS.Identity) -> Profile:
        """Verify a SignedProfile against an identity and return the Profile.

        Raises SignatureError if the fingerprint or signature do not match.
        """
        if fingerprint(identity) != signed.fp:
            raise SignatureError("profile fingerprint does not match identity")
        if not identity.validate(signed.sig, signed.profile_bytes):
            raise SignatureError("invalid profile signature")
        return Profile.from_bytes(signed.profile_bytes)

    @staticmethod
    def verify_profile_pubkey(signed: SignedProfile, pubkey: bytes) -> Profile:
        """Verify when only the public key is available (cached identity)."""
        ident = RNS.Identity(create_keys=False)
        ident.load_public_key(pubkey)
        return IdentityManager.verify_profile(signed, ident)