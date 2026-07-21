"""RNet SDK facade — the API app developers use.

Wraps the node's subsystems (messaging, naming, storage, web, discovery) into
a small surface:

    register_service(app)      -> mount an app's RNS service
    send_message(recipient, text, ...) -> encrypted DM
    store_content(data, name)  -> content-addressed, returns manifest hash
    fetch_content(hash)        -> assemble from local CAS (or replicate)
    resolve_name(name)         -> .rns resolution
    discover_peers(cap)        -> peers advertising a capability

Apps receive an :class:`RNet` instance on :meth:`App.start`.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

import RNS

from rnet.apps.base import App, AppService
from rnet.apps.manifest import AppManifest
from rnet.config import NodeConfig
from rnet.core.events import EventBus, LoopBridge
from rnet.db.connection import Database
from rnet.discovery import PeerRegistry
from rnet.identity import IdentityManager
from rnet.messaging import Messenger
from rnet.naming import NamingService, NameSource
from rnet.social import FollowStore, PostStore, SocialService
from rnet.storage import ContentStore, ManifestStore, build_manifest, assemble, verify_manifest
from rnet.storage.replication import ChunkSource, Replicator


class RNet:
    """SDK facade. Apps call methods on this object."""

    def __init__(self, config: NodeConfig, identity: RNS.Identity, db: Database,
                 idm: IdentityManager, bus: EventBus, bridge: LoopBridge,
                 messenger: Optional[Messenger] = None,
                 naming: Optional[NamingService] = None,
                 content_store: Optional[ContentStore] = None,
                 manifest_store: Optional[ManifestStore] = None,
                 registry: Optional[PeerRegistry] = None):
        self.config = config
        self.identity = identity
        self.db = db
        self.idm = idm
        self.bus = bus
        self.bridge = bridge
        self.messenger = messenger
        self.naming = naming
        self.content_store = content_store or ContentStore(db, config.paths()["cas"])
        self.manifest_store = manifest_store or ManifestStore(db)
        self.registry = registry or PeerRegistry(db)
        self.replicator = Replicator(self.content_store)
        self.social = SocialService(
            PostStore(db, self.content_store), FollowStore(db), idm
        )
        self._apps: List[AppService] = []

    # -- service registration --------------------------------------------
    def register_service(self, app: App) -> str:
        """Mount an app's RNS service destination. Returns its dest hash."""
        svc = AppService(app, self.identity, self.bridge)
        dest_hash = svc.start()
        app.service = svc
        app.sdk = self
        # Persist manifest.
        self.db.execute(
            """INSERT OR REPLACE INTO apps
               (app_id, name, version, cap, dest_hash, manifest, installed)
               VALUES (?,?,?,?,?,?,?)""",
            (app.manifest.app_id, app.manifest.name, app.manifest.version,
             app.manifest.cap, dest_hash, app.manifest.to_bytes(), int(self.db.now())),
        )
        self._apps.append(svc)
        app.on_start()
        return dest_hash

    def stop_apps(self) -> None:
        for svc in self._apps:
            svc.app.on_stop()
            svc.stop()
        self._apps.clear()

    def list_apps(self) -> List[dict]:
        rows = self.db.query("SELECT * FROM apps ORDER BY installed DESC")
        return [dict(r) for r in rows]

    # -- messaging --------------------------------------------------------
    async def send_message(self, recipient_dest_hash: str,
                           recipient_identity: RNS.Identity, text: str,
                           bw: int = 0) -> str:
        if self.messenger is None:
            raise RuntimeError("messaging not enabled on this node")
        return await self.messenger.send_dm(recipient_dest_hash, recipient_identity,
                                            text, bw=bw)

    # -- storage ----------------------------------------------------------
    def store_content(self, data: bytes, name: str = "",
                      ctype: str = "") -> bytes:
        """Content-address ``data``; returns the manifest hash (content id)."""
        m = build_manifest(data, self.content_store, name=name, ctype=ctype)
        return self.manifest_store.put(m)

    def fetch_content(self, manifest_hash: bytes,
                      sources: Optional[List[ChunkSource]] = None) -> bytes:
        """Assemble content from local CAS, replicating from sources if needed."""
        m = self.manifest_store.get(manifest_hash)
        if m is None:
            raise KeyError(f"unknown manifest {manifest_hash.hex()}")
        if not verify_manifest(m, self.content_store) and sources:
            asyncio.get_event_loop()  # ensure loop exists; replicator is async
            fut = asyncio.run_coroutine_threadsafe(
                self.replicator.fetch_manifest(m, sources), self.bridge.loop
            ) if self.bridge and self.bridge.loop else None
            if fut:
                fut.result(timeout=60)
        return assemble(m, self.content_store)

    # -- naming -----------------------------------------------------------
    async def resolve_name(self, name: str,
                           sources: Optional[List[NameSource]] = None):
        if self.naming is None:
            raise RuntimeError("naming not enabled on this node")
        return await self.naming.resolve_name(name, sources=sources)

    # -- discovery --------------------------------------------------------
    def discover_peers(self, cap: Optional[str] = None) -> List[dict]:
        if cap is None:
            return self.registry.list_all()
        return self.registry.list_by_capability(cap)