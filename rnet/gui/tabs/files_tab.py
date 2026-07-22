"""Files tab: share, fetch, shared-library index, download to a chosen dir."""
from __future__ import annotations

import os

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, Toast, confirm, warn
from rnet.gui.workers import offload


class FilesTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        self.widget.setAcceptDrops(True)
        self.widget.dragEnterEvent = self._drag_enter
        self.widget.dropEvent = self._drop
        root = QtWidgets.QVBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Share row.
        share = Card()
        sl = QtWidgets.QVBoxLayout(share)
        sl.addWidget(SectionLabel("Share a file (drag-drop anywhere here, or browse)"))
        row = QtWidgets.QHBoxLayout()
        self.share_path = QtWidgets.QLineEdit()
        self.share_path.setPlaceholderText("/path/to/file  (or drag a file onto this tab)")
        row.addWidget(self.share_path, 1)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse(self.share_path))
        row.addWidget(browse)
        self.share_btn = QtWidgets.QPushButton("Share")
        self.share_btn.setObjectName("primary")
        self.share_btn.clicked.connect(self._on_share)
        row.addWidget(self.share_btn)
        sl.addLayout(row)
        self.share_result = QtWidgets.QLabel("")
        self.share_result.setStyleSheet("color: palette(placeholder-text);")
        sl.addWidget(self.share_result)
        root.addWidget(share)

        # Get row.
        get = Card()
        gl = QtWidgets.QVBoxLayout(get)
        gl.addWidget(SectionLabel("Fetch a file by manifest hash"))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("hash:"))
        self.get_hash = QtWidgets.QLineEdit()
        self.get_hash.setPlaceholderText("hex manifest hash")
        row.addWidget(self.get_hash, 1)
        self.get_btn = QtWidgets.QPushButton("Fetch")
        self.get_btn.clicked.connect(self._on_get)
        row.addWidget(self.get_btn)
        gl.addLayout(row)
        drow = QtWidgets.QHBoxLayout()
        drow.addWidget(QtWidgets.QLabel("download to:"))
        self.dl_dir = QtWidgets.QLineEdit(self._dl_dir())
        drow.addWidget(self.dl_dir, 1)
        dl_browse = QtWidgets.QPushButton("Browse…")
        dl_browse.clicked.connect(self._pick_dl_dir)
        drow.addWidget(dl_browse)
        gl.addLayout(drow)
        self.get_result = QtWidgets.QLabel("")
        self.get_result.setStyleSheet("color: palette(placeholder-text);")
        gl.addWidget(self.get_result)
        root.addWidget(get)

        # Shared library index.
        lib = Card()
        ll = QtWidgets.QVBoxLayout(lib)
        lrow = QtWidgets.QHBoxLayout()
        lrow.addWidget(SectionLabel("Shared library"))
        lrow.addStretch(1)
        self.stats_label = QtWidgets.QLabel("CAS: —")
        lrow.addWidget(self.stats_label)
        ll.addLayout(lrow)
        self.lib_table = QtWidgets.QTableWidget(0, 3)
        self.lib_table.setHorizontalHeaderLabels(["name", "hash", "size"])
        self.lib_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.lib_table.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
        act_copy = QtGui.QAction("Copy hash", self.lib_table)
        act_copy.triggered.connect(self._copy_lib_hash)
        self.lib_table.addAction(act_copy)
        ll.addWidget(self.lib_table, 1)
        root.addWidget(lib, 1)

        self._refresh_lib()
        self._refresh_stats()

    # -- paths + stores -------------------------------------------------
    def _dl_dir(self) -> str:
        d = self.controller.settings.get("download_dir")
        if d:
            return d
        return os.path.join(self.controller.datadir, "downloads")

    def _pick_dl_dir(self) -> None:
        QtWidgets, _, _ = qt()
        d = QtWidgets.QFileDialog.getExistingDirectory(self.widget, "Download folder", self.dl_dir.text())
        if d:
            self.dl_dir.setText(d)
            self.controller.settings.set("download_dir", d)

    def _stores(self):
        sdk = self.controller.sdk
        if sdk is not None:
            return sdk.content_store, sdk.manifest_store
        from rnet.storage import ContentStore, ManifestStore
        return (ContentStore(self.controller.db, os.path.join(self.controller.datadir, "cas")),
                ManifestStore(self.controller.db))

    # -- share ---------------------------------------------------------
    def _browse(self, field) -> None:
        QtWidgets, _, _ = qt()
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self.widget, "Choose file")
        if f:
            field.setText(f)

    def _drag_enter(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def _drop(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p and os.path.isfile(p):
                self.share_path.setText(p)
                self._on_share()
                break

    def _on_share(self) -> None:
        path = self.share_path.text().strip()
        if not path or not os.path.isfile(path):
            self.share_result.setText("pick an existing file first")
            return
        store, ms = self._stores()

        def work():
            from rnet.storage import build_manifest
            with open(path, "rb") as f:
                data = f.read()
            m = build_manifest(data, store, name=os.path.basename(path))
            return ms.put(m), os.path.basename(path), len(data)

        def on_done(result):
            if isinstance(result, Exception):
                self.share_result.setText(f"failed: {result}")
            else:
                h, name, size = result
                self.share_result.setText(f"shared {name} ({size} bytes) → {h.hex()}")
                self._refresh_lib()
                self._refresh_stats()

        offload(work, on_done=on_done)

    # -- get -----------------------------------------------------------
    def _on_get(self) -> None:
        h = self.get_hash.text().strip()
        if not h:
            return
        store, ms = self._stores()
        out_dir = self.dl_dir.text().strip() or self._dl_dir()
        os.makedirs(out_dir, exist_ok=True)

        def work():
            from rnet.storage import assemble
            m = ms.get(bytes.fromhex(h))
            if m is None:
                raise KeyError("unknown manifest")
            data = assemble(m, store)
            name = m.name or "rnet-output"
            out = os.path.join(out_dir, name)
            with open(out, "wb") as f:
                f.write(data)
            return out, len(data)

        def on_done(result):
            if isinstance(result, Exception):
                self.get_result.setText(f"failed: {result}")
            else:
                self.get_result.setText(f"retrieved {result[1]} bytes → {result[0]}")

        offload(work, on_done=on_done)

    # -- library index -------------------------------------------------
    def _refresh_lib(self) -> None:
        QtWidgets, _, _ = qt()
        try:
            rows = self.controller.db.query(
                "SELECT name, hash, size, created_at FROM cas_manifests ORDER BY created_at DESC"
            )
        except Exception:
            rows = []
        self.lib_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self.lib_table.setItem(i, 0, QtWidgets.QTableWidgetItem(r["name"] or "(unnamed)"))
            self.lib_table.setItem(i, 1, QtWidgets.QTableWidgetItem(bytes(r["hash"]).hex()))
            self.lib_table.setItem(i, 2, QtWidgets.QTableWidgetItem(self._human(int(r["size"]))))
        self.lib_table.resizeColumnsToContents()

    def _copy_lib_hash(self) -> None:
        r = self.lib_table.currentRow()
        if r >= 0:
            QtWidgets.QApplication.clipboard().setText(self.lib_table.item(r, 1).text())

    def _refresh_stats(self) -> None:
        try:
            store, _ = self._stores()
            s = store.stats()
            self.stats_label.setText(f"CAS: {s['blocks']} blocks, {self._human(s['bytes'])}")
        except Exception:
            self.stats_label.setText("CAS: —")

    @staticmethod
    def _human(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n} {unit}"
            n //= 1024
        return f"{n} TB"

    def on_node_started(self) -> None:
        self._refresh_lib()
        self._refresh_stats()

    def refresh(self) -> None:
        self._refresh_lib()
        self._refresh_stats()