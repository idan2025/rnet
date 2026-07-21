"""Peers tab: table of discovered nodes/services, refreshed on a timer."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


class PeersTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        v.addWidget(QtWidgets.QLabel("Discovered peers:"))
        self.summary = QtWidgets.QLabel("nodes: 0  reachable: 0")
        v.addWidget(self.summary)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["name", "dest", "capabilities", "age", "reachable"])
        v.addWidget(self.table, 1)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        v.addWidget(self.refresh_btn)
        self.refresh_btn.clicked.connect(self._refresh)

        if bridge is not None:
            bridge.peer_discovered.connect(lambda e: self._refresh())

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(3000)
        self._refresh()

    def _refresh(self) -> None:
        QtWidgets, _, _ = qt()
        peers = self.controller.node.peers() if self.controller.node and self.controller.node.running else \
            self.controller.node.registry.list_all() if self.controller.node else []
        self.table.setRowCount(len(peers))
        import time
        for i, p in enumerate(peers):
            age = int(time.time()) - int(p.get("last_seen", 0))
            self.table.setItem(i, 0, QtWidgets.QTableWidgetItem(p.get("name") or "?"))
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(p.get("dest_hash", "")))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(p.get("capabilities") or ""))
            self.table.setItem(i, 3, QtWidgets.QTableWidgetItem(f"{age}s"))
            self.table.setItem(i, 4, QtWidgets.QTableWidgetItem("yes" if p.get("reachable") else "no"))
        self.summary.setText(f"nodes: {len(peers)}  reachable: "
                             f"{sum(1 for p in peers if p.get('reachable'))}")