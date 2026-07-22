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
            "SELECT first_seen, display, trusted, blocked, notes "
            "FROM identities WHERE dest_hash=?",
            (dest_hash,),
        )
        first_seen = row["first_seen"] if row else now
        # Preserve user-set display/trusted/blocked/notes across announces:
        # a fresh announce only refreshes pubkey/name/profile/last_seen.
        if row:
            if display:
                # Caller-supplied display wins only if the stored one is empty.
                if not row["display"]:
                    pass  # keep new display
                else:
                    display = row["display"]
            else:
                display = row["display"]
            trusted = row["trusted"]
            blocked = row["blocked"]
            notes = row["notes"]
        else:
            trusted = 0
            blocked = 0
            notes = None
        self.db.execute(
            """INSERT OR REPLACE INTO identities
               (dest_hash, fingerprint, pubkey, name, display,
                profile, profile_sig, verified, first_seen, last_seen,
                trusted, blocked, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                trusted,
                blocked,
                notes,
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

    def list_known(self, include_blocked: bool = False):
        if include_blocked:
            return self.db.query(
                "SELECT * FROM identities ORDER BY last_seen DESC"
            )
        return self.db.query(
            "SELECT * FROM identities WHERE blocked=0 ORDER BY last_seen DESC"
        )

    # -- known-identity address-book mutations -----------------------------
    def set_display(self, dest_hash: str, display: str) -> None:
        self.db.execute(
            "UPDATE identities SET display=? WHERE dest_hash=?",
            (display, dest_hash),
        )

    def set_trusted(self, dest_hash: str, trusted: bool) -> None:
        self.db.execute(
            "UPDATE identities SET trusted=? WHERE dest_hash=?",
            (1 if trusted else 0, dest_hash),
        )

    def set_blocked(self, dest_hash: str, blocked: bool) -> None:
        self.db.execute(
            "UPDATE identities SET blocked=? WHERE dest_hash=?",
            (1 if blocked else 0, dest_hash),
        )

    def set_notes(self, dest_hash: str, notes: str) -> None:
        self.db.execute(
            "UPDATE identities SET notes=? WHERE dest_hash=?",
            (notes, dest_hash),
        )

    def delete_known(self, dest_hash: str) -> None:
        self.db.execute(
            "DELETE FROM identities WHERE dest_hash=?", (dest_hash,)
        )

    # -- owned-identity mutations -----------------------------------------
    def set_default_own(self, dest_hash: str) -> None:
        self.db.execute("UPDATE own_identities SET is_default=0")
        self.db.execute(
            "UPDATE own_identities SET is_default=1 WHERE dest_hash=?",
            (dest_hash,),
        )

    def rename_own(self, name: str, new_name: str) -> None:
        self.db.execute(
            "UPDATE own_identities SET name=? WHERE name=?", (new_name, name)
        )

    def delete_own(self, name: str) -> None:
        row = self.db.query_one(
            "SELECT keyfile FROM own_identities WHERE name=?", (name,)
        )
        if not row:
            return
        self.db.execute("DELETE FROM own_identities WHERE name=?", (name,))
        # Best-effort: remove the keyfile so the identity is gone for real.
        try:
            import os

            if row["keyfile"] and os.path.exists(row["keyfile"]):
                os.remove(row["keyfile"])
        except Exception:  # pragma: no cover - best effort
            pass

    def get_default_own(self):
        return self.db.query_one(
            "SELECT * FROM own_identities WHERE is_default=1 LIMIT 1"
        )