"""The RNet core node: lifecycle over RNS, announce loop, capability ads.

A :class:`Node` owns:

- an :class:`RNS.Reticulum` instance (the RNS daemon-equivalent in-process),
- a node :class:`RNS.Identity` (persistent, from a keyfile),
- a node :class:`RNS.Destination` on the ``rnet.node`` aspect for presence,
- an :class:`ServiceDiscovery` that parses peer announces and maintains the
  peer registry,
- an asyncio announce loop honoring low-power timing.

RNS callbacks run on RNS threads; all cross-thread handoffs go through the
:class:`EventBus` (threadsafe emit) and :class:`LoopBridge`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Optional

import RNS

from rnet.config import NodeConfig
from rnet.core.events import (
    EventBus,
    LoopBridge,
    NODE_STARTED,
    NODE_STOPPED,
)
from rnet.db.connection import Database
from rnet.discovery import AnnounceHandler, NODE_ASPECT, PeerRegistry, ServiceDiscovery
from rnet.errors import RNetError
from rnet.identity import IdentityManager, IdentityStore, SignedProfile, fingerprint
from rnet.protocol.capabilities import Bandwidth, CapabilitySet

log = logging.getLogger(__name__)

# RNS requires transport to forward announces between interfaces. RNet nodes
# that only want to *participate* (not relay) can still announce; relays set
# transport in their RNS config. We do not force transport here.


class Node:
    """A running RNet node. Construct, then ``await node.start()``."""

    def __init__(
        self,
        config: NodeConfig,
        identity: RNS.Identity,
        db: Database,
        bus: Optional[EventBus] = None,
        identity_manager: Optional[IdentityManager] = None,
        reticulum_factory: Optional[Callable[[Optional[str]], RNS.Reticulum]] = None,
    ):
        self.config = config
        self.identity = identity
        self.db = db
        self.bus = bus or EventBus()
        self.idm = identity_manager or IdentityManager(
            IdentityStore(db), config.paths()["keys"]
        )
        self.registry = PeerRegistry(db)
        self.discovery = ServiceDiscovery(self.bus, self.registry, self.idm)
        self._reticulum_factory = reticulum_factory or (lambda cfg: RNS.Reticulum(cfg))
        self.reticulum: Optional[RNS.Reticulum] = None
        self.destination: Optional[RNS.Destination] = None
        self.node_dest_hash: Optional[str] = None
        self._announce_task: Optional[asyncio.Task] = None
        self._announce_handler: Optional[AnnounceHandler] = None
        self._running = False
        self._seq = 0
        self._signed_profile: Optional[SignedProfile] = None
        self.capability_set = CapabilitySet(config.capabilities)
        self.messaging = None  # MessagingService, built in start() if capable
        self.web = None        # WebService, built in start() if capable
        self.naming = None     # NamingService, built in start()
        self.sdk = None        # RNet SDK facade, built in start()
        self._started_at: Optional[float] = None
        # Loop binding deferred until start() (the loop may not exist yet).

    # -- lifecycle --------------------------------------------------------
    async def start(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        loop = loop or asyncio.get_running_loop()
        self.bus.bind(loop)
        self.bridge = LoopBridge(loop, self.bus)

        self.config.validate()
        os.makedirs(self.config.datadir, exist_ok=True)

        # Initialize RNS. Pass None to let RNS use its default config dir.
        # RNS.Reticulum is a process singleton; reuse an existing instance if
        # one is already running (e.g. another node in the same process).
        existing = RNS.Reticulum.get_instance()
        if existing is not None:
            self.reticulum = existing
        else:
            self.reticulum = self._reticulum_factory(self.config.rns_configdir)

        # Create the node destination. All RNet nodes share the rnet.node
        # aspect (app "rnet", aspect "node"); different identities yield
        # different destination hashes. RNS app names cannot contain dots.
        app_name, _, aspect = NODE_ASPECT.partition(".")
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            app_name,
            aspect,
        )
        self.node_dest_hash = self.destination.hash.hex()

        # Build + persist our signed profile (binds node identity to caps).
        self._signed_profile = self.idm.make_profile(
            self.identity,
            name=self.config.name,
            capabilities=self.config.capabilities,
            node_dest_hash=self.node_dest_hash,
        )
        self.idm.store.upsert_known(
            dest_hash=self.node_dest_hash,
            fingerprint_bytes=fingerprint(self.identity),
            pubkey=self.identity.get_public_key(),
            name=self.config.name,
            verified=True,
            profile_bytes=self._signed_profile.profile_bytes,
            profile_sig=self._signed_profile.sig,
        )

        # Register the announce handler (RNS dispatches on its threads).
        self._announce_handler = AnnounceHandler(self.bus, self.discovery.handle_announce)
        RNS.Transport.register_announce_handler(self._announce_handler)

        self._running = True
        self._started_at = time.time()
        self.bus.emit(NODE_STARTED, {"name": self.config.name,
                                     "dest": self.node_dest_hash})

        # If this node offers messaging, wire the messaging service.
        if "messaging" in self.config.capabilities:
            from rnet.messaging import MessagingService
            self.messaging = MessagingService(self.config, self.identity, self.db,
                                              self.idm, self.bridge)
            self.messaging.start()
            await self.messaging.start_loops()

        # If this node offers web hosting, mount the RHTTP server.
        if "web" in self.config.capabilities:
            if not self.config.web_root:
                raise RNetError("web capability requires --web-root / web_root")
            from rnet.web import RHTTPServer, WebService
            from rnet.storage import ContentStore, ManifestStore
            store = ContentStore(self.db, self.config.paths()["cas"])
            manifests = ManifestStore(self.db)
            server = RHTTPServer(self.config.web_root, self.identity, store,
                                 manifests, inline_max=self.config.web_inline_max)
            self.web = WebService(server, self.identity, self.bridge)
            self.web.start()

        # Announce once immediately, then on a schedule.
        self.announce_now()
        self._announce_task = asyncio.create_task(self._announce_loop())

        # Build the SDK facade (apps + node code use this).
        from rnet.apps import RNet
        from rnet.naming import NamingService, NameRegistry
        from rnet.storage import ContentStore, ManifestStore
        self.naming = NamingService(NameRegistry(self.db), self.idm)
        self.sdk = RNet(
            self.config, self.identity, self.db, self.idm, self.bus, self.bridge,
            messenger=getattr(self.messaging, "messenger", None) if self.messaging else None,
            naming=self.naming,
            content_store=ContentStore(self.db, self.config.paths()["cas"]),
            manifest_store=ManifestStore(self.db),
            registry=self.registry,
        )

    async def stop(self) -> None:
        self._running = False
        if self.sdk is not None:
            self.sdk.stop_apps()
            self.sdk = None
        if self.web is not None:
            self.web.stop()
            self.web = None
        if self.messaging is not None:
            await self.messaging.stop()
            self.messaging = None
        if self._announce_task:
            self._announce_task.cancel()
            try:
                await self._announce_task
            except asyncio.CancelledError:
                pass
            self._announce_task = None
        if self._announce_handler is not None:
            try:
                RNS.Transport.deregister_announce_handler(self._announce_handler)
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
        self.bus.emit(NODE_STOPPED, {"name": self.config.name})
        # RNS.Reticulum has no public halt; it stops with the process.

    # -- announcing -------------------------------------------------------
    def _build_capadv(self) -> bytes:
        max_bw = min(
            int(self.config.max_bandwidth),
            int(self.capability_set.max_bandwidth()) if self.capability_set.tokens else int(self.config.max_bandwidth),
        )
        adv = self.discovery.build_capadv(
            name=self.config.name,
            caps=self.config.capabilities,
            profile_sig=self._signed_profile.sig,
            fp=fingerprint(self.identity),
            ts=int(time.time()),
            max_bw=max_bw,
            low_power=self.config.low_power,
        )
        return adv.to_bytes()

    def announce_now(self) -> None:
        """Announce presence + capabilities immediately."""
        if self.destination is None:
            raise RNetError("node not started")
        app_data = self._build_capadv()
        try:
            self.destination.announce(app_data=app_data)
            log.info("announced %s (%s)", self.config.name, self.node_dest_hash)
        except Exception as exc:  # pragma: no cover - depends on RNS interfaces
            log.warning("announce failed: %s", exc)

    async def _announce_loop(self) -> None:
        import random

        interval = self.config.effective_announce_interval()
        while self._running:
            jitter = interval * self.config.announce_jitter
            sleep = interval + random.uniform(-jitter, jitter)
            try:
                await asyncio.sleep(max(5.0, sleep))
            except asyncio.CancelledError:
                return
            if self._running:
                self.announce_now()
                self.registry.prune_stale()

    # -- introspection ----------------------------------------------------
    def peers(self):
        return self.registry.list_all()

    def peers_with(self, cap: str):
        return self.registry.list_by_capability(cap)

    @property
    def running(self) -> bool:
        return self._running

    def uptime(self) -> float:
        """Seconds since start, or 0 if not started."""
        if self._started_at is None:
            return 0.0
        return time.time() - self._started_at

    def interfaces(self) -> list:
        """Snapshot of RNS interfaces as plain dicts for the GUI.

        Reads ``self.reticulum.interfaces`` (a name -> RNS interface object
        dict). Each RNS interface exposes different attributes depending on
        type, so this is best-effort and never raises.
        """
        out = []
        if self.reticulum is None:
            return out
        try:
            ifaces = self.reticulum.interfaces or {}
        except Exception:  # pragma: no cover - defensive
            return out
        for name, ifc in ifaces.items():
            entry = {
                "name": str(name),
                "type": getattr(ifc, "type", type(ifc).__name__),
                "enabled": bool(getattr(ifc, "enabled", True)),
                "online": bool(getattr(ifc, "online", False)),
                "mode": getattr(ifc, "mode", None),
                "rx_bytes": getattr(ifc, "rx_bytes", None),
                "tx_bytes": getattr(ifc, "tx_bytes", None),
                "bitrate": getattr(ifc, "bitrate", None),
                "rssi": getattr(ifc, "rssi", None),
                "snr": getattr(ifc, "snr", None),
            }
            # Surface common config knobs for display.
            for attr in ("target_host", "target_port", "device", "port",
                         "host", "listen_port", "bitrate"):
                val = getattr(ifc, attr, None)
                if val is not None:
                    entry[attr] = val
            out.append(entry)
        return out

    async def restart(self) -> None:
        """Stop and start again with the same config + identity."""
        await self.stop()
        # Brief yield so downstream observers see NODE_STOPPED before restart.
        await asyncio.sleep(0.2)
        await self.start()