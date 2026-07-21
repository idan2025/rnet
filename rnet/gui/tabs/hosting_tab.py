"""Hosting tab: pick a directory, start hosting, show dest + URL."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


class HostingTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        v.addWidget(QtWidgets.QLabel("Host a website over RHTTP. Pick a directory:"))
        row = QtWidgets.QHBoxLayout()
        self.dir_field = QtWidgets.QLineEdit()
        self.dir_field.setPlaceholderText("/path/to/website")
        row.addWidget(self.dir_field, 1)
        browse = QtWidgets.QPushButton("Browse…")
        row.addWidget(browse)
        v.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Host name:"))
        self.name_field = QtWidgets.QLineEdit("rnet-host")
        row.addWidget(self.name_field, 1)
        v.addLayout(row)

        self.enable_messaging = QtWidgets.QCheckBox("also enable messaging")
        v.addWidget(self.enable_messaging)

        self.host_btn = QtWidgets.QPushButton("Start hosting")
        v.addWidget(self.host_btn)

        self.info = QtWidgets.QLabel("(not hosting)")
        v.addWidget(self.info)

        v.addStretch(1)

        browse.clicked.connect(self._on_browse)
        self.host_btn.clicked.connect(self._on_host)

    def _on_browse(self) -> None:
        QtWidgets, _, _ = qt()
        d = QtWidgets.QFileDialog.getExistingDirectory(self.widget, "Choose website directory")
        if d:
            self.dir_field.setText(d)

    def _on_host(self) -> None:
        QtWidgets, _, _ = qt()
        root = self.dir_field.text().strip()
        if not root:
            QtWidgets.QMessageBox.warning(self.widget, "hosting", "choose a directory first")
            return
        # Stash web_root on the controller so the Node tab picks it up, and
        # (re)start the node with web capability.
        self.controller.web_root = root
        name = self.name_field.text().strip() or "rnet-host"
        caps = ["web"]
        if self.enable_messaging.isChecked():
            caps.append("messaging")
        # If already running, stop first then start with web.
        if self.controller.running:
            def after_stop(_r):
                self.controller.start_node(name=name, capabilities=caps,
                                           web_root=root,
                                           on_done=lambda n: self.info.setText(
                                               f"hosting {root} — dest {self.controller.node.node_dest_hash}"),
                                           on_error=lambda e: self.info.setText(f"failed: {e}"))
            self.controller.stop_node(on_done=after_stop, on_error=lambda e: self.info.setText(f"stop failed: {e}"))
        else:
            self.controller.start_node(name=name, capabilities=caps, web_root=root,
                on_done=lambda n: self.info.setText(
                    f"hosting {root} — dest {self.controller.node.node_dest_hash}"),
                on_error=lambda e: self.info.setText(f"failed: {e}"))
        self.info.setText("starting host…")