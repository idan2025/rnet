"""App base class + RNS service mounting."""
from __future__ import annotations

import logging
from typing import Optional

import RNS

from rnet.apps.manifest import AppManifest
from rnet.core.events import LoopBridge

log = logging.getLogger(__name__)


class App:
    """Base class for RNet applications.

    Subclasses set ``manifest`` and implement :meth:`handle_request` (sync,
    returns response bytes) and optionally :meth:`on_start` / :meth:`on_stop`.
    The SDK calls :meth:`handle_request` when a peer sends a request to the
    app's service destination.
    """

    manifest: AppManifest = None

    def __init__(self, manifest: Optional[AppManifest] = None):
        if manifest is not None:
            self.manifest = manifest
        if self.manifest is None:
            raise ValueError("App.manifest must be set")
        self.sdk = None       # set by SDK on register_service
        self.service = None   # AppService, set by SDK

    # -- lifecycle hooks --------------------------------------------------
    def on_start(self) -> None:
        """Called after the service is mounted; override to initialize."""

    def on_stop(self) -> None:
        """Called before the service is unmounted; override to clean up."""

    # -- request handling -------------------------------------------------
    def handle_request(self, path: str, data: bytes,
                       remote_identity=None) -> bytes:
        """Override to answer peer requests. Default: 404-style empty."""
        return b""


class AppService:
    """Mounts an :class:`App` on an RNS ``rnet.<cap>`` destination."""

    def __init__(self, app: App, identity: RNS.Identity, bridge: LoopBridge):
        self.app = app
        self.identity = identity
        self.bridge = bridge
        self.destination: Optional[RNS.Destination] = None

    def start(self) -> str:
        cap = self.app.manifest.cap
        if not cap:
            raise ValueError("app manifest has no capability token")
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            "rnet",
            cap,
        )
        app = self.app

        def generator(path, data, request_id, remote_identity, requested_at):
            try:
                return app.handle_request(path, bytes(data), remote_identity)
            except Exception:  # pragma: no cover
                log.exception("app %s handler crashed", app.manifest.name)
                return b""

        self.destination.register_request_handler(
            cap, response_generator=generator, allow=RNS.Destination.ALLOW_ALL
        )
        return self.destination.hash.hex()

    def stop(self) -> None:
        if self.destination is not None:
            try:
                self.destination.deregister_request_handler(self.app.manifest.cap)
            except Exception:  # pragma: no cover
                pass
            self.destination = None