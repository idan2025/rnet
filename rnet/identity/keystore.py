"""SQLite-backed identity keystore.

Stores *references* to owned identities (path to RNS keyfile + metadata) and
a cache of known remote identities (pubkey + signed profile). Never stores
private key bytes.
"""
from __future__ import annotations

import time
from typing import List, Optional

import RNS

from rnet.db.connection import Database
from rnet.identity.util import fingerprint


class IdentityStore:
    def __init__(self, db: Database):
        self.db = db

    # -- owned identities -------------------------------------------------
    def register_own(
        self,
        identity: RNS.Identity,
        name: str,
        keyfile: str,
        is_node: bool = False,
    ) -> str:
        fp = fingerprint(identity)
        # A node identity has no destination yet; use fingerprint as the
        # stable local key. dest_hash column stores the pubkey fingerprint
        # hex until a destination is created; destinations update it.
        dest_hash = fp.hex()
        self.db.execute(
            """INSERT OR REPLACE INTO own_identities
               (dest_hash, name, keyfile, is_node, created)
               VALUES (?,?,?,?,?)""",
            (dest_hash, name, keyfile, 1 if is_node else 0, int(time.time())),
        )
        return dest_hash

    def get_own(self, dest_hash_hex: str):
        return self.db.query_one(
            "SELECT * FROM own_identities WHERE dest_hash=?",
            (dest_hash_hex,),
        )

    def get_own_by_name(self, name: str):
        return self.db.query_one(
            "SELECT * FROM own_identities WHERE name=?", (name,)
        )

    def list_own(self):
        return self.db.query("SELECT * FROM own_identities ORDER BY created")

    # -- known remote identities (cache) ----------------------------------
    def upsert_known(
        self,
        dest_hash: str,
        fingerprint_bytes: bytes,
        pubkey: bytes,
        name: str = "",
        display: str = "",
        profile_bytes: bytes = b"",
        profile_sig: bytes = b"",
        verified: bool = False,
    ) -> None:
        now = int(time.time())
        row = self.db.query_one(
            "SELECT first_seen FROM identities WHERE dest_hash=?", (dest_hash,)
        )
        first_seen = row["first_seen"] if row else now
        self.db.execute(
            """INSERT OR REPLACE INTO identities
               (dest_hash, fingerprint, pubkey, name, display,
                profile, profile_sig, verified, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                dest_hash,
                fingerprint_bytes,
                pubkey,
                name,
                display,
                profile_bytes,
                profile_sig,
                1 if verified else 0,
                first_seen,
                now,
            ),
        )

    def get_known(self, dest_hash: str):
        return self.db.query_one(
            "SELECT * FROM identities WHERE dest_hash=?", (dest_hash,)
        )

    def get_known_by_fp(self, fp: bytes):
        return self.db.query_one(
            "SELECT * FROM identities WHERE fingerprint=?", (fp,)
        )

    def list_known(self):
        return self.db.query(
            "SELECT * FROM identities ORDER BY last_seen DESC"
        )