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
    expected = {"Node", "Identities", "Messages", "Peers", "Hosting",
                "Files", "Browser", "Social", "Forum", "Explorer"}
    assert set(w.tab_objs.keys()) == expected
    w.show()
    qapp.processEvents()
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