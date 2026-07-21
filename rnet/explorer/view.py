"""PySide6 network explorer view. Imports Qt lazily.

Draws known peers as nodes around the local node, colored by capability and
sized by recency; reachable vs unreachable distinguished by outline style.
RTT (when known) labels the edge. A side table lists services.
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


def launch_explorer(model: ExplorerModel) -> int:
    from PySide6 import QtWidgets, QtCore, QtGui

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setWindowTitle("RNet Network Explorer")
    win.resize(900, 640)

    central = QtWidgets.QWidget()
    win.setCentralWidget(central)
    layout = QtWidgets.QHBoxLayout(central)

    scene = QtWidgets.QGraphicsScene()
    view = QtWidgets.QGraphicsView(scene)
    view.setRenderHint(QtGui.QPainter.Antialiasing)
    layout.addWidget(view, 2)

    side = QtWidgets.QVBoxLayout()
    summary = QtWidgets.QLabel("loading…")
    summary.setWordWrap(True)
    side.addWidget(summary)

    table = QtWidgets.QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["capability", "node", "reachable", "rtt(ms)"])
    side.addWidget(table, 1)
    layout.addLayout(side, 1)

    def refresh():
        s = model.summary()
        scene.clear()
        cx, cy = 400.0, 300.0
        # local node at center
        local = scene.addEllipse(cx - 14, cy - 14, 28, 28,
                                 QtGui.QPen(QtGui.QColor("#000"), 2),
                                 QtGui.QBrush(QtGui.QColor("#111")))
        scene.addText("you").setPos(cx - 10, cy + 16)

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
            brush = QtGui.QBrush(QtGui.QColor(color))
            pen = QtGui.QPen(QtGui.QColor("#0a0") if p["reachable"] else "#a00", 2)
            r = 10 + min(10, max(0, 12 - p["age"] // 60))
            scene.addLine(cx, cy, px, py,
                          QtGui.QPen(QtGui.QColor("#ccc"), 1,
                                     QtCore.Qt.DashLine if not p["reachable"] else QtCore.Qt.SolidLine))
            scene.addEllipse(px - r, py - r, r * 2, r * 2, pen, brush)
            label = scene.addText(p["name"] or p["dest_hash"][:8])
            label.setPos(px - 20, py - 30)
            if p["rtt_ms"] is not None:
                scene.addText(f"{p['rtt_ms']:.0f}ms").setPos((cx + px) / 2, (cy + py) / 2)

        cap_str = ", ".join(f"{k}={v}" for k, v in s["capabilities"].items()) or "-"
        summary.setText(
            f"nodes: {s['nodes']}  reachable: {s['reachable']}\ncapabilities: {cap_str}"
        )
        services = model.services()
        table.setRowCount(len(services))
        for row, sv in enumerate(services):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(sv["cap"]))
            table.setItem(row, 1, QtWidgets.QTableWidgetItem(sv["name"] or sv["dest"][:12]))
            table.setItem(row, 2, QtWidgets.QTableWidgetItem("yes" if sv["reachable"] else "no"))
            table.setItem(row, 3, QtWidgets.QTableWidgetItem(
                "" if sv["rtt_ms"] is None else f"{sv['rtt_ms']:.0f}"))

    refresh()

    # Refresh every few seconds.
    timer = QtCore.QTimer()
    timer.timeout.connect(refresh)
    timer.start(3000)

    win.show()
    return app.exec()