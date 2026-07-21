"""Group + channel messaging via a shared group identity.

Model: a group has its own :class:`RNS.Identity` (the *group key*). The
founder creates it and invites members by encrypting the group private key to
each member's identity (sent as a DM attachment). Any member holding the group
private key can decrypt group messages. Group messages are envelopes with
``kind=GROUP`` (or ``CHANNEL``) and the body encrypted to the group identity,
so relays still can't read them.

This is simple and works on store-and-forward; the tradeoff is that adding a
member requires distributing the key, and removing one means rotating the group
key (re-create + re-invite remaining members).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

import RNS

from rnet.db.connection import Database
from rnet.errors import MessageError
from rnet.identity.util import fingerprint
from rnet.protocol import Envelope, MessageKind
from rnet.storage.cas import hash_data

log = logging.getLogger(__name__)


class Group:
    """A group identity + metadata."""

    def __init__(self, identity: RNS.Identity, name: str, founder: str,
                 keyfile: str = "", created: int = 0):
        self.identity = identity
        self.name = name
        self.founder = founder
        self.keyfile = keyfile
        self.created = created or int(time.time())

    @property
    def dest_hash(self) -> str:
        return fingerprint(self.identity).hex()


class GroupRegistry:
    """Loads/stores groups this node is a member of."""

    def __init__(self, db: Database, keys_dir: str):
        self.db = db
        self.keys_dir = keys_dir
        os.makedirs(keys_dir, exist_ok=True)
        self._cache: Dict[str, RNS.Identity] = {}
        self._load_all()

    def _load_all(self) -> None:
        rows = self.db.query("SELECT * FROM groups WHERE is_member=1")
        for r in rows:
            if r["keyfile"] and os.path.exists(r["keyfile"]):
                ident = RNS.Identity.from_file(r["keyfile"])
                if ident is not None:
                    self._cache[r["group_dest"]] = ident

    def add(self, group: Group) -> None:
        self.db.execute(
            """INSERT OR REPLACE INTO groups
               (group_dest, name, founder, created, keyfile, is_member)
               VALUES (?,?,?,?,?,1)""",
            (group.dest_hash, group.name, group.founder, group.created, group.keyfile),
        )
        self._cache[group.dest_hash] = group.identity

    def get(self, group_dest_hash: str) -> Optional[RNS.Identity]:
        return self._cache.get(group_dest_hash)

    def list(self) -> List[dict]:
        rows = self.db.query("SELECT * FROM groups WHERE is_member=1 ORDER BY created DESC")
        return [dict(r) for r in rows]

    def add_member_record(self, group_dest: str, member_fp: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO group_members (group_dest, member, added) VALUES (?,?,?)",
            (group_dest, member_fp, int(time.time())),
        )

    def members(self, group_dest: str) -> List[str]:
        rows = self.db.query(
            "SELECT member FROM group_members WHERE group_dest=?", (group_dest,)
        )
        return [r["member"] for r in rows]


class GroupManager:
    """Create groups, invite members, build group messages."""

    def __init__(self, registry: GroupRegistry):
        self.registry = registry

    def create_group(self, founder_identity: RNS.Identity, name: str) -> Group:
        """Generate a new group identity and record membership."""
        group_ident = RNS.Identity()
        keyfile = os.path.join(self.registry.keys_dir, f"group-{name}-{fingerprint(group_ident).hex()[:8]}.key")
        group_ident.to_file(keyfile)
        group = Group(identity=group_ident, name=name,
                      founder=fingerprint(founder_identity).hex(), keyfile=keyfile)
        self.registry.add(group)
        self.registry.add_member_record(group.dest_hash, fingerprint(founder_identity).hex())
        return group

    def invite_bytes(self, group: Group, member_identity: RNS.Identity) -> bytes:
        """Encrypt the group private key to a member for delivery via DM."""
        prv = group.identity.get_private_key()
        return member_identity.encrypt(prv)

    def accept_invite(self, member_identity: RNS.Identity, name: str,
                      founder_fp: str, encrypted_prv: bytes) -> Group:
        """Decrypt an invite and join the group."""
        prv = member_identity.decrypt(encrypted_prv)
        if prv is None:
            raise MessageError("could not decrypt group invite")
        group_ident = RNS.Identity.from_bytes(prv)
        keyfile = os.path.join(self.registry.keys_dir,
                               f"group-{name}-{fingerprint(group_ident).hex()[:8]}.key")
        group_ident.to_file(keyfile)
        group = Group(identity=group_ident, name=name, founder=founder_fp, keyfile=keyfile)
        self.registry.add(group)
        self.registry.add_member_record(group.dest_hash, fingerprint(member_identity).hex())
        return group

    def build_group_envelope(self, sender_identity: RNS.Identity,
                             group_identity: RNS.Identity, text: str,
                             attachments: Optional[List[bytes]] = None,
                             kind: int = int(MessageKind.GROUP)) -> Envelope:
        """Build a group message envelope (body encrypted to the group identity)."""
        from rnet.protocol import Body, Bandwidth  # local import to avoid cycle
        body = Body(text=text, files=[{"hash": h, "name": "", "size": 0} for h in (attachments or [])],
                    bw=int(Bandwidth.LOW))
        ct = group_identity.encrypt(body.to_bytes())
        return Envelope(
            sender=fingerprint(sender_identity).hex(),
            recipient=fingerprint(group_identity).hex(),
            kind=kind,
            id=Envelope.new_id(),
            ts=int(time.time()),
            ciphertext=ct,
            nonce=os.urandom(16),
        )

    def open_envelope(self, env: Envelope) -> Optional[bytes]:
        """Decrypt a group envelope's body using the held group key."""
        group_ident = self.registry.get(env.recipient)
        if group_ident is None:
            return None
        return group_ident.decrypt(env.ciphertext)