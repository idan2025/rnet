"""Status tab: live node dashboard (no manual start/stop)."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import (
    qt, Card, SectionLabel, CopyLabel, StatusDot, Avatar, IconButton, Toast,
)


def _fmt_uptime(secs: float) -> str:
    s = int(secs)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m {s%60}s"
    if s < 86400:
        return f"{s//3600}h {(s%3600)//60}m"
    return f"{s//86400}d {(s%86400)//3600}h"


class StatusTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header card: avatar + name + dest + status dot.
        head = Card()
        head_lay = QtWidgets.QVBoxLayout(head)
        head_lay.setContentsMargins(12, 12, 12, 12)
        hrow = QtWidgets.QHBoxLayout()
        self.avatar = Avatar("rnet", "RNet", 48)
        hrow.addWidget(self.avatar)
        col = QtWidgets.QVBoxLayout()
        self.name_label = QtWidgets.QLabel("rnet-node")
        f = self.name_label.font(); f.setPointSize(14); f.setBold(True)
        self.name_label.setFont(f)
        col.addWidget(self.name_label)
        self.dest_label = CopyLabel("node: not running")
        col.addWidget(self.dest_label)
        hrow.addLayout(col, 1)
        self.run_dot = StatusDot("amber")
        hrow.addWidget(self.run_dot)
        head_lay.addLayout(hrow)
        root.addWidget(head)

        # Stats row.
        stats = Card("h")
        stats_lay = QtWidgets.QHBoxLayout(stats)
        stats_lay.setContentsMargins(12, 12, 12, 12)
        self.stat_peers = self._stat(stats, "Peers", "0")
        self.stat_ifaces = self._stat(stats, "Interfaces", "0")
        self.stat_uptime = self._stat(stats, "Uptime", "—")
        self.stat_caps = self._stat(stats, "Capabilities", "—")
        root.addWidget(stats)

        # Actions row.
        acts = QtWidgets.QHBoxLayout()
        self.announce_btn = QtWidgets.QPushButton("Announce now")
        self.announce_btn.setObjectName("primary")
        self.announce_btn.clicked.connect(self._on_announce)
        acts.addWidget(self.announce_btn)
        acts.addStretch(1)
        root.addLayout(acts)

        # Log card with filter + clear.
        logcard = Card()
        logcard_lay = QtWidgets.QVBoxLayout(logcard)
        logcard_lay.setContentsMargins(12, 12, 12, 12)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(SectionLabel("Live log"))
        lrow.addStretch(1)
        self.log_search = QtWidgets.QLineEdit()
        self.log_search.setPlaceholderText("Filter log…")
        self.log_search.setClearButtonEnabled(True)
        self.log_search.textChanged.connect(self._filter_log)
        lrow.addWidget(self.log_search, 2)
        clr = QtWidgets.QPushButton("Clear")
        clr.clicked.connect(lambda: self.log.clear())
        lrow.addWidget(clr)
        logcard.layout().addLayout(lrow)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        logcard.layout().addWidget(self.log)
        root.addWidget(logcard, 1)
        self._log_lines = []

        if bridge is not None:
            bridge.log.connect(self._append_log)
            bridge.node_started.connect(lambda e: self._post_start(e))
            bridge.node_stopped.connect(lambda e: self._post_stop())

        # Live uptime + counters.
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _stat(self, card, label, value):
        QtWidgets, _, _ = qt()
        box = QtWidgets.QVBoxLayout()
        cap = QtWidgets.QLabel(label)
        cap.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        val = QtWidgets.QLabel(value)
        f = val.font(); f.setPointSize(16); f.setBold(True)
        val.setFont(f)
        box.addWidget(cap); box.addWidget(val)
        card.layout().addLayout(box)
        return val

    def _tick(self) -> None:
        node = self.controller.node
        if node and node.running:
            self.run_dot.set_state("green")
            self.dest_label.setText(node.node_dest_hash or "")
            self.stat_peers.setText(str(len(node.peers())))
            self.stat_ifaces.setText(str(len(node.interfaces())))
            self.stat_uptime.setText(_fmt_uptime(node.uptime()))
            self.stat_caps.setText(", ".join(node.config.capabilities))
            self.name_label.setText(node.config.name)
        else:
            self.run_dot.set_state("grey")
            self.stat_uptime.setText("—")

    def _on_announce(self) -> None:
        try:
            self.controller.announce_now()
            self._append_log("announce sent")
        except Exception as exc:
            self._append_log(f"announce failed: {exc}")

    def _append_log(self, text: str) -> None:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        item = f"[{ts}] {text}"
        self._log_lines.append(item)
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-2000:]
        self._render_log()

    def _filter_log(self, needle: str) -> None:
        self._render_log()

    def _render_log(self) -> None:
        needle = (self.log_search.text() or "").lower()
        lines = [ln for ln in self._log_lines
                 if (not needle) or (needle in ln.lower())]
        self.log.setPlainText("\n".join(lines))

    def _post_start(self, event) -> None:
        dest = event.get("dest", "") if isinstance(event, dict) else ""
        self.dest_label.setText(dest)
        self._tick()

    def _post_stop(self) -> None:
        self.dest_label.setText("node: not running")
        self._tick()

    def on_node_started(self) -> None:
        self._tick()

    def refresh(self) -> None:
        self._tick()

    def focus_search(self) -> None:
        self.log_search.setFocus()