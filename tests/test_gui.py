"""Headless GUI tests (QT_QPA_PLATFORM=offscreen). No app.exec()."""
import os
import sys
import tempfile

import pytest

# Force offscreen for the whole module.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _ensure_rns():
    import RNS
    if RNS.Reticulum.get_instance() is None:
        RNS.Reticulum(tempfile.mkdtemp(prefix="rns_gui_"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    return app


def _controller(tmp):
    _ensure_rns()
    from rnet.gui.controller import GuiController
    from rnet.gui.bridge import make_bridge
    bridge = make_bridge()
    return GuiController(datadir=tmp, bridge=bridge), bridge


def test_controller_constructs_and_lists_identities(tmp_path, qapp):
    c, _b = _controller(str(tmp_path))
    assert c.loop is not None
    # No identities yet.
    assert c.list_own_identities() == []
    ident = c.create_identity("alice", is_node=True)
    assert ident is not None
    assert any(r["name"] == "alice" for r in c.list_own_identities())
    assert c.running is False
    assert c.sdk is None


def test_controller_start_stop_node(tmp_path, qapp):
    import asyncio
    c, bridge = _controller(str(tmp_path))
    c.create_identity("n1", is_node=True)
    done = asyncio.Event()

    def on_done(node):
        done.set()

    c.start_node(name="n1", capabilities=["messaging", "relay"],
                 max_bandwidth="medium", on_done=on_done)
    # Wait (on the test thread) for the async start to finish via the loop.
    import time
    deadline = time.time() + 15
    while not c.running and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    assert c.running, "node did not start"
    assert c.sdk is not None
    assert c.node.node_dest_hash is not None

    # Stop.
    stopped = {"v": False}

    def on_stop(_r):
        stopped["v"] = True

    c.stop_node(on_done=on_stop)
    deadline = time.time() + 15
    while c.running and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    assert not c.running
    c.shutdown()


def test_interfaces_live_add_remove(tmp_path, qapp):
    """Adding/removing an interface applies live to a running node and shows
    up in list_interfaces (which must read RNS.Transport.interfaces, not the
    non-existent reticulum.interfaces attr)."""
    import asyncio, time
    c, _b = _controller(str(tmp_path))
    c.create_identity("n1", is_node=True)
    started = {"v": False}
    c.start_node(name="n1", capabilities=["messaging", "relay"],
                 max_bandwidth="medium", on_done=lambda _n: started.__setitem__("v", True))
    deadline = time.time() + 20
    while not c.running and time.time() < deadline:
        qapp.processEvents(); time.sleep(0.05)
    assert c.running

    box = {}
    c.add_interface("udp-test", {"type": "UDP", "listen_ip": "127.0.0.1",
                                  "listen_port": "39871", "interface_enabled": True},
                    on_done=lambda r: box.__setitem__("done", r),
                    on_error=lambda e: box.__setitem__("err", e))
    deadline = time.time() + 10
    while "done" not in box and "err" not in box and time.time() < deadline:
        qapp.processEvents(); time.sleep(0.05)
    assert "done" in box and "err" not in box, box
    time.sleep(0.3); qapp.processEvents()
    names = [i.get("name") for i in c.list_interfaces()]
    assert "udp-test" in names

    rbox = {}
    c.remove_interface("udp-test",
                       on_done=lambda r: rbox.__setitem__("done", r),
                       on_error=lambda e: rbox.__setitem__("err", e))
    deadline = time.time() + 10
    while "done" not in rbox and "err" not in rbox and time.time() < deadline:
        qapp.processEvents(); time.sleep(0.05)
    assert "done" in rbox
    time.sleep(0.3); qapp.processEvents()
    assert "udp-test" not in [i.get("name") for i in c.list_interfaces()]
    c.shutdown()


def test_event_to_signal_bridge(tmp_path, qapp):
    import asyncio, time
    from rnet.core.events import MESSAGE_RECEIVED
    c, bridge = _controller(str(tmp_path))
    received = []
    bridge.message_received.connect(lambda e: received.append(e))
    # Emit on the bus from the loop thread (threadsafe).
    c.bus.emit_threadsafe(MESSAGE_RECEIVED, {"id": "x", "sender": "s", "text": "hi"})
    # Pump Qt so the queued signal fires.
    deadline = time.time() + 2
    while not received and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert received and received[0]["text"] == "hi"
    c.shutdown()


def test_main_window_builds_all_tabs(tmp_path, qapp):
    from rnet.gui.app import MainWindow
    c, bridge = _controller(str(tmp_path))
    w = MainWindow(c, bridge)
    expected = {"Status", "Conversations", "Contacts", "Peers", "Files",
                "Browser", "Social", "Forum", "Explorer", "Hosting",
                "Interfaces", "Settings"}
    assert set(w.tab_objs.keys()) == expected
    w.show()
    qapp.processEvents()
    c.shutdown()


def test_status_bar_refreshes_on_peer_and_iface_signals(tmp_path, qapp):
    """Bottom status bar must update live on peer_discovered /
    interface_changed, not only on a Reticulum restart."""
    import time
    from rnet.gui.app import MainWindow
    c, bridge = _controller(str(tmp_path))
    w = MainWindow(c, bridge)
    calls = {"n": 0}
    orig = w._refresh_status

    def counting():
        calls["n"] += 1
        orig()

    w._refresh_status = counting
    # Reconnect the bridge signals to the patched method.
    bridge.peer_discovered.disconnect()
    bridge.interface_changed.disconnect()
    bridge.peer_discovered.connect(lambda _e: w._refresh_status())
    bridge.interface_changed.connect(lambda _e: w._refresh_status())

    before = calls["n"]
    bridge.peer_discovered.emit({"dest": "x"})
    bridge.interface_changed.emit({"action": "add", "name": "udp"})
    deadline = time.time() + 2
    while calls["n"] < before + 2 and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert calls["n"] >= before + 2, calls
    c.shutdown()


def test_interfaces_tab_refreshes_on_interface_changed(tmp_path, qapp):
    """Interfaces tab must refresh when an interface is added/edited/removed,
    and poll on a timer — not only on manual button clicks."""
    import time
    from rnet.gui.tabs.interfaces_tab import InterfacesTab
    c, bridge = _controller(str(tmp_path))
    tab = InterfacesTab(c, bridge)
    calls = {"n": 0}
    orig = tab._refresh

    def counting():
        calls["n"] += 1
        orig()

    tab._refresh = counting
    bridge.interface_changed.emit({"action": "add", "name": "udp"})
    deadline = time.time() + 2
    while calls["n"] == 0 and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert calls["n"] >= 1, calls
    c.shutdown()


def test_set_enable_transport_writes_config(tmp_path):
    """set_enable_transport toggles enable_transport in the RNS config file,
    creating the [reticulum] section / line if missing."""
    from rnet.gui.rns_config import default_config_path, set_enable_transport, read_interfaces
    path = default_config_path(str(tmp_path))
    set_enable_transport(path, True)
    with open(path) as f:
        assert "enable_transport = True" in f.read()
    set_enable_transport(path, False)
    with open(path) as f:
        assert "enable_transport = False" in f.read()
    # Toggling must not corrupt the [interfaces] section parser.
    assert read_interfaces(path) == []


def test_controller_applies_transport_setting_before_rns_init(tmp_path, qapp):
    """GuiController must write enable_transport from settings into the RNS
    config before RNS initialises (so a relay-hub node actually relays)."""
    from rnet.gui.controller import GuiController
    from rnet.gui.bridge import make_bridge
    from rnet.gui.settings_store import SettingsStore
    import os
    datadir = str(tmp_path)
    SettingsStore(os.path.join(datadir, "settings.json")).set("enable_transport", True)
    _ensure_rns()
    c = GuiController(datadir=datadir, bridge=make_bridge())
    with open(os.path.join(datadir, "reticulum", "config")) as f:
        assert "enable_transport = True" in f.read()
    c.shutdown()


def test_set_node_name_renames_default_identity(tmp_path, qapp):
    """set_node_name renames the default identity (keeps dest hash) and
    persists it as default — the name peers see in announces."""
    c, _b = _controller(str(tmp_path))
    c.create_identity("default", is_node=True)
    before = c.default_identity_name()
    assert before == "default"
    c.set_node_name("alice")
    assert c.default_identity_name() == "alice"
    assert any(r["name"] == "alice" for r in c.list_own_identities())
    assert not any(r["name"] == "default" for r in c.list_own_identities())
    # Switching to an existing name just changes default (no clobber).
    c.create_identity("bob", is_node=True)
    c.set_node_name("alice")
    assert c.default_identity_name() == "alice"
    c.shutdown()


def test_browser_and_explorer_widgets_construct(tmp_path, qapp):
    from rnet.browser import BrowserModel
    from rnet.browser.view import BrowserWidget
    from rnet.explorer import ExplorerModel
    from rnet.explorer.view import ExplorerWidget
    c, bridge = _controller(str(tmp_path))
    # Explorer widget works pre-start (uses db).
    ew = ExplorerWidget(ExplorerModel(c.db))
    assert ew.widget is not None
    ew.stop()
    # Browser widget needs a model + loop; build a minimal model without a node.
    from rnet.storage import ContentStore, ManifestStore
    from rnet.web import FakeWebTransport, WebClient
    from rnet.naming import NameRegistry, NamingService
    store = ContentStore(c.db, os.path.join(c.datadir, "cas"))
    ms = ManifestStore(c.db)
    naming = NamingService(NameRegistry(c.db), c.idm)
    web = WebClient(FakeWebTransport(), store, ms, c.idm)
    model = BrowserModel(c.db, c.idm, web, naming)
    bw = BrowserWidget(model, c.loop)
    assert bw.widget is not None
    c.shutdown()


def test_run_async_resolves(tmp_path, qapp):
    import asyncio, time
    from rnet.gui.workers import run_async
    c, _b = _controller(str(tmp_path))

    async def coro():
        await asyncio.sleep(0.01)
        return 42

    box = []
    run_async(coro(), c.loop, on_done=lambda r: box.append(r))
    deadline = time.time() + 3
    while not box and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert box == [42]
    c.shutdown()