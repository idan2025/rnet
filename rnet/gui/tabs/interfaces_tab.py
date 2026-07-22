"""Interfaces tab: list + add/edit/remove RNS interfaces (RNode, TCP, UDP, ...)."""
from __future__ import annotations

from typing import Optional

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, StatusDot, confirm, warn
from rnet.gui.rns_config import SPEC_TYPES


# Per-type editable fields shown in the add form. (key, label, placeholder)
TYPE_FIELDS = {
    "AutoInterface": [("network", "network", "auto"), ("device", "device", ""),
                       ("discovery_port", "discovery port", "29713")],
    "TCPClient": [("target_host", "target host", "host or .rns name"),
                  ("target_port", "target port", "4242")],
    "TCPHost": [("listen_ip", "listen ip", "0.0.0.0"),
                ("listen_port", "listen port", "4242")],
    "UDP": [("listen_ip", "listen ip", "0.0.0.0"),
            ("listen_port", "listen port", "4242")],
    "RNode": [("device", "device", "/dev/ttyUSB0"),
              ("baudrate", "baudrate", "115200"),
              ("frequency", "frequency MHz", "868"),
              ("bandwidth", "bandwidth kHz", "125"),
              ("txpower", "tx power dBm", "17")],
    "SerialKISS": [("device", "device", "/dev/ttyUSB0"),
                   ("baudrate", "baudrate", "115200")],
    "AX25KISS": [("device", "device", "/dev/ttyUSB0"),
                 ("baudrate", "baudrate", "9600"),
                 ("callsign", "callsign", "N0CALL")],
    "I2P": [("peers", "peers", "host:port")],
    "RNSLocal": [],
}


class InterfacesTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # List.
        lst = Card()
        ll = QtWidgets.QVBoxLayout(lst)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(SectionLabel("Active interfaces"))
        lrow.addStretch(1)
        ref_btn = QtWidgets.QPushButton("Refresh")
        ref_btn.clicked.connect(self._refresh)
        lrow.addWidget(ref_btn)
        edit_btn = QtWidgets.QPushButton("Edit…")
        edit_btn.clicked.connect(self._edit_selected)
        lrow.addWidget(edit_btn)
        rm_btn = QtWidgets.QPushButton("Remove…")
        rm_btn.clicked.connect(self._remove_selected)
        lrow.addWidget(rm_btn)
        ll.addLayout(lrow)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "name", "type", "details"])
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        # Keep the right-click context menu too (Remove).
        self.table.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
        act_remove = QtGui.QAction("Remove interface", self.table)
        act_remove.triggered.connect(self._remove_selected)
        self.table.addAction(act_remove)
        ll.addWidget(self.table, 1)
        root.addWidget(lst, 1)

        # Add / edit form.
        add = Card()
        al = QtWidgets.QVBoxLayout(add)
        self.form_title = SectionLabel("Add interface")
        al.addWidget(self.form_title)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("name:"))
        self.iface_name = QtWidgets.QLineEdit()
        self.iface_name.setPlaceholderText("e.g. RNode-LoRa")
        row.addWidget(self.iface_name, 1)
        row.addWidget(QtWidgets.QLabel("type:"))
        self.type_box = QtWidgets.QComboBox()
        for t in SPEC_TYPES:
            self.type_box.addItem(t)
        self.type_box.currentTextChanged.connect(self._build_type_fields)
        row.addWidget(self.type_box)
        al.addLayout(row)
        self.fields_holder = QtWidgets.QWidget()
        self.fields_layout = QtWidgets.QFormLayout(self.fields_holder)
        al.addWidget(self.fields_holder)
        btn_row = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._on_submit)
        btn_row.addWidget(self.add_btn)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_edit)
        self.cancel_btn.setVisible(False)
        btn_row.addStretch(1)
        btn_row.addWidget(self.cancel_btn)
        al.addLayout(btn_row)
        root.addWidget(add)

        self._field_widgets = {}
        self._editing: Optional[str] = None
        self._build_type_fields(self.type_box.currentText())
        self._refresh()

        # Live updates: interface online/offline state, bitrate and rx/tx
        # counters change without any user action, so poll on a timer and
        # refresh immediately when an interface is added/edited/removed.
        if bridge is not None:
            bridge.interface_changed.connect(lambda _e: self._refresh())
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(3000)

    def _build_type_fields(self, type_name: str) -> None:
        QtWidgets, _, _ = qt()
        # Clear existing.
        while self.fields_layout.rowCount():
            self.fields_layout.removeRow(0)
        self._field_widgets = {}
        for key, label, ph in TYPE_FIELDS.get(type_name, []):
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText(ph)
            self.fields_layout.addRow(label, edit)
            self._field_widgets[key] = edit

    def _refresh(self) -> None:
        QtWidgets, _, _ = qt()
        ifaces = self.controller.list_interfaces()
        self.table.setRowCount(len(ifaces))
        for i, ifc in enumerate(ifaces):
            online = ifc.get("online") if "online" in ifc else (ifc.get("enabled", True))
            dot = StatusDot("green" if online else "grey")
            self.table.setCellWidget(i, 0, dot)
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(ifc.get("name", ""))))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(ifc.get("type", ""))))
            details = self._details(ifc)
            self.table.setItem(i, 3, QtWidgets.QTableWidgetItem(details))
        self.table.resizeColumnsToContents()

    @staticmethod
    def _details(ifc: dict) -> str:
        parts = []
        for k in ("target_host", "target_port", "listen_ip", "listen_port",
                  "device", "baudrate", "frequency", "host", "port", "network"):
            if ifc.get(k):
                parts.append(f"{k}={ifc[k]}")
        if ifc.get("rx_bytes") is not None:
            parts.append(f"rx={ifc['rx_bytes']}")
        if ifc.get("tx_bytes") is not None:
            parts.append(f"tx={ifc['tx_bytes']}")
        return "  ".join(parts)

    def _spec_from_form(self) -> dict:
        spec = {"type": self.type_box.currentText(), "interface_enabled": True}
        for key, edit in self._field_widgets.items():
            val = edit.text().strip()
            if val:
                spec[key] = val
        return spec

    def _on_submit(self) -> None:
        QtWidgets, _, _ = qt()
        name = self.iface_name.text().strip()
        if not name:
            warn(self.widget, "interface", "give the interface a name")
            return
        spec = self._spec_from_form()
        if self._editing is not None:
            target = self._editing
            self._reset_form()
            self.controller.update_interface(
                target, spec,
                on_done=lambda _r: self._refresh(),
                on_error=lambda e: warn(self.widget, "edit failed", str(e)),
            )
        else:
            self.iface_name.clear()
            self.controller.add_interface(
                name, spec,
                on_done=lambda _r: self._refresh(),
                on_error=lambda e: warn(self.widget, "add failed", str(e)),
            )

    def _edit_selected(self) -> None:
        QtWidgets, _, _ = qt()
        r = self.table.currentRow()
        if r < 0:
            warn(self.widget, "edit interface", "select an interface to edit")
            return
        name = self.table.item(r, 1).text()
        ifc = self.controller.get_interface(name)
        if ifc is None:
            warn(self.widget, "edit interface", f"could not read config for '{name}'")
            return
        # Map the RNS interface type string back to our spec type key.
        rns_type = ifc.get("type", "")
        spec_key = None
        for k, v in SPEC_TYPES.items():
            if v["rns_type"] == rns_type:
                spec_key = k
                break
        if spec_key is None:
            warn(self.widget, "edit interface",
                 f"unsupported interface type '{rns_type}'")
            return
        self._editing = name
        self.form_title.setText(f"Edit interface — {name}")
        self.iface_name.setText(name)
        self.iface_name.setReadOnly(True)
        self.type_box.setCurrentText(spec_key)
        # _build_type_fields fired on setCurrentText; now fill values. Clear
        # first so a re-edit of the same type doesn't keep stale text.
        options = ifc.get("options", {})
        for key, edit in self._field_widgets.items():
            edit.clear()
            if key in options:
                edit.setText(str(options[key]))
        self.add_btn.setText("Save")
        self.cancel_btn.setVisible(True)

    def _cancel_edit(self) -> None:
        self._reset_form()

    def _reset_form(self) -> None:
        QtWidgets, _, _ = qt()
        self._editing = None
        self.form_title.setText("Add interface")
        self.iface_name.clear()
        self.iface_name.setReadOnly(False)
        self.add_btn.setText("Add")
        self.cancel_btn.setVisible(False)
        for edit in self._field_widgets.values():
            edit.clear()

    def _remove_selected(self) -> None:
        QtWidgets, _, _ = qt()
        r = self.table.currentRow()
        if r < 0:
            warn(self.widget, "remove interface", "select an interface to remove")
            return
        name = self.table.item(r, 1).text()
        if not confirm(self.widget, "Remove interface",
                       f"Remove '{name}'?"):
            return
        # If we were editing this one, drop the half-edited form.
        if self._editing == name:
            self._reset_form()
        self.controller.remove_interface(name,
                                           on_done=lambda _r: self._refresh(),
                                           on_error=lambda e: warn(self.widget, "remove failed", str(e)))

    def on_node_started(self) -> None:
        self._refresh()

    def refresh(self) -> None:
        self._refresh()