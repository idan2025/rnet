import os
import tempfile

import RNS

from rnet.apps import App, AppManifest, RNet
from rnet.config import NodeConfig
from rnet.core.events import EventBus, LoopBridge
from rnet.db.connection import Database
from rnet.discovery import PeerRegistry
from rnet.identity import IdentityManager, IdentityStore


class EchoApp(App):
    """Trivial app: echoes the request data as its response."""

    def __init__(self):
        super().__init__(AppManifest(name="echo", version="0.1.0", cap="echo",
                                     description="echo service",
                                     permissions=[]))

    def handle_request(self, path, data, remote_identity=None):
        return b"echo:" + data


def _ensure_rns():
    import RNS
    if not getattr(_ensure_rns, "_done", False):
        rns_dir = tempfile.mkdtemp(prefix="rns_sdk_")
        RNS.Reticulum(rns_dir)
        _ensure_rns._done = True


def _sdk(tmp, name="sdk"):
    _ensure_rns()
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"k_{name}"))
    ident = idm.create(name, is_node=True)
    cfg = NodeConfig(name=name, datadir=tmp)
    bus = EventBus()
    import asyncio
    loop = asyncio.new_event_loop()
    bus.bind(loop)
    bridge = LoopBridge(loop, bus)
    sdk = RNet(cfg, ident, db, idm, bus, bridge, registry=PeerRegistry(db))
    return db, idm, ident, sdk, loop


def test_app_manifest_roundtrip():
    m = AppManifest(name="forum", version="1.0.0", cap="forum",
                    description="mesh forum", permissions=["send_message", "store_content"],
                    author_fp=b"\x01" * 8)
    back = AppManifest.from_bytes(m.to_bytes())
    assert back.name == "forum" and back.cap == "forum"
    assert back.app_id == "forum@1.0.0"
    assert "send_message" in back.permissions


def test_store_and_fetch_content():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, sdk, loop = _sdk(tmp)
        data = b"app content payload " * 50
        h = sdk.store_content(data, name="file.bin", ctype="application/octet-stream")
        out = sdk.fetch_content(h)
        assert out == data


def test_register_service_mounts_app_and_persists():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, sdk, loop = _sdk(tmp)
        app = EchoApp()
        dest = sdk.register_service(app)
        assert len(dest) == 32
        assert app.sdk is sdk
        apps = sdk.list_apps()
        assert any(a["name"] == "echo" for a in apps)
        # app handles a request
        assert app.handle_request("echo", b"hi") == b"echo:hi"
        sdk.stop_apps()
        assert app.service.destination is None


def test_discover_peers_and_registry():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, sdk, loop = _sdk(tmp)
        from rnet.protocol.capabilities import CapabilityAdvertisement, Bandwidth
        adv = CapabilityAdvertisement(name="p", caps=["web"], fp=b"\x09" * 8,
                                      max_bw=int(Bandwidth.MEDIUM))
        sdk.registry.upsert_from_announce(adv, "dd" * 16)
        peers = sdk.discover_peers("web")
        assert len(peers) == 1
        all_peers = sdk.discover_peers()
        assert len(all_peers) == 1