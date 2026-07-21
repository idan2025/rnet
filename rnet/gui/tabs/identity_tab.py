"""Identities tab: create/list/show owned identities."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


class IdentityTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        # Create
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("New identity name:"))
        self.name_field = QtWidgets.QLineEdit()
        row.addWidget(self.name_field, 1)
        self.node_role = QtWidgets.QCheckBox("node role")
        self.node_role.setChecked(True)
        row.addWidget(self.node_role)
        self.create_btn = QtWidgets.QPushButton("Create")
        row.addWidget(self.create_btn)
        v.addLayout(row)

        # List
        v.addWidget(QtWidgets.QLabel("Your identities:"))
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["name", "fingerprint", "role"])
        v.addWidget(self.table, 1)

        self.create_btn.clicked.connect(self._on_create)
        self._refresh()

    def _refresh(self) -> None:
        QtWidgets, _, _ = qt()
        rows = self.controller.list_own_identities()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QtWidgets.QTableWidgetItem(r["name"]))
            self.table.setItem(i, 1, QtWidgets.QTableWidgetItem(r["dest_hash"]))
            self.table.setItem(i, 2, QtWidgets.QTableWidgetItem("node" if r["is_node"] else "user"))

    def _on_create(self) -> None:
        name = self.name_field.text().strip()
        if not name:
            return
        try:
            self.controller.create_identity(name, is_node=self.node_role.isChecked())
            self.name_field.clear()
            self._refresh()
        except Exception as exc:
            QtWidgets, _, _ = qt()
            QtWidgets.QMessageBox.warning(self.widget, "create failed", str(exc))