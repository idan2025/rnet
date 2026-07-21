"""Node tab: start/stop the node, configure name/capabilities, live log."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


ALL_CAPS = ["messaging", "relay", "web", "storage", "naming", "search", "social"]


class NodeTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        # Identity selection
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Node identity:"))
        self.identity_box = QtWidgets.QComboBox()
        self.identity_box.setEditable(True)
        row.addWidget(self.identity_box, 1)
        v.addLayout(row)

        # Name (defaults to selected identity)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Name:"))
        self.name_field = QtWidgets.QLineEdit("rnet-node")
        row.addWidget(self.name_field, 1)
        v.addLayout(row)

        # Capabilities
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Capabilities:"))
        self.cap_checks = {}
        for cap in ALL_CAPS:
            cb = QtWidgets.QCheckBox(cap)
            if cap in ("messaging", "relay"):
                cb.setChecked(True)
            self.cap_checks[cap] = cb
            row.addWidget(cb)
        v.addLayout(row)

        # Low power + bandwidth
        row = QtWidgets.QHBoxLayout()
        self.low_power = QtWidgets.QCheckBox("low-power (sleepy node)")
        row.addWidget(self.low_power)
        row.addWidget(QtWidgets.QLabel("Max bandwidth:"))
        self.bw_box = QtWidgets.QComboBox()
        self.bw_box.addItems(["low", "medium", "high"])
        self.bw_box.setCurrentText("medium")
        row.addWidget(self.bw_box)
        v.addLayout(row)

        # Start / stop
        row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("Start node")
        self.stop_btn = QtWidgets.QPushButton("Stop node")
        self.stop_btn.setEnabled(False)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        v.addLayout(row)

        # Dest + status
        self.dest_label = QtWidgets.QLabel("node: not running")
        v.addWidget(self.dest_label)

        # Live log
        v.addWidget(QtWidgets.QLabel("Log:"))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)

        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)

        if bridge is not None:
            bridge.log.connect(self.log.appendPlainText)
            bridge.node_started.connect(lambda e: self._post_start(e))
            bridge.node_stopped.connect(lambda e: self._post_stop())

        self._refresh_identities()

    def _refresh_identities(self) -> None:
        QtWidgets, _, _ = qt()
        self.identity_box.clear()
        rows = self.controller.list_own_identities()
        for r in rows:
            self.identity_box.addItem(f"{r['name']} ({r['dest_hash'][:8]}…)")
        if rows:
            self.identity_box.setCurrentIndex(0)
            self.name_field.setText(rows[0]["name"])

    def _selected_name(self) -> str:
        txt = self.identity_box.currentText()
        # "alice (12e95047…)" -> "alice"
        return txt.split(" (")[0].strip() or self.name_field.text().strip()

    def _on_start(self) -> None:
        name = self._selected_name() or self.name_field.text().strip()
        if not name:
            self.log.appendPlainText("error: pick or create an identity first")
            return
        caps = [c for c, cb in self.cap_checks.items() if cb.isChecked()]
        # If web checked but no web_root yet, that's handled on the Hosting tab
        # via restart; here we just pass None and let Hosting restart with root.
        web_root = None
        if "web" in caps:
            web_root = getattr(self.controller, "web_root", None)
        self.start_btn.setEnabled(False)
        self.log.appendPlainText(f"starting node '{name}'…")

        def on_done(node):
            self.log.appendPlainText("node started")

        def on_error(exc):
            self.log.appendPlainText(f"start failed: {exc}")
            self.start_btn.setEnabled(True)

        self.controller.start_node(
            name=name, capabilities=caps,
            low_power=self.low_power.isChecked(),
            max_bandwidth=self.bw_box.currentText(),
            web_root=web_root,
            on_done=on_done, on_error=on_error,
        )

    def _on_stop(self) -> None:
        self.stop_btn.setEnabled(False)
        self.log.appendPlainText("stopping node…")
        self.controller.stop_node(on_done=lambda _r: self.log.appendPlainText("node stopped"),
                                  on_error=lambda e: self.log.appendPlainText(f"stop failed: {e}"))

    def _post_start(self, event) -> None:
        dest = event.get("dest", "") if isinstance(event, dict) else ""
        self.dest_label.setText(f"node: {dest}")
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _post_stop(self) -> None:
        self.dest_label.setText("node: not running")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def on_node_started(self) -> None:
        self._refresh_identities()