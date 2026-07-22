"""Settings tab: theme, identity, node config, restart, about."""
from __future__ import annotations

import os

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, open_path, Toast, warn

ALL_CAPS = ["messaging", "relay", "web", "storage", "naming", "search", "social"]


class SettingsTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Appearance.
        app_card = Card()
        al = QtWidgets.QVBoxLayout(app_card)
        al.addWidget(SectionLabel("Appearance"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Theme:"))
        self.theme_box = QtWidgets.QComboBox()
        self.theme_box.addItems(["dark", "light"])
        self.theme_box.setCurrentText(self.controller.settings.get("theme", "dark"))
        row.addWidget(self.theme_box)
        row.addStretch(1)
        al.addLayout(row)
        root.addWidget(app_card)

        # Identity + node.
        node_card = Card()
        nl = QtWidgets.QVBoxLayout(node_card)
        nl.addWidget(SectionLabel("Node"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Node name:"))
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("the name other peers see")
        self.name_edit.setText(self.controller.default_identity_name() or "")
        row.addWidget(self.name_edit, 1)
        nl.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Default identity:"))
        self.id_box = QtWidgets.QComboBox()
        for r in self.controller.list_own_identities():
            self.id_box.addItem(r["name"])
        default = self.controller.default_identity_name()
        if default:
            idx = max(0, self.id_box.findText(default))
            self.id_box.setCurrentIndex(idx)
        row.addWidget(self.id_box, 1)
        nl.addLayout(row)

        nl.addWidget(SectionLabel("Capabilities"))
        crow = QtWidgets.QHBoxLayout()
        self.cap_checks = {}
        for cap in ALL_CAPS:
            cb = QtWidgets.QCheckBox(cap)
            cb.setChecked(cap in (self.controller.settings.get("capabilities") or []))
            self.cap_checks[cap] = cb
            crow.addWidget(cb)
        nl.addLayout(crow)

        row = QtWidgets.QHBoxLayout()
        self.low_power = QtWidgets.QCheckBox("low-power (sleepy node)")
        self.low_power.setChecked(bool(self.controller.settings.get("low_power")))
        row.addWidget(self.low_power)
        row.addWidget(QtWidgets.QLabel("Max bandwidth:"))
        self.bw_box = QtWidgets.QComboBox()
        self.bw_box.addItems(["low", "medium", "high"])
        self.bw_box.setCurrentText(self.controller.settings.get("max_bandwidth", "medium"))
        row.addWidget(self.bw_box)
        row.addWidget(QtWidgets.QLabel("Announce every (s):"))
        self.ann_spin = QtWidgets.QDoubleSpinBox()
        self.ann_spin.setRange(10, 86400)
        self.ann_spin.setValue(float(self.controller.settings.get("announce_interval", 120)))
        row.addWidget(self.ann_spin)
        nl.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        self.transport_cb = QtWidgets.QCheckBox(
            "relay mode (enable RNS transport — mesh other clients through this node)")
        self.transport_cb.setChecked(bool(self.controller.settings.get("enable_transport", False)))
        self.transport_cb.setToolTip(
            "When on, this node forwards announces between its interfaces so\n"
            "rnet clients peering through it discover each other — equivalent\n"
            "to running a separate rnsd. Off = plain client. Requires restart.")
        row.addWidget(self.transport_cb)
        row.addStretch(1)
        nl.addLayout(row)
        root.addWidget(node_card)

        # Storage.
        st_card = Card()
        sl = QtWidgets.QVBoxLayout(st_card)
        sl.addWidget(SectionLabel("Storage"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Data dir:"))
        ddir = QtWidgets.QLineEdit(self.controller.datadir)
        ddir.setReadOnly(True)
        row.addWidget(ddir, 1)
        open_btn = QtWidgets.QPushButton("Open")
        open_btn.clicked.connect(lambda: open_path(self.controller.datadir))
        row.addWidget(open_btn)
        sl.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Downloads:"))
        self.dl_dir = QtWidgets.QLineEdit(self.controller.settings.get("download_dir") or
                                           os.path.join(self.controller.datadir, "downloads"))
        row.addWidget(self.dl_dir, 1)
        dl_browse = QtWidgets.QPushButton("Browse…")
        dl_browse.clicked.connect(self._pick_dl)
        row.addWidget(dl_browse)
        sl.addLayout(row)
        root.addWidget(st_card)

        # Actions.
        act_card = Card()
        acl = QtWidgets.QVBoxLayout(act_card)
        acl.addWidget(SectionLabel("Actions"))
        row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Save settings")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        row.addWidget(save_btn)
        announce_btn = QtWidgets.QPushButton("Announce now")
        announce_btn.clicked.connect(lambda: self.controller.announce_now())
        row.addWidget(announce_btn)
        restart_btn = QtWidgets.QPushButton("Restart Reticulum")
        restart_btn.clicked.connect(self._restart)
        row.addWidget(restart_btn)
        autostart_cb = QtWidgets.QCheckBox("auto-start node on launch")
        autostart_cb.setChecked(bool(self.controller.settings.get("autostart", True)))
        autostart_cb.toggled.connect(lambda v: self.controller.settings.set("autostart", v))
        row.addWidget(autostart_cb)
        row.addStretch(1)
        acl.addLayout(row)
        root.addWidget(act_card)

        # About.
        ab_card = Card()
        abl = QtWidgets.QVBoxLayout(ab_card)
        abl.addWidget(SectionLabel("About"))
        try:
            from rnet.__version__ import __version__
        except Exception:
            __version__ = "?"
        abl.addWidget(QtWidgets.QLabel(f"RNet {__version__} — The Reticulum Internet"))
        root.addWidget(ab_card)
        root.addStretch(1)

    def _pick_dl(self) -> None:
        QtWidgets, _, _ = qt()
        d = QtWidgets.QFileDialog.getExistingDirectory(self.widget, "Download folder", self.dl_dir.text())
        if d:
            self.dl_dir.setText(d)

    def _save(self) -> None:
        s = self.controller.settings
        s.set("theme", self.theme_box.currentText())
        # Node name: rename the default identity (keeps keys/dest) so the
        # chosen name is what peers see in announces. Applies on next start.
        new_name = self.name_edit.text().strip()
        name_changed = False
        if new_name and new_name != self.controller.default_identity_name():
            try:
                self.controller.set_node_name(new_name)
                name_changed = True
            except Exception as exc:
                warn(self.widget, "rename failed", str(exc))
        default = self.id_box.currentText().strip()
        if default:
            try:
                self.controller.set_default_identity(default)
            except Exception:
                pass
            s.set("default_identity", default)
        caps = [c for c, cb in self.cap_checks.items() if cb.isChecked()]
        s.set("capabilities", caps)
        s.set("low_power", self.low_power.isChecked())
        s.set("max_bandwidth", self.bw_box.currentText())
        s.set("announce_interval", float(self.ann_spin.value()))
        transport_changed = self.transport_cb.isChecked() != bool(s.get("enable_transport", False))
        s.set("enable_transport", self.transport_cb.isChecked())
        s.set("download_dir", self.dl_dir.text().strip())
        # Apply theme live.
        try:
            from rnet.gui import theme
            app = QtWidgets.QApplication.instance()
            theme.apply_theme(app, self.theme_box.currentText())
            if self.bridge is not None:
                self.bridge.theme_changed.emit(self.theme_box.currentText())
        except Exception:
            pass
        msg = "settings saved — restart Reticulum to apply node name" if name_changed else "settings saved"
        if transport_changed:
            msg = "settings saved — restart Reticulum to apply relay mode"
        Toast.show_in(self.widget.window().statusBar(), msg, 2500)

    def _restart(self) -> None:
        self.controller.restart_node(
            on_done=lambda _r: Toast.show_in(self.widget.window().statusBar(), "reticulum restarted", 3000),
            on_error=lambda e: warn(self.widget, "restart failed", str(e)),
        )

    def on_node_started(self) -> None:
        pass

    def refresh(self) -> None:
        pass