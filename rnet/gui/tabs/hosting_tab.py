"""Hosting tab: host a website over RHTTP with start/stop + status."""
from __future__ import annotations

import os

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, CopyLabel, StatusDot, warn


class HostingTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Status card.
        status = Card()
        sl = QtWidgets.QVBoxLayout(status)
        srow = QtWidgets.QHBoxLayout()
        self.dot = StatusDot("grey")
        srow.addWidget(self.dot)
        self.status_label = QtWidgets.QLabel("not hosting")
        f = self.status_label.font(); f.setBold(True)
        self.status_label.setFont(f)
        srow.addWidget(self.status_label)
        srow.addStretch(1)
        sl.addLayout(srow)
        self.dest_label = CopyLabel("")
        sl.addWidget(self.dest_label)
        root.addWidget(status)

        # Config card.
        cfg = Card()
        cl = QtWidgets.QVBoxLayout(cfg)
        cl.addWidget(SectionLabel("Website directory"))
        row = QtWidgets.QHBoxLayout()
        self.dir_field = QtWidgets.QLineEdit()
        self.dir_field.setPlaceholderText("/path/to/website")
        row.addWidget(self.dir_field, 1)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)
        cl.addLayout(row)
        cl.addWidget(SectionLabel("Host name"))
        self.name_field = QtWidgets.QLineEdit("rnet-host")
        cl.addWidget(self.name_field)
        arow = QtWidgets.QHBoxLayout()
        self.host_btn = QtWidgets.QPushButton("Start hosting")
        self.host_btn.setObjectName("primary")
        self.host_btn.clicked.connect(self._on_host)
        self.stop_btn = QtWidgets.QPushButton("Stop hosting")
        self.stop_btn.clicked.connect(self._on_stop)
        self.stop_btn.setEnabled(False)
        arow.addWidget(self.host_btn)
        arow.addWidget(self.stop_btn)
        arow.addStretch(1)
        cl.addLayout(arow)
        root.addWidget(cfg)

        # Served files preview.
        files = Card()
        fl = QtWidgets.QVBoxLayout(files)
        fl.addWidget(SectionLabel("Served files"))
        self.file_list = QtWidgets.QListWidget()
        fl.addWidget(self.file_list, 1)
        root.addWidget(files, 1)

        self._refresh_status()

    def _on_browse(self) -> None:
        QtWidgets, _, _ = qt()
        d = QtWidgets.QFileDialog.getExistingDirectory(self.widget, "Choose website directory")
        if d:
            self.dir_field.setText(d)
            self._list_files(d)

    def _list_files(self, d: str) -> None:
        QtWidgets, _, _ = qt()
        self.file_list.clear()
        if not d or not os.path.isdir(d):
            return
        try:
            for name in sorted(os.listdir(d)):
                self.file_list.addItem(QtWidgets.QListWidgetItem(name))
        except Exception:
            pass

    def _on_host(self) -> None:
        QtWidgets, _, _ = qt()
        root = self.dir_field.text().strip()
        if not root or not os.path.isdir(root):
            warn(self.widget, "hosting", "choose an existing directory first")
            return
        self.host_btn.setEnabled(False)

        def on_done(node):
            self._refresh_status()

        def on_error(e):
            warn(self.widget, "hosting failed", str(e))
            self.host_btn.setEnabled(True)
            self._refresh_status()

        self.controller.start_hosting(root, on_done=on_done, on_error=on_error)
        self._list_files(root)

    def _on_stop(self) -> None:
        self.stop_btn.setEnabled(False)
        self.controller.stop_hosting(on_done=lambda _r: self._refresh_status(),
                                      on_error=lambda e: (warn(self.widget, "stop failed", str(e)),
                                                          self._refresh_status()))

    def _refresh_status(self) -> None:
        QtWidgets, _, _ = qt()
        node = self.controller.node
        hosting = node is not None and node.running and "web" in (node.config.capabilities or [])
        if hosting:
            self.dot.set_state("green")
            self.status_label.setText("hosting")
            self.dest_label.setText(node.node_dest_hash or "")
            self.stop_btn.setEnabled(True)
            self.host_btn.setEnabled(False)
            if node.config.web_root:
                self.dir_field.setText(node.config.web_root)
                self._list_files(node.config.web_root)
        else:
            self.dot.set_state("grey")
            self.status_label.setText("not hosting")
            self.dest_label.setText("")
            self.stop_btn.setEnabled(False)
            self.host_btn.setEnabled(True)

    def on_node_started(self) -> None:
        self._refresh_status()

    def on_node_stopped(self) -> None:
        self._refresh_status()

    def refresh(self) -> None:
        self._refresh_status()