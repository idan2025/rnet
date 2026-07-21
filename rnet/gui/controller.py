"""GUI controller: owns the asyncio loop, Node lifecycle, and SDK handle.

One process, one shared asyncio loop (daemon thread). The controller creates
the loop up front, passes it to ``Node.start(loop=...)`` so ``node.bridge.loop``
is the same loop all async SDK calls use. RNet EventBus handlers run on the
asyncio thread and re-emit through a Qt signal bridge so the UI updates on the
Qt main thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

import RNS

from rnet.config import NodeConfig, default_datadir
from rnet.core import EventBus, Node
from rnet.core.events import (
    ANNOUNCE_RECEIVED,
    MESSAGE_RECEIVED,
    NODE_STARTED,
    NODE_STOPPED,
    PEER_DISCOVERED,
    RECEIPT_RECEIVED,
)
from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore
from rnet.protocol.capabilities import Bandwidth

log = logging.getLogger(__name__)


class GuiController:
    """Holds node state shared across all GUI tabs."""

    def __init__(self, datadir: Optional[str] = None, rns_configdir: Optional[str] = None,
                 bridge=None):
        self.datadir = datadir or default_datadir()
        self.rns_configdir = rns_configdir
        self.bridge = bridge  # Qt signal bridge (set by launch if GUI)
        # asyncio loop on a daemon thread (single shared loop)
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        self.bus = EventBus()
        self.bus.bind(self.loop)
        # DB + identity manager opened eagerly so Identities tab works pre-start.
        import os
        os.makedirs(self.datadir, exist_ok=True)
        self.db = Database(self._db_path())
        self.idm = IdentityManager(IdentityStore(self.db), self._keys_dir())
        self.node: Optional[Node] = None
        self._sdk = None
        self._bus_handlers = []
        self._wire_bus()  # bus -> Qt signals, live even before node start

    # -- paths ------------------------------------------------------------
    def _db_path(self) -> str:
        import os
        return os.path.join(self.datadir, "rnet.db")

    def _keys_dir(self) -> str:
        import os
        return os.path.join(self.datadir, "keys")

    # -- loop -------------------------------------------------------------
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_async(self, coro, on_done=None, on_error=None):
        from rnet.gui.workers import run_async
        return run_async(coro, self.loop, on_done=on_done, on_error=on_error)

    # -- bus -> Qt bridge -------------------------------------------------
    def _wire_bus(self) -> None:
        bridge = self.bridge
        if bridge is None:
            return

        def relay(signal_name):
            def _h(event):
                # event may be a dict; emit on the Qt signal (thread-safe via
                # queued connection since signal is emitted from asyncio thread).
                getattr(bridge, signal_name).emit(event)
            return _h

        mapping = {
            PEER_DISCOVERED: "peer_discovered",
            MESSAGE_RECEIVED: "message_received",
            RECEIPT_RECEIVED: "receipt_received",
            NODE_STARTED: "node_started",
            NODE_STOPPED: "node_stopped",
        }
        for ev_type, sig in mapping.items():
            h = relay(sig)
            self.bus.subscribe(ev_type, h)
            self._bus_handlers.append((ev_type, h))

    # -- node lifecycle ---------------------------------------------------
    def list_own_identities(self):
        return self.idm.list_own()

    def load_identity(self, name: str):
        return self.idm.load_by_name(name)

    def create_identity(self, name: str, is_node: bool = True):
        return self.idm.create(name, is_node=is_node)

    def start_node(self, name: str, capabilities, low_power: bool = False,
                   max_bandwidth: str = "medium", web_root: Optional[str] = None,
                   ratchets_path: Optional[str] = None,
                   on_done=None, on_error=None) -> None:
        """Build + start the node on the shared loop."""
        if self.node is not None and self.node.running:
            if on_error:
                on_error(RuntimeError("node already running"))
            return
        # Load or create the node identity by name.
        ident = self.idm.load_by_name(name)
        if ident is None:
            ident = self.idm.create(name, is_node=True)
        caps = [c.strip() for c in capabilities if c.strip()] if capabilities else ["messaging", "relay"]
        cfg = NodeConfig(
            name=name,
            capabilities=caps,
            rns_configdir=self.rns_configdir,
            datadir=self.datadir,
            low_power=low_power,
            max_bandwidth=int(Bandwidth.parse(max_bandwidth)),
            web_root=web_root,
            ratchets_path=ratchets_path,
        )
        self.node = Node(cfg, ident, self.db, bus=self.bus, identity_manager=self.idm)
        self._wire_bus()

        async def _start():
            await self.node.start(loop=self.loop)
            return self.node

        self.run_async(_start(), on_done=on_done, on_error=on_error)

    def stop_node(self, on_done=None, on_error=None) -> None:
        if self.node is None:
            return

        async def _stop():
            await self.node.stop()
            return True

        self.run_async(_stop(), on_done=on_done, on_error=on_error)

    @property
    def running(self) -> bool:
        return self.node is not None and self.node.running

    @property
    def sdk(self):
        return self.node.sdk if self.node is not None else None

    def get_sdk(self):
        return self.sdk

    def shutdown(self) -> None:
        """Best-effort cleanup on app exit."""
        if self.node is not None and self.node.running:
            try:
                fut = asyncio.run_coroutine_threadsafe(self.node.stop(), self.loop)
                fut.result(timeout=10)
            except Exception:  # pragma: no cover
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)