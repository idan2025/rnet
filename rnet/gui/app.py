"""Main window: sidebar + stacked tabs."""
from __future__ import annotations

from typing import Optional


def _import_qt():
    from PySide6 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui


# (label, module, class) for each tab — imported lazily when built.
TABS = [
    ("Node", "rnet.gui.tabs.node_tab", "NodeTab"),
    ("Identities", "rnet.gui.tabs.identity_tab", "IdentityTab"),
    ("Messages", "rnet.gui.tabs.messages_tab", "MessagesTab"),
    ("Peers", "rnet.gui.tabs.peers_tab", "PeersTab"),
    ("Hosting", "rnet.gui.tabs.hosting_tab", "HostingTab"),
    ("Files", "rnet.gui.tabs.files_tab", "FilesTab"),
    ("Browser", "rnet.gui.tabs.browser_tab", "BrowserTab"),
    ("Social", "rnet.gui.tabs.social_tab", "SocialTab"),
    ("Forum", "rnet.gui.tabs.forum_tab", "ForumTab"),
    ("Explorer", "rnet.gui.tabs.explorer_tab", "ExplorerTab"),
]


class MainWindow:
    """Builds the sidebar + stacked-tab window bound to a controller."""

    def __init__(self, controller, bridge):
        self.controller = controller
        self.bridge = bridge
        QtWidgets, QtCore, QtGui = _import_qt()
        self.QtWidgets = QtWidgets

        self.win = QtWidgets.QMainWindow()
        self.win.setWindowTitle("RNet — The Reticulum Internet")
        self.win.resize(1100, 720)

        central = QtWidgets.QWidget()
        self.win.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)

        # Sidebar
        self.sidebar = QtWidgets.QListWidget()
        self.sidebar.setFixedWidth(150)
        for label, _, _ in TABS:
            self.sidebar.addItem(QtWidgets.QListWidgetItem(label))
        outer.addWidget(self.sidebar)

        # Stack
        self.stack = QtWidgets.QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.tab_objs = {}
        for label, mod, cls in TABS:
            obj = self._build_tab(mod, cls)
            self.stack.addWidget(obj.widget)
            self.tab_objs[label] = obj

        self.sidebar.setCurrentRow(0)
        self.stack.setCurrentIndex(0)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)

        # Status bar
        self.status = self.win.statusBar()
        self.status.showMessage("ready — start the node on the Node tab")

        # Bridge -> status bar
        if bridge is not None:
            bridge.log.connect(self.status.showMessage)
            bridge.node_started.connect(lambda e: self._on_node_started(e))
            bridge.node_stopped.connect(lambda e: self._on_node_stopped(e))

    def _build_tab(self, module_path: str, class_name: str):
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls(self.controller, self.bridge)

    def _on_node_started(self, event) -> None:
        dest = event.get("dest", "") if isinstance(event, dict) else ""
        self.status.showMessage(f"node running — dest {dest}")
        for tab in self.tab_objs.values():
            if hasattr(tab, "on_node_started"):
                tab.on_node_started()

    def _on_node_stopped(self, event) -> None:
        self.status.showMessage("node stopped")
        for tab in self.tab_objs.values():
            if hasattr(tab, "on_node_stopped"):
                tab.on_node_stopped()

    def show(self) -> None:
        self.win.show()