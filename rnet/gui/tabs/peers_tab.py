"""Peers tab: live discovered-peer table with announce + filters."""
from __future__ import annotations

import time

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, SearchField, StatusDot, Toast, confirm


class PeersTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Toolbar.
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(SectionLabel("Discovered peers"))
        bar.addStretch(1)
        self.filter = QtWidgets.QComboBox()
        self.filter.addItem("all capabilities")
        for c in ("messaging", "relay", "web", "storage", "naming", "search", "social"):
            self.filter.addItem(c)
        self.filter.currentIndexChanged.connect(lambda _: self._refresh())
        bar.addWidget(QtWidgets.QLabel("filter:"))
        bar.addWidget(self.filter)
        self.search = SearchField("Search peers…")
        self.search.textChanged.connect(self._filter_text)
        bar.addWidget(self.search, 2)
        self.announce_btn = QtWidgets.QPushButton("Announce now")
        self.announce_btn.setObjectName("primary")
        self.announce_btn.clicked.connect(self._on_announce)
        bar.addWidget(self.announce_btn)
        root.addLayout(bar)

        # Summary.
        self.summary = QtWidgets.QLabel("nodes: 0  reachable: 0")
        root.addWidget(self.summary)

        # Table.
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "name", "dest", "capabilities", "hops", "age"])
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
        act_copy = QtGui.QAction("Copy dest hash", self.table)
        act_copy.triggered.connect(self._copy_selected)
        self.table.addAction(act_copy)
        act_block = QtGui.QAction("Block peer", self.table)
        act_block.triggered.connect(self._block_selected)
        self.table.addAction(act_block)
        root.addWidget(self.table, 1)

        if bridge is not None:
            bridge.peer_discovered.connect(lambda _e: self._refresh())

        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(3000)
        self._refresh()

    def _peers(self):
        node = self.controller.node
        if node is None:
            return []
        if not node.running:
            try:
                return node.registry.list_all() if node.registry else []
            except Exception:
                return []
        return node.peers()

    def _refresh(self) -> None:
        QtWidgets, _, _ = qt()
        cap_filter = self.filter.currentText()
        peers = self._peers()
        if cap_filter and cap_filter != "all capabilities":
            peers = [p for p in peers if cap_filter in (p.get("capabilities") or "")]
        self.table.setRowCount(len(peers))
        for i, p in enumerate(peers):
            dot = StatusDot("green" if p.get("reachable") else "grey")
            self.table.setCellWidget(i, 0, dot)
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(p.get("name") or "?"))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(p.get("dest_hash", "")))
            self.table.setItem(i, 3, QtWidgets.QTableWidgetItem(p.get("capabilities") or ""))
            self.table.setItem(i, 4, QtWidgets.QTableWidgetItem(str(p.get("hops")) if p.get("hops") is not None else "—"))
            age = int(time.time()) - int(p.get("last_seen", 0) or 0)
            self.table.setItem(i, 5, QtWidgets.QTableWidgetItem(f"{age}s"))
        reach = sum(1 for p in peers if p.get("reachable"))
        self.summary.setText(f"nodes: {len(peers)}  reachable: {reach}")
        self._filter_text(self.search.text())

    def _filter_text(self, needle: str) -> None:
        needle = (needle or "").lower()
        for i in range(self.table.rowCount()):
            cells = " ".join(self.table.item(i, c).text().lower()
                             for c in (1, 2, 3) if self.table.item(i, c))
            self.table.setRowHidden(i, bool(needle) and needle not in cells)

    def _on_announce(self) -> None:
        try:
            self.controller.announce_now()
            Toast.show_in(self.widget.window().statusBar(), "announce sent", 3000)
        except Exception as exc:
            Toast.show_in(self.widget.window().statusBar(), f"announce failed: {exc}")

    def _selected_dest(self) -> str:
        r = self.table.currentRow()
        if r < 0:
            return ""
        return self.table.item(r, 2).text()

    def _copy_selected(self) -> None:
        d = self._selected_dest()
        if d:
            QtWidgets.QApplication.clipboard().setText(d)

    def _block_selected(self) -> None:
        d = self._selected_dest()
        if not d:
            return
        if not confirm(self.widget, "Block peer", f"Block {d[:12]}…? Messages from blocked peers are hidden."):
            return
        self.controller.set_blocked(d, True)
        self._refresh()

    def on_node_started(self) -> None:
        self._refresh()

    def refresh(self) -> None:
        self._refresh()

    def focus_search(self) -> None:
        self.search.setFocus()