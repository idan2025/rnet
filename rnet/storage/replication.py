"""Replication: fetch chunks from peers so content survives node loss.

A :class:`ChunkSource` fetches a block by hash from a remote peer. The real
implementation (:class:`RNSChunkSource`) issues an RNS request to a peer's
storage destination; :class:`FakeChunkSource` wires stores together in tests.

A :class:`Replicator` walks a manifest, asks a set of sources for any chunk
the local store lacks, verifies each against its hash, and pins the result.
Because chunks are content-addressed, a node can pull different chunks from
different peers and the assembled file still verifies.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, List, Optional

import RNS

from rnet.errors import StorageError
from rnet.storage.cas import ContentStore, Manifest, hash_data

log = logging.getLogger(__name__)


class ChunkSource:
    async def fetch(self, chunk_hash: bytes) -> Optional[bytes]:
        raise NotImplementedError


class FakeChunkSource(ChunkSource):
    """Serves chunks from an in-process :class:`ContentStore`."""

    def __init__(self, store: ContentStore):
        self.store = store

    async def fetch(self, chunk_hash: bytes) -> Optional[bytes]:
        await asyncio.sleep(0)
        return self.store.get_block(chunk_hash)


class RNSChunkSource(ChunkSource):
    """Fetches a chunk from a specific storage peer over RNS.

    The peer runs an ``rnet.cas`` request handler returning the raw block for
    a requested hash (or None). Verified locally on receipt.
    """

    REQUEST_PATH = "cas"

    def __init__(self, peer_dest_hash: str, timeout: float = 30.0):
        self.peer_dest_hash = peer_dest_hash
        self.timeout = timeout

    def _resolve(self) -> Optional[RNS.Destination]:
        ident = RNS.Identity.recall(bytes.fromhex(self.peer_dest_hash))
        if ident is None:
            return None
        return RNS.Destination(
            ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnet", "cas"
        )

    async def fetch(self, chunk_hash: bytes) -> Optional[bytes]:
        loop = asyncio.get_running_loop()

        def _do() -> Optional[bytes]:
            dest = self._resolve()
            if dest is None:
                return None
            if not RNS.Transport.has_path(dest.hash):
                RNS.Transport.request_path(dest.hash)
                return None
            link = RNS.Link(dest)
            resp = link.request(self.REQUEST_PATH, data=chunk_hash,
                                timeout=self.timeout)
            return bytes(resp) if resp is not None else None

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            return None


class Replicator:
    """Fetches + verifies a manifest's chunks into a local store."""

    def __init__(self, store: ContentStore):
        self.store = store

    async def fetch_manifest(
        self, manifest: Manifest, sources: Iterable[ChunkSource],
        pin: bool = True, max_concurrent: int = 4,
    ) -> int:
        """Pull any missing chunks from ``sources``. Returns count fetched."""
        missing = [c for c in manifest.chunks if not self.store.has_block(c.hash)]
        if not missing:
            return 0
        sources = list(sources)
        if not sources:
            raise StorageError("no chunk sources available for replication")
        sem = asyncio.Semaphore(max_concurrent)
        fetched = 0

        async def fetch_one(chunk_hash: bytes) -> bool:
            async with sem:
                for src in sources:
                    try:
                        block = await src.fetch(chunk_hash)
                    except Exception:  # pragma: no cover - peer errors
                        block = None
                    if block is None:
                        continue
                    if hash_data(block) != chunk_hash:
                        log.warning("chunk %s failed verification, trying next source",
                                    chunk_hash.hex()[:12])
                        continue
                    self.store.put_block(block)
                    if pin:
                        self.store.pin(chunk_hash)
                    return True
                return False

        results = await asyncio.gather(*[fetch_one(c.hash) for c in missing])
        fetched = sum(1 for r in results if r)
        if fetched != len(missing):
            missing_after = [c.hash.hex() for c in missing
                             if not self.store.has_block(c.hash)]
            raise StorageError(
                f"could not fetch {len(missing) - fetched} chunks: {missing_after[:4]}"
            )
        return fetched