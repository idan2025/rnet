"""Files tab: share a file (content-addressed) and get a file by manifest hash."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt
from rnet.gui.workers import offload


class FilesTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        # Share
        g = QtWidgets.QGroupBox("Share a file")
        gl = QtWidgets.QVBoxLayout(g)
        row = QtWidgets.QHBoxLayout()
        self.share_path = QtWidgets.QLineEdit()
        self.share_path.setPlaceholderText("/path/to/file")
        row.addWidget(self.share_path, 1)
        b = QtWidgets.QPushButton("Browse…")
        row.addWidget(b)
        self.share_btn = QtWidgets.QPushButton("Share")
        row.addWidget(self.share_btn)
        gl.addLayout(row)
        self.share_result = QtWidgets.QLabel("(manifest hash will appear here)")
        gl.addWidget(self.share_result)
        v.addWidget(g)

        # Get
        g = QtWidgets.QGroupBox("Get a file")
        gl = QtWidgets.QVBoxLayout(g)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Manifest hash:"))
        self.get_hash = QtWidgets.QLineEdit()
        self.get_hash.setPlaceholderText("hex")
        row.addWidget(self.get_hash, 1)
        b = QtWidgets.QPushButton("Browse…")
        row.addWidget(b)
        self.get_btn = QtWidgets.QPushButton("Get")
        row.addWidget(self.get_btn)
        gl.addLayout(row)
        self.get_result = QtWidgets.QLabel("(result)")
        gl.addWidget(self.get_result)
        v.addWidget(g)

        # CAS stats
        self.stats_label = QtWidgets.QLabel("CAS: —")
        v.addWidget(self.stats_label)
        v.addStretch(1)

        b.clicked.connect(lambda: self._browse(self.share_path))
        self.share_btn.clicked.connect(self._on_share)
        self.get_btn.clicked.connect(self._on_get)
        self._refresh_stats()

    def _browse(self, field) -> None:
        QtWidgets, _, _ = qt()
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self.widget, "Choose file")
        if f:
            field.setText(f)

    def _stores(self):
        sdk = self.controller.sdk
        if sdk is not None:
            return sdk.content_store, sdk.manifest_store
        # Pre-start: build ephemeral stores from the controller's db.
        from rnet.storage import ContentStore, ManifestStore
        import os
        return (ContentStore(self.controller.db, os.path.join(self.controller.datadir, "cas")),
                ManifestStore(self.controller.db))

    def _refresh_stats(self) -> None:
        try:
            store, _ = self._stores()
            s = store.stats()
            self.stats_label.setText(f"CAS: {s['blocks']} blocks, {s['bytes']} bytes")
        except Exception:
            self.stats_label.setText("CAS: —")

    def _on_share(self) -> None:
        QtWidgets, _, _ = qt()
        path = self.share_path.text().strip()
        if not path:
            return
        store, ms = self._stores()

        def work():
            from rnet.storage import build_manifest
            import os
            with open(path, "rb") as f:
                data = f.read()
            m = build_manifest(data, store, name=os.path.basename(path))
            return ms.put(m)

        def on_done(result):
            if isinstance(result, Exception):
                self.share_result.setText(f"failed: {result}")
            else:
                self.share_result.setText(f"manifest hash: {result.hex()}")
            self._refresh_stats()

        offload(work, on_done=on_done)

    def _on_get(self) -> None:
        QtWidgets, _, _ = qt()
        h = self.get_hash.text().strip()
        if not h:
            return
        store, ms = self._stores()

        def work():
            from rnet.storage import assemble
            m = ms.get(bytes.fromhex(h))
            if m is None:
                raise KeyError("unknown manifest")
            data = assemble(m, store)
            out = m.name or "rnet-output"
            with open(out, "wb") as f:
                f.write(data)
            return out, len(data)

        def on_done(result):
            if isinstance(result, Exception):
                self.get_result.setText(f"failed: {result}")
            else:
                self.get_result.setText(f"retrieved {result[1]} bytes -> {result[0]}")

        offload(work, on_done=on_done)