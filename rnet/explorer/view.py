"""PySide6 network explorer view. Imports Qt lazily.

:func:`ExplorerWidget` is an embeddable QWidget (used by the dashboard and by
:func:`launch_explorer`). Draws known peers as nodes around the local node,
colored by capability, sized by recency; reachable vs unreachable by outline;
RTT labels edges. A side table lists services. Polls the model on a timer.
"""
from __future__ import annotations

import math
from typing import Optional

from rnet.explorer.model import ExplorerModel

# Capability -> color.
_CAP_COLORS = {
    "messaging": "#2563eb",
    "web": "#16a34a",
    "storage": "#9333ea",
    "relay": "#ea580c",
    "naming": "#0891b2",
    "search": "#db2777",
    "social": "#ca8a04",
    "apps": "#475569",
}


def _import_qt():
    from PySide6 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui


class ExplorerWidget:
    """Embeddable network explorer widget."""

    def __init__(self, model: ExplorerModel, refresh_ms: int = 3000):
        self.model = model
        QtWidgets, QtCore, QtGui = _import_qt()
        self.QtWidgets = QtWidgets
        self.QtCore = QtCore

        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(self.widget)
        self.widget.setLayout(layout)

        self.scene = QtWidgets.QGraphicsScene()
        self.view = QtWidgets.QGraphicsView(self.scene)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        layout.addWidget(self.view, 2)

        side = QtWidgets.QVBoxLayout()
        self.summary = QtWidgets.QLabel("loading…")
        self.summary.setWordWrap(True)
        side.addWidget(self.summary)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["capability", "node", "reachable", "rtt(ms)"])
        side.addWidget(self.table, 1)
        layout.addLayout(side, 1)

        self._refresh()
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(refresh_ms)

    def _refresh(self) -> None:
        s = self.model.summary()
        self.scene.clear()
        cx, cy = 400.0, 300.0
        QtGui = self.QtWidgets  # alias module access
        from PySide6 import QtGui as QG
        local = self.scene.addEllipse(cx - 14, cy - 14, 28, 28,
                                      QG.QPen(QG.QColor("#000"), 2),
                                      QG.QBrush(QG.QColor("#111")))
        self.scene.addText("you").setPos(cx - 10, cy + 16)

        peers = s["peers"]
        n = max(1, len(peers))
        for i, p in enumerate(peers):
            ang = 2 * math.pi * i / n
            px = cx + 220 * math.cos(ang)
            py = cy + 200 * math.sin(ang)
            color = "#999"
            for c in p["caps_list"]:
                if c in _CAP_COLORS:
                    color = _CAP_COLORS[c]
                    break
            brush = QG.QBrush(QG.QColor(color))
            pen = QG.QPen(QG.QColor("#0a0") if p["reachable"] else "#a00", 2)
            r = 10 + min(10, max(0, 12 - p["age"] // 60))
            self.scene.addLine(cx, cy, px, py,
                               QG.QPen(QG.QColor("#ccc"), 1,
                                       self.QtCore.Qt.DashLine if not p["reachable"] else self.QtCore.Qt.SolidLine))
            self.scene.addEllipse(px - r, py - r, r * 2, r * 2, pen, brush)
            label = self.scene.addText(p["name"] or p["dest_hash"][:8])
            label.setPos(px - 20, py - 30)
            if p["rtt_ms"] is not None:
                self.scene.addText(f"{p['rtt_ms']:.0f}ms").setPos((cx + px) / 2, (cy + py) / 2)

        cap_str = ", ".join(f"{k}={v}" for k, v in s["capabilities"].items()) or "-"
        self.summary.setText(
            f"nodes: {s['nodes']}  reachable: {s['reachable']}\ncapabilities: {cap_str}"
        )
        services = self.model.services()
        self.table.setRowCount(len(services))
        QtWidgets = self.QtWidgets
        for row, sv in enumerate(services):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(sv["cap"]))
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(sv["name"] or sv["dest"][:12]))
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("yes" if sv["reachable"] else "no"))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(
                "" if sv["rtt_ms"] is None else f"{sv['rtt_ms']:.0f}"))

    def stop(self) -> None:
        self._timer.stop()


def launch_explorer(model: ExplorerModel) -> int:
    """Run the explorer as a standalone window. Requires a display (or offscreen)."""
    QtWidgets, QtCore, QtGui = _import_qt()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setWindowTitle("RNet Network Explorer")
    win.resize(900, 640)
    ew = ExplorerWidget(model)
    win.setCentralWidget(ew.widget)
    win.show()
    return app.exec()