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
import os
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
from rnet.gui.settings_store import SettingsStore

log = logging.getLogger(__name__)


class GuiController:
    """Holds node state shared across all GUI tabs."""

    def __init__(self, datadir: Optional[str] = None, rns_configdir: Optional[str] = None,
                 bridge=None):
        self.datadir = datadir or default_datadir()
        os.makedirs(self.datadir, exist_ok=True)
        # Keep RNS config inside the RNet data dir by default so GUI-managed
        # interfaces persist alongside the rest of the user's data and survive
        # a fresh ~/.reticulum. Env override still wins.
        self.rns_configdir = rns_configdir or os.path.join(self.datadir, "reticulum")
        self.bridge = bridge  # Qt signal bridge (set by launch if GUI)
        # asyncio loop on a daemon thread (single shared loop)
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        self.bus = EventBus()
        self.bus.bind(self.loop)
        # DB + identity manager opened eagerly so Contacts tab works pre-start.
        self.db = Database(self._db_path())
        self.idm = IdentityManager(IdentityStore(self.db), self._keys_dir())
        self.node: Optional[Node] = None
        self._sdk = None
        self._bus_handlers = []
        # Persistent GUI settings + cached start config.
        self.settings = SettingsStore(os.path.join(self.datadir, "settings.json"))
        self.web_root: Optional[str] = None
        # Apply the transport (relay-hub) toggle to the RNS config before RNS
        # initialises — enable_transport is read once at Reticulum init, so it
        # must be set on disk first. With it on, this node forwards announces
        # between its interfaces and can mesh rnet clients without a rnsd.
        from rnet.gui.rns_config import default_config_path, set_enable_transport
        try:
            set_enable_transport(
                default_config_path(self.rns_configdir),
                bool(self.settings.get("enable_transport", False)),
            )
        except Exception as exc:  # pragma: no cover - don't crash GUI on config IO
            log.warning("set_enable_transport failed: %s", exc)
        # RNS uses POSIX signals internally which only work on the main
        # thread of the main interpreter. The node starts on the asyncio
        # daemon thread, so pre-build RNS.Reticulum here (main thread) —
        # Node.start() then reuses the instance via get_instance().
        self._init_rns_mainthread()
        self._wire_bus()  # bus -> Qt signals, live even before node start

    def _init_rns_mainthread(self) -> None:
        try:
            if RNS.Reticulum.get_instance() is None:
                RNS.Reticulum(self.rns_configdir)
        except Exception as exc:  # pragma: no cover - don't crash GUI on RNS init
            log.warning("RNS init failed (will retry on node start): %s", exc)

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
            ANNOUNCE_RECEIVED: "announce_sent",
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
        # Bus -> Qt bridge is wired once in __init__; don't re-subscribe here
        # or every event emits twice (double refreshes / double signals).

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

    def restart_node(self, on_done=None, on_error=None) -> None:
        """Stop and start again with the current config + identity."""
        if self.node is None:
            self.autostart(on_done=on_done, on_error=on_error)
            return

        async def _restart():
            await self.node.restart()
            return self.node

        self.run_async(_restart(), on_done=on_done, on_error=on_error)

    def autostart(self, on_done=None, on_error=None) -> None:
        """Start the node using persisted settings + default identity.

        Creates a ``default`` identity on first run. Safe to call from the GUI
        launch path; never raises into the caller.
        """
        s = self.settings
        name = s.get("default_identity")
        if not name:
            own = self.list_own_identities()
            if own:
                name = own[0]["name"]
                s.set("default_identity", name)
            else:
                name = "default"
                self.create_identity(name, is_node=True)
                s.set("default_identity", name)
        caps = s.get("capabilities") or ["messaging", "relay", "naming", "storage"]
        self.start_node(
            name=name,
            capabilities=caps,
            low_power=s.get("low_power", False),
            max_bandwidth=s.get("max_bandwidth", "medium"),
            web_root=self.web_root,
            on_done=on_done,
            on_error=on_error,
        )

    def announce_now(self) -> None:
        if self.node is not None and self.node.running:
            self.node.announce_now()

    # -- hosting ---------------------------------------------------------
    def start_hosting(self, web_root: str, on_done=None, on_error=None) -> None:
        """Enable the web capability with ``web_root`` and restart the node."""
        self.web_root = web_root
        caps = list(self.settings.get("capabilities") or ["messaging", "relay", "naming", "storage"])
        if "web" not in caps:
            caps.append("web")
        self.settings.set("capabilities", caps)
        self._restart_with_caps(caps, web_root=web_root, on_done=on_done, on_error=on_error)

    def stop_hosting(self, on_done=None, on_error=None) -> None:
        """Drop the web capability and restart the node."""
        self.web_root = None
        caps = [c for c in (self.settings.get("capabilities") or [])
                if c != "web"]
        self.settings.set("capabilities", caps)
        self._restart_with_caps(caps, web_root=None, on_done=on_done, on_error=on_error)

    def _restart_with_caps(self, caps, web_root, on_done=None, on_error=None) -> None:
        name = self.default_identity_name() or "default"

        def _start():
            self.start_node(
                name=name, capabilities=caps,
                low_power=self.settings.get("low_power", False),
                max_bandwidth=self.settings.get("max_bandwidth", "medium"),
                web_root=web_root,
                on_done=on_done, on_error=on_error,
            )

        if self.node is not None and self.node.running:
            self.stop_node(on_done=lambda _r: _start(), on_error=on_error)
        else:
            _start()

    # -- interfaces ------------------------------------------------------
    def list_interfaces(self) -> list:
        if self.node is not None and self.node.running:
            return self.node.interfaces()
        # Before node start, read the RNS config file directly.
        from rnet.gui.rns_config import read_interfaces, default_config_path
        try:
            return read_interfaces(default_config_path(self.rns_configdir))
        except Exception:
            return []

    def get_interface(self, name: str) -> Optional[dict]:
        """Return ``{name, type, options}`` for one interface from config."""
        from rnet.gui.rns_config import read_interfaces, default_config_path
        try:
            for ifc in read_interfaces(default_config_path(self.rns_configdir)):
                if ifc.get("name") == name:
                    return ifc
        except Exception:
            return None
        return None

    def add_interface(self, name: str, spec: dict, on_done=None, on_error=None) -> None:
        from rnet.gui.rns_config import write_interface, default_config_path
        path = default_config_path(self.rns_configdir)

        def _do():
            write_interface(path, name, spec)
            # Apply live to a running node; if the node isn't running the
            # block will be picked up from config on the next start.
            if self.node is not None and self.node.running:
                self.node.load_interface_live(name)
            return name

        def _after(_r):
            if self.bridge is not None:
                self.bridge.interface_changed.emit({"action": "add", "name": name})
            if on_done is not None:
                on_done(_r)

        from rnet.gui.workers import offload
        offload(_do, on_done=lambda _r: _after(_r), on_error=on_error)

    def update_interface(self, name: str, spec: dict, on_done=None, on_error=None) -> None:
        """Edit an existing interface block in config + apply live.

        ``write_interface`` is idempotent (it replaces any existing block of
        the same name), so this rewrites the config then reloads the interface
        on a running node: detach the old live instance and synthesize the new
        one from the updated config.
        """
        from rnet.gui.rns_config import write_interface, default_config_path
        path = default_config_path(self.rns_configdir)

        def _do():
            if self.node is not None and self.node.running:
                self.node.unload_interface_live(name)
            write_interface(path, name, spec)
            if self.node is not None and self.node.running:
                self.node.load_interface_live(name)
            return name

        def _after(_r):
            if self.bridge is not None:
                self.bridge.interface_changed.emit({"action": "update", "name": name})
            if on_done is not None:
                on_done(_r)

        from rnet.gui.workers import offload
        offload(_do, on_done=lambda _r: _after(_r), on_error=on_error)

    def remove_interface(self, name: str, on_done=None, on_error=None) -> None:
        from rnet.gui.rns_config import remove_interface as remove_block, default_config_path
        path = default_config_path(self.rns_configdir)

        def _do():
            if self.node is not None and self.node.running:
                self.node.unload_interface_live(name)
            return remove_block(path, name)

        def _after(_r):
            if self.bridge is not None:
                self.bridge.interface_changed.emit({"action": "remove", "name": name})
            if on_done is not None:
                on_done(_r)

        from rnet.gui.workers import offload
        offload(_do, on_done=lambda _r: _after(_r), on_error=on_error)

    # -- known identities (address book) ---------------------------------
    def list_known(self, include_blocked: bool = False):
        return self.idm.list_known(include_blocked=include_blocked)

    def set_display(self, dest_hash: str, display: str) -> None:
        self.idm.set_display(dest_hash, display)

    def set_trusted(self, dest_hash: str, trusted: bool) -> None:
        self.idm.set_trusted(dest_hash, trusted)

    def set_blocked(self, dest_hash: str, blocked: bool) -> None:
        self.idm.set_blocked(dest_hash, blocked)

    def set_notes(self, dest_hash: str, notes: str) -> None:
        self.idm.set_notes(dest_hash, notes)

    def delete_known(self, dest_hash: str) -> None:
        self.idm.delete_known(dest_hash)

    # -- owned identities ------------------------------------------------
    def delete_identity(self, name: str) -> None:
        self.idm.delete_own(name)

    def rename_identity(self, name: str, new_name: str) -> None:
        self.idm.rename_own(name, new_name)
        if self.settings.get("default_identity") == name:
            self.settings.set("default_identity", new_name)

    def set_default_identity(self, name: str) -> None:
        self.idm.set_default(name)
        self.settings.set("default_identity", name)

    def set_node_name(self, name: str) -> None:
        """Set the name this node announces under.

        Renames the current default identity to ``name`` (keeping its keys and
        destination hash, so contacts/address don't change) and persists it as
        the default. If an identity named ``name`` already exists, switches the
        default to it instead of clobbering. Applies on the next node start.
        """
        name = (name or "").strip()
        if not name:
            return
        current = self.default_identity_name()
        if name == current:
            return
        # If the desired name is already a different identity, just switch.
        if any(r["name"] == name for r in self.list_own_identities()):
            self.set_default_identity(name)
            return
        if current is None:
            # No identity yet: create one with the requested name.
            self.create_identity(name, is_node=True)
            self.set_default_identity(name)
            return
        self.idm.rename_own(current, name)
        self.set_default_identity(name)

    def default_identity_name(self) -> Optional[str]:
        name = self.settings.get("default_identity")
        if name:
            return name
        own = self.list_own_identities()
        if own:
            name = own[0]["name"]
            self.settings.set("default_identity", name)
            return name
        return None

    @property
    def running(self) -> bool:
        return self.node is not None and self.node.running

    @property
    def sdk(self):
        return self.node.sdk if self.node is not None else None

    def get_sdk(self):
        return self.sdk

    def shutdown(self) -> None:
        """Best-effort cleanup on app exit.

        Stops the node (bounded so a hung RNS teardown can't wedge the GUI),
        detaches RNS interfaces so sockets/threads close, then stops the
        asyncio loop and joins its thread so the process can actually exit.
        """
        if self.node is not None and self.node.running:
            try:
                fut = asyncio.run_coroutine_threadsafe(self.node.stop(), self.loop)
                fut.result(timeout=5)
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
        # Detach RNS interfaces (closes TCP/serial sockets, stops interface
        # threads). RNS threads are daemon, so this is about clean teardown,
        # not unblocking interpreter exit.
        try:
            RNS.Transport.detach_interfaces()
        except Exception:  # pragma: no cover - shutdown best-effort
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        try:
            self._loop_thread.join(timeout=3)
        except Exception:  # pragma: no cover
            pass