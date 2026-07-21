"""Explorer tab: embed the reusable ExplorerWidget."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


class ExplorerTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, _, _ = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)
        self.widget.setLayout(v)
        self._ew = None
        self.placeholder = QtWidgets.QLabel("Start the node to see the network map.")
        v.addWidget(self.placeholder)
        self._build()

    def _build(self) -> None:
        # Explorer works off the peer registry, which exists once the controller
        # has a db (even pre-start). Build immediately so users see cached peers.
        QtWidgets, _, _ = qt()
        from rnet.explorer import ExplorerModel
        from rnet.explorer.view import ExplorerWidget
        registry = self.controller.node.registry if self.controller.node else None
        model = ExplorerModel(self.controller.db, registry=registry)
        self._ew = ExplorerWidget(model)
        lay = self.widget.layout()
        lay.removeWidget(self.placeholder)
        self.placeholder.setParent(None)
        lay.addWidget(self._ew.widget, 1)

    def on_node_started(self) -> None:
        # Rebind the model to the live registry once the node is up.
        if self._ew is not None and self.controller.node is not None:
            from rnet.explorer import ExplorerModel
            self._ew.model = ExplorerModel(self.controller.db,
                                           registry=self.controller.node.registry)