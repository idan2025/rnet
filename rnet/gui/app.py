"""Main window: sidebar + stacked tabs, menu bar, themed status bar."""
from __future__ import annotations

from typing import Optional


def _import_qt():
    from PySide6 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui


# (label, module, class) for each tab — imported lazily when built.
TABS = [
    ("Status", "rnet.gui.tabs.node_tab", "StatusTab"),
    ("Conversations", "rnet.gui.tabs.messages_tab", "ConversationsTab"),
    ("Contacts", "rnet.gui.tabs.identity_tab", "ContactsTab"),
    ("Peers", "rnet.gui.tabs.peers_tab", "PeersTab"),
    ("Files", "rnet.gui.tabs.files_tab", "FilesTab"),
    ("Browser", "rnet.gui.tabs.browser_tab", "BrowserTab"),
    ("Social", "rnet.gui.tabs.social_tab", "SocialTab"),
    ("Forum", "rnet.gui.tabs.forum_tab", "ForumTab"),
    ("Explorer", "rnet.gui.tabs.explorer_tab", "ExplorerTab"),
    ("Hosting", "rnet.gui.tabs.hosting_tab", "HostingTab"),
    ("Interfaces", "rnet.gui.tabs.interfaces_tab", "InterfacesTab"),
    ("Settings", "rnet.gui.tabs.settings_tab", "SettingsTab"),
]


class MainWindow:
    """Builds the sidebar + stacked-tab window bound to a controller."""

    def __init__(self, controller, bridge, app=None, settings=None):
        self.controller = controller
        self.bridge = bridge
        self.app = app
        self.settings = settings
        QtWidgets, QtCore, QtGui = _import_qt()
        self.QtWidgets = QtWidgets
        self.QtCore = QtCore
        self.QtGui = QtGui

        self.win = QtWidgets.QMainWindow()
        self.win.setWindowTitle("RNet — The Reticulum Internet")
        self.win.resize(1180, 760)

        # Restore geometry + active tab.
        geom = self.settings.get("window_geometry") if self.settings else None
        if geom:
            self.win.restoreGeometry(QtCore.QByteArray(bytes(geom)))
        self._restore_tab = (self.settings.get("active_tab") if self.settings else 0) or 0

        central = QtWidgets.QWidget()
        self.win.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Sidebar
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(168)
        self.sidebar.setSortingEnabled(False)
        for label, _, _ in TABS:
            self.sidebar.addItem(QtWidgets.QListWidgetItem(label))
        outer.addWidget(self.sidebar)

        # Stack
        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.tab_objs = {}
        for i, (label, mod, cls) in enumerate(TABS):
            obj = self._build_tab(mod, cls)
            self.stack.addWidget(obj.widget)
            self.tab_objs[label] = obj

        idx = max(0, min(self._restore_tab, len(TABS) - 1))
        self.sidebar.setCurrentRow(idx)
        self.stack.setCurrentIndex(idx)
        self.sidebar.currentRowChanged.connect(self._on_tab_changed)

        # Status bar with a live status dot.
        self.status = self.win.statusBar()
        from rnet.gui.widgets import StatusDot
        self.status_dot = StatusDot("amber")
        self.status.addPermanentWidget(self.status_dot)
        self.status_label = QtWidgets.QLabel("starting…")
        self.status.addPermanentWidget(self.status_label)
        self.status.showMessage("starting node…")

        # Menu bar + shortcuts.
        self._build_menu()

        # Bridge -> status bar + fanout.
        if bridge is not None:
            bridge.log.connect(self.status.showMessage)
            bridge.node_started.connect(self._on_node_started)
            bridge.node_stopped.connect(self._on_node_stopped)
            # Live peer / interface counts: refresh the status bar the moment
            # a peer is discovered or an interface changes, not only on start.
            bridge.peer_discovered.connect(lambda _e: self._refresh_status())
            bridge.interface_changed.connect(lambda _e: self._refresh_status())

        # Periodic refresh so the status bar's peer / iface counters and
        # online state track reality (peers going stale, interfaces
        # going up/down) without waiting for a Reticulum restart.
        self._status_timer = QtCore.QTimer()
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(2000)
        self._refresh_status()

        self.win.closeEvent = self._close_event

    # -- tabs -------------------------------------------------------------
    def _build_tab(self, module_path: str, class_name: str):
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls(self.controller, self.bridge)

    def _on_tab_changed(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        if self.settings is not None:
            self.settings.set("active_tab", row)

    # -- node state -------------------------------------------------------
    def _on_node_started(self, event) -> None:
        from rnet.gui.widgets import StatusDot
        self.status_dot.set_state("green")
        self._refresh_status()
        for tab in self.tab_objs.values():
            if hasattr(tab, "on_node_started"):
                tab.on_node_started()

    def _on_node_stopped(self, event) -> None:
        from rnet.gui.widgets import StatusDot
        self.status_dot.set_state("grey")
        self._refresh_status()
        for tab in self.tab_objs.values():
            if hasattr(tab, "on_node_stopped"):
                tab.on_node_stopped()

    def _refresh_status(self) -> None:
        """Rebuild the bottom status bar from live node state.

        Called on start/stop, on peer_discovered / interface_changed signals,
        and on a 2s timer — so the peer + iface counters stay current without
        a Reticulum restart.
        """
        node = self.controller.node
        if node is not None and node.running:
            dest = node.node_dest_hash or ""
            try:
                n_peers = len(node.peers())
            except Exception:
                n_peers = 0
            try:
                n_if = len(self.controller.list_interfaces())
            except Exception:
                n_if = 0
            self.status_label.setText(
                f"node running · {dest[:12]}… · {n_peers} peers · {n_if} ifaces"
            )
        else:
            self.status_label.setText("node stopped")

    # -- menu + shortcuts -------------------------------------------------
    def _build_menu(self) -> None:
        QtWidgets, QtCore, QtGui = _import_qt()
        mb = self.win.menuBar()

        m_file = mb.addMenu("File")
        a_quit = QtGui.QAction("Quit", self.win)
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.win.close)
        m_file.addAction(a_quit)

        m_view = mb.addMenu("View")
        self.a_theme = QtGui.QAction("Toggle Dark/Light", self.win)
        self.a_theme.setShortcut("Ctrl+Shift+T")
        self.a_theme.triggered.connect(self.toggle_theme)
        m_view.addAction(self.a_theme)
        a_reload = QtGui.QAction("Reload active tab", self.win)
        a_reload.setShortcut("Ctrl+R")
        a_reload.triggered.connect(self._reload_active)
        m_view.addAction(a_reload)
        a_search = QtGui.QAction("Focus search", self.win)
        a_search.setShortcut("Ctrl+K")
        a_search.triggered.connect(self._focus_search)
        m_view.addAction(a_search)

        m_tools = mb.addMenu("Tools")
        a_announce = QtGui.QAction("Announce now", self.win)
        a_announce.setShortcut("Ctrl+J")
        a_announce.triggered.connect(lambda: self.controller.announce_now())
        m_tools.addAction(a_announce)
        a_restart = QtGui.QAction("Restart Reticulum", self.win)
        a_restart.triggered.connect(lambda: self.controller.restart_node())
        m_tools.addAction(a_restart)

        m_help = mb.addMenu("Help")
        a_about = QtGui.QAction("About RNet", self.win)
        a_about.triggered.connect(self._about)
        m_help.addAction(a_about)

    def toggle_theme(self) -> None:
        if self.app is None or self.settings is None:
            return
        from rnet.gui import theme
        new = theme.toggle(self.app, self.settings.get("theme", "dark"))
        self.settings.set("theme", new)
        if self.bridge is not None:
            self.bridge.theme_changed.emit(new)

    def _reload_active(self) -> None:
        obj = self.tab_objs.get(TABS[self.sidebar.currentRow()][0])
        if obj is not None and hasattr(obj, "refresh"):
            obj.refresh()

    def _focus_search(self) -> None:
        obj = self.tab_objs.get(TABS[self.sidebar.currentRow()][0])
        if obj is not None and hasattr(obj, "focus_search"):
            obj.focus_search()

    def _about(self) -> None:
        QtWidgets, _, _ = _import_qt()
        from rnet.__version__ import __version__
        dest = ""
        try:
            dest = self.controller.node.node_dest_hash or "" if self.controller.node else ""
        except Exception:
            pass
        QtWidgets.QMessageBox.about(
            self.win, "About RNet",
            f"<h3>RNet {__version__}</h3>"
            "<p>The Reticulum Internet — a decentralized, off-grid client.</p>"
            f"<p>Node: <code>{dest or 'not running'}</code></p>"
            "<p><a href='https://github.com/idan2025/rnet'>github.com/idan2025/rnet</a></p>",
        )

    # -- lifecycle --------------------------------------------------------
    def _close_event(self, event) -> None:
        if self.settings is not None:
            self.settings.set("window_geometry", bytes(self.win.saveGeometry().data()))
            self.settings.set("active_tab", self.sidebar.currentRow())
        event.accept()

    def show(self) -> None:
        self.win.show()