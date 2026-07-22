"""Contacts tab: your identities + known peers (address book)."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, SearchField, CopyLabel, confirm, warn


class ContactsTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # -- Your identities ---------------------------------------------
        own = Card()
        ol = QtWidgets.QVBoxLayout(own)
        ol.addWidget(SectionLabel("Your identities"))
        row = QtWidgets.QHBoxLayout()
        self.name_field = QtWidgets.QLineEdit()
        self.name_field.setPlaceholderText("new identity name")
        row.addWidget(self.name_field, 1)
        self.node_role = QtWidgets.QCheckBox("node role")
        self.node_role.setChecked(True)
        row.addWidget(self.node_role)
        self.create_btn = QtWidgets.QPushButton("Create")
        self.create_btn.setObjectName("primary")
        self.create_btn.clicked.connect(self._on_create)
        row.addWidget(self.create_btn)
        ol.addLayout(row)
        self.own_table = QtWidgets.QTableWidget(0, 5)
        self.own_table.setHorizontalHeaderLabels(["", "name", "fingerprint", "role", ""])
        self.own_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.own_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        ol.addWidget(self.own_table, 1)
        root.addWidget(own, 1)

        # -- Known peers -------------------------------------------------
        kp = Card()
        kl = QtWidgets.QVBoxLayout(kp)
        krow = QtWidgets.QHBoxLayout()
        krow.addWidget(SectionLabel("Known peers"))
        krow.addStretch(1)
        self.search = SearchField("Search peers…")
        self.search.textChanged.connect(self._filter_known)
        krow.addWidget(self.search, 2)
        self.show_blocked = QtWidgets.QCheckBox("show blocked")
        self.show_blocked.toggled.connect(self._refresh_known)
        krow.addWidget(self.show_blocked)
        kl.addLayout(krow)
        self.known_table = QtWidgets.QTableWidget(0, 6)
        self.known_table.setHorizontalHeaderLabels(["name", "fingerprint", "trusted", "blocked", "first seen", ""])
        self.known_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.known_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        kl.addWidget(self.known_table, 1)
        root.addWidget(kp, 1)

        self._refresh()
        self._refresh_known()

    # -- own identities -------------------------------------------------
    def _refresh(self) -> None:
        QtWidgets, QtCore, _ = qt()
        rows = self.controller.list_own_identities()
        default = self.controller.default_identity_name()
        self.own_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            is_def = (r["name"] == default)
            star = QtWidgets.QLabel("★" if is_def else "☆")
            star.setAlignment(QtCore.Qt.AlignCenter)
            self.own_table.setCellWidget(i, 0, star)
            self.own_table.setItem(i, 1, QtWidgets.QTableWidgetItem(r["name"]))
            self.own_table.setItem(i, 2, QtWidgets.QTableWidgetItem(r["dest_hash"]))
            self.own_table.setItem(i, 3, QtWidgets.QTableWidgetItem("node" if r["is_node"] else "user"))
            actions = QtWidgets.QWidget()
            al = QtWidgets.QHBoxLayout(actions)
            al.setContentsMargins(2, 2, 2, 2)
            setdef = QtWidgets.QPushButton("default")
            setdef.clicked.connect(lambda _, n=r["name"]: self._set_default(n))
            ren = QtWidgets.QPushButton("rename")
            ren.clicked.connect(lambda _, n=r["name"]: self._rename(n))
            dele = QtWidgets.QPushButton("delete")
            dele.clicked.connect(lambda _, n=r["name"]: self._delete_own(n))
            for b in (setdef, ren, dele):
                al.addWidget(b)
            self.own_table.setCellWidget(i, 4, actions)
        self.own_table.resizeColumnsToContents()

    def _on_create(self) -> None:
        name = self.name_field.text().strip()
        if not name:
            return
        try:
            self.controller.create_identity(name, is_node=self.node_role.isChecked())
            self.name_field.clear()
            self._refresh()
        except Exception as exc:
            warn(self.widget, "create failed", str(exc))

    def _set_default(self, name: str) -> None:
        try:
            self.controller.set_default_identity(name)
            self._refresh()
        except Exception as exc:
            warn(self.widget, "set default failed", str(exc))

    def _rename(self, name: str) -> None:
        QtWidgets, _, _ = qt()
        new, ok = QtWidgets.QInputDialog.getText(self.widget, "Rename identity",
                                                  "new name:", text=name)
        if ok and new and new != name:
            try:
                self.controller.rename_identity(name, new)
                self._refresh()
            except Exception as exc:
                warn(self.widget, "rename failed", str(exc))

    def _delete_own(self, name: str) -> None:
        if not confirm(self.widget, "Delete identity",
                       f"Permanently delete identity '{name}' and its keyfile?"):
            return
        try:
            self.controller.delete_identity(name)
            self._refresh()
        except Exception as exc:
            warn(self.widget, "delete failed", str(exc))

    # -- known peers ----------------------------------------------------
    def _refresh_known(self) -> None:
        QtWidgets, _, _ = qt()
        rows = self.controller.list_known(include_blocked=self.show_blocked.isChecked())
        self.known_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = r["display"] or r["name"] or r["dest_hash"][:10]
            self.known_table.setItem(i, 0, QtWidgets.QTableWidgetItem(name))
            self.known_table.setItem(i, 1, QtWidgets.QTableWidgetItem(r["dest_hash"]))
            self.known_table.setItem(i, 2, QtWidgets.QTableWidgetItem("yes" if r["trusted"] else "—"))
            self.known_table.setItem(i, 3, QtWidgets.QTableWidgetItem("yes" if r["blocked"] else "—"))
            from datetime import datetime
            fs = datetime.fromtimestamp(r["first_seen"]).strftime("%Y-%m-%d") if r["first_seen"] else ""
            self.known_table.setItem(i, 4, QtWidgets.QTableWidgetItem(fs))
            actions = QtWidgets.QWidget()
            al = QtWidgets.QHBoxLayout(actions)
            al.setContentsMargins(2, 2, 2, 2)
            trust = QtWidgets.QPushButton("trust" if not r["trusted"] else "untrust")
            trust.clicked.connect(lambda _, d=r["dest_hash"], t=not r["trusted"]: self._trust(d, t))
            block = QtWidgets.QPushButton("block" if not r["blocked"] else "unblock")
            block.clicked.connect(lambda _, d=r["dest_hash"], b=not r["blocked"]: self._block(d, b))
            rn = QtWidgets.QPushButton("rename")
            rn.clicked.connect(lambda _, d=r["dest_hash"]: self._rename_known(d))
            msg = QtWidgets.QPushButton("message")
            msg.clicked.connect(lambda _, d=r["dest_hash"]: self._message(d))
            for b in (trust, block, rn, msg):
                al.addWidget(b)
            self.known_table.setCellWidget(i, 5, actions)
        self.known_table.resizeColumnsToContents()

    def _filter_known(self, needle: str) -> None:
        needle = (needle or "").lower()
        for i in range(self.known_table.rowCount()):
            name = self.known_table.item(i, 0).text().lower()
            dh = self.known_table.item(i, 1).text().lower()
            self.known_table.setRowHidden(i, bool(needle) and (needle not in name and needle not in dh))

    def _trust(self, dest_hash: str, trusted: bool) -> None:
        self.controller.set_trusted(dest_hash, trusted)
        self._refresh_known()

    def _block(self, dest_hash: str, blocked: bool) -> None:
        self.controller.set_blocked(dest_hash, blocked)
        self._refresh_known()

    def _rename_known(self, dest_hash: str) -> None:
        QtWidgets, _, _ = qt()
        row = self.controller.idm.store.get_known(dest_hash)
        cur = row["display"] or row["name"] or ""
        new, ok = QtWidgets.QInputDialog.getText(self.widget, "Rename contact", "display name:", text=cur)
        if ok and new is not None:
            self.controller.set_display(dest_hash, new)
            self._refresh_known()

    def _message(self, dest_hash: str) -> None:
        # Switch to Conversations tab via the main window.
        win = self.widget.window()
        try:
            sb = win.findChild(QtWidgets.QListWidget, "sidebar")
            if sb is not None:
                for i in range(sb.count()):
                    if sb.item(i).text() == "Conversations":
                        sb.setCurrentRow(i)
                        break
        except Exception:
            pass

    # -- lifecycle ------------------------------------------------------
    def on_node_started(self) -> None:
        self._refresh_known()

    def refresh(self) -> None:
        self._refresh()
        self._refresh_known()

    def focus_search(self) -> None:
        self.search.setFocus()