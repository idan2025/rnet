"""Web service + client: RNS wiring and content retrieval.

:class:`WebService` mounts an :class:`RHTTPServer` on the host's
``rnet.http`` destination so peers can fetch content. :class:`WebClient`
issues requests via a :class:`WebTransport`, verifies the signed response
against the resolved host identity, and reassembles CAS-referenced bodies by
pulling chunks through a :class:`Replicator`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

import RNS

from rnet.core.events import LoopBridge
from rnet.db.connection import Database
from rnet.identity import IdentityManager
from rnet.storage.cas import (
    ContentStore,
    ManifestStore,
    Manifest,
    assemble,
    verify_manifest,
)
from rnet.storage.replication import ChunkSource, Replicator
from rnet.web.protocol import RHTTPRequest, RHTTPResponse
from rnet.web.server import RHTTPServer
from rnet.web.transport import WEB_APP, WEB_ASPECT, WebTransport

log = logging.getLogger(__name__)


class WebService:
    """Owns the ``rnet.http`` destination + request handler for a host."""

    def __init__(self, server: RHTTPServer, identity: RNS.Identity,
                 bridge: LoopBridge, app_name: str = WEB_APP, aspect: str = WEB_ASPECT):
        self.server = server
        self.identity = identity
        self.bridge = bridge
        self.app_name = app_name
        self.aspect = aspect
        self.destination: Optional[RNS.Destination] = None

    def start(self) -> str:
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            self.app_name,
            self.aspect,
        )
        self.destination.register_request_handler(
            "http",
            response_generator=self.server.request_handler(),
            allow=RNS.Destination.ALLOW_ALL,
        )
        return self.destination.hash.hex()

    def stop(self) -> None:
        if self.destination is not None:
            try:
                self.destination.deregister_request_handler("http")
            except Exception:  # pragma: no cover
                pass
            self.destination = None


class WebClient:
    """Fetches + verifies RHTTP responses, reassembling CAS bodies."""

    def __init__(self, transport: WebTransport, content_store: ContentStore,
                 manifest_store: ManifestStore, idm: IdentityManager):
        self.transport = transport
        self.store = content_store
        self.manifests = manifest_store
        self.idm = idm
        self.replicator = Replicator(content_store)

    async def get(self, host_dest_hash: str, path: str,
                  host_pubkey: Optional[bytes] = None,
                  sources: Optional[List[ChunkSource]] = None,
                  range_: Optional[list] = None) -> Optional[RHTTPResponse]:
        req = RHTTPRequest(method="GET", path=path, range=range_)
        resp = await self.transport.request(host_dest_hash, req)
        if resp is None:
            return None
        # Verify signature against the host identity, if known.
        if host_pubkey is not None:
            if not resp.verify_pubkey(host_pubkey):
                log.warning("RHTTP response signature failed for %s", host_dest_hash)
                return None
        # Inline body: verify content hash.
        if resp.body:
            from rnet.storage.cas import hash_data
            if hash_data(resp.body) != resp.content_hash:
                log.warning("inline body hash mismatch for %s", host_dest_hash)
                return None
            return resp
        # CAS-referenced body: parse embedded manifest (or look up cached),
        # fetch missing chunks, reassemble.
        manifest = None
        if resp.manifest:
            from rnet.storage.cas import Manifest
            try:
                manifest = Manifest.from_bytes(resp.manifest)
            except Exception:
                manifest = None
        if manifest is None:
            manifest = self.manifests.get(resp.content_hash)
        if manifest is None:
            log.warning("unknown manifest %s for response", resp.content_hash.hex()[:12])
            return resp  # caller may fetch manifest separately
        # Cache the manifest locally.
        self.manifests.put(manifest)
        if not verify_manifest(manifest, self.store) and sources:
            try:
                await self.replicator.fetch_manifest(manifest, sources)
            except Exception as exc:
                log.warning("CAS fetch failed: %s", exc)
                return resp
        try:
            resp.body = assemble(manifest, self.store)
        except Exception as exc:
            log.warning("CAS assemble failed: %s", exc)
        return resp