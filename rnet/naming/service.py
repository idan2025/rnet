"""Name resolution: cache-first, then query naming-capable peers.

``resolve_name(name)`` returns a verified :class:`NameRecord` or None. It
checks the local cache (honor TTL), and on miss/staleness queries one or more
:class:`NameSource` peers (naming-capable nodes). A retrieved record is
signature-verified against the owner identity (looked up in the known
identity cache, or recalled from RNS) before caching.

Decentralized by construction: no central registrar, ownership is signed,
records are replicated by any node that opts into the ``naming`` capability.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

import RNS

from rnet.errors import SignatureError
from rnet.identity import IdentityManager
from rnet.naming.records import NameRecord
from rnet.naming.registry import NameRegistry

log = logging.getLogger(__name__)


class NameSource:
    """Resolves a name to a signed record blob from a remote peer."""

    async def resolve(self, name: str) -> Optional[bytes]:
        raise NotImplementedError


class FakeNameSource(NameSource):
    """In-process source backed by a dict of name -> record bytes."""

    def __init__(self, records: dict):
        self._records = records

    async def resolve(self, name: str) -> Optional[bytes]:
        await asyncio.sleep(0)
        return self._records.get(name)


class RNSNameSource(NameSource):
    """Queries a naming peer's ``rnet.name`` request handler over RNS."""

    REQUEST_PATH = "name"

    def __init__(self, peer_dest_hash: str, timeout: float = 20.0):
        self.peer_dest_hash = peer_dest_hash
        self.timeout = timeout

    def _resolve(self) -> Optional[RNS.Destination]:
        ident = RNS.Identity.recall(bytes.fromhex(self.peer_dest_hash))
        if ident is None:
            return None
        return RNS.Destination(
            ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnet", "name"
        )

    async def resolve(self, name: str) -> Optional[bytes]:
        loop = asyncio.get_running_loop()

        def _do() -> Optional[bytes]:
            dest = self._resolve()
            if dest is None or not RNS.Transport.has_path(dest.hash):
                if dest is not None:
                    RNS.Transport.request_path(dest.hash)
                return None
            link = RNS.Link(dest)
            resp = link.request(self.REQUEST_PATH, data=name.encode("utf-8"),
                                timeout=self.timeout)
            return bytes(resp) if resp is not None else None

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            return None


class NamingService:
    """Build, sign, publish, and resolve ``.rns`` names."""

    def __init__(self, registry: NameRegistry, idm: IdentityManager):
        self.registry = registry
        self.idm = idm

    @staticmethod
    def bare_name(name: str) -> str:
        n = name.strip().lower()
        if n.endswith(".rns"):
            n = n[:-4]
        return n

    # -- publishing -------------------------------------------------------
    def publish(
        self,
        owner_identity: RNS.Identity,
        name: str,
        node_dest_hash: str,
        services: List[dict],
        seq: int = 1,
        ttl: int = 86400,
        prev: bytes = b"",
    ) -> NameRecord:
        """Create + sign a name record and cache it locally."""
        bare = self.bare_name(name)
        record = NameRecord(
            name=bare,
            owner="",  # filled below from identity fingerprint
            fp=b"",
            node=node_dest_hash,
            services=services,
            seq=seq,
            ts=int(self.registry.db.now()),
            ttl=ttl,
            prev=prev,
        )
        # owner dest hash: we use the fingerprint hex as the stable owner id
        # (a user identity may not have a destination yet). The fingerprint
        # is what signatures bind to.
        record.owner = RNS.Identity.full_hash(owner_identity.get_public_key())[:8].hex()
        record.sign(owner_identity)
        self.registry.put(record)
        return record

    def transfer(
        self,
        new_owner_identity: RNS.Identity,
        old_record: NameRecord,
        node_dest_hash: str,
        services: List[dict],
        seq: Optional[int] = None,
        ttl: int = 86400,
    ) -> NameRecord:
        """Sign a transfer to a new owner, chaining to ``old_record`` via prev."""
        record = NameRecord(
            name=old_record.name,
            node=node_dest_hash,
            services=services,
            seq=seq if seq is not None else old_record.seq + 1,
            ts=int(self.registry.db.now()),
            ttl=ttl,
            prev=old_record.fp,
        )
        record.owner = RNS.Identity.full_hash(new_owner_identity.get_public_key())[:8].hex()
        record.sign(new_owner_identity)
        self.registry.put(record)
        return record

    # -- resolution -------------------------------------------------------
    async def resolve_name(
        self, name: str, sources: Optional[List[NameSource]] = None,
        allow_stale: bool = True,
    ) -> Optional[NameRecord]:
        """Resolve a name to a verified record.

        Cache-first. On miss or staleness, queries ``sources``. If all sources
        fail and ``allow_stale`` is True, returns the stale cached record.
        """
        bare = self.bare_name(name)
        cached = self.registry.get(bare)
        if cached is not None and not self.registry.is_stale(bare):
            return cached

        if sources:
            for src in sources:
                raw = await src.resolve(bare)
                if raw is None:
                    continue
                try:
                    record = NameRecord.from_bytes(raw)
                except Exception:
                    log.warning("bad name record from source")
                    continue
                if record.name != bare:
                    continue
                if self._verify_record(record):
                    self.registry.put(record)
                    return record
                else:
                    log.warning("name record for %s failed verification", bare)

        if cached is not None and allow_stale:
            return cached
        return None

    def _verify_record(self, record: NameRecord) -> bool:
        """Verify a record's signature against the owner identity pubkey."""
        # Owner id is the fingerprint hex; look up the known identity by fp.
        row = self.idm.store.get_known_by_fp(record.fp)
        if not row or not row["pubkey"]:
            # Try owner dest hash as a fallback key.
            row = self.idm.store.get_known(record.owner)
        if not row or not row["pubkey"]:
            log.warning("no pubkey to verify name record for %s", record.name)
            return False
        try:
            record.verify_pubkey(bytes(row["pubkey"]))
        except SignatureError:
            return False
        # If we have a prior record, enforce seq monotonicity + transfer rules.
        prior = self.registry.get(record.name)
        if prior is not None and record.seq <= prior.seq and record.owner != prior.owner:
            return False
        return True

    # -- helpers ----------------------------------------------------------
    def find_owner_identity(self, record: NameRecord) -> Optional[RNS.Identity]:
        row = self.idm.store.get_known_by_fp(record.fp) or self.idm.store.get_known(record.owner)
        if not row or not row["pubkey"]:
            return None
        ident = RNS.Identity(create_keys=False)
        ident.load_public_key(bytes(row["pubkey"]))
        return ident