"""Browser tab: embed the reusable BrowserWidget."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


class BrowserTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)
        self.widget.setLayout(v)

        self._bw = None  # BrowserWidget, built on node start
        self.placeholder = QtWidgets.QLabel("Start the node to browse the mesh web.")
        v.addWidget(self.placeholder)
        self._layout = v

    def on_node_started(self) -> None:
        QtWidgets, _, _ = qt()
        if self._bw is not None:
            return
        sdk = self.controller.sdk
        if sdk is None:
            return
        from rnet.browser import BrowserModel
        from rnet.browser.view import BrowserWidget
        from rnet.web import WebClient, RNSWebTransport
        web = WebClient(RNSWebTransport(), sdk.content_store, sdk.manifest_store, sdk.idm)
        model = BrowserModel(sdk.db, sdk.idm, web, sdk.naming,
                             peer_registry=getattr(sdk, "registry", None))
        self._bw = BrowserWidget(model, self.controller.loop)
        # Swap placeholder for the browser widget.
        self._layout.removeWidget(self.placeholder)
        self.placeholder.setParent(None)
        self._layout.addWidget(self._bw.widget, 1)