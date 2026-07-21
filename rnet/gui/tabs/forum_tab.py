"""Forum tab: post / recent / thread using the reference ForumApp."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt
from rnet.gui.workers import offload


class ForumTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Author:"))
        self.author_box = QtWidgets.QComboBox()
        self.author_box.setEditable(True)
        row.addWidget(self.author_box, 1)
        row.addWidget(QtWidgets.QLabel("Board:"))
        self.board_field = QtWidgets.QLineEdit("forum")
        row.addWidget(self.board_field)
        v.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        self.post_field = QtWidgets.QLineEdit()
        self.post_field.setPlaceholderText("thread text")
        row.addWidget(self.post_field, 1)
        self.post_btn = QtWidgets.QPushButton("Post")
        row.addWidget(self.post_btn)
        v.addLayout(row)

        v.addWidget(QtWidgets.QLabel("Recent threads:"))
        self.recent = QtWidgets.QListWidget()
        v.addWidget(self.recent, 1)

        row = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.thread_btn = QtWidgets.QPushButton("Show thread")
        row.addWidget(self.refresh_btn)
        row.addWidget(self.thread_btn)
        v.addLayout(row)
        self.thread_out = QtWidgets.QPlainTextEdit()
        self.thread_out.setReadOnly(True)
        v.addWidget(self.thread_out, 1)

        self.post_btn.clicked.connect(self._on_post)
        self.refresh_btn.clicked.connect(self._refresh)
        self.thread_btn.clicked.connect(self._on_thread)
        self._refresh_authors()

    def _refresh_authors(self) -> None:
        QtWidgets, _, _ = qt()
        self.author_box.clear()
        for r in self.controller.list_own_identities():
            self.author_box.addItem(r["name"])

    def _forum(self):
        sdk = self.controller.sdk
        if sdk is None:
            return None
        from rnet.apps import ForumApp
        board = self.board_field.text().strip() or "forum"
        f = ForumApp(community_dest_hash="", name=board)
        f.sdk = sdk
        f.on_start()
        return f

    def _on_post(self) -> None:
        QtWidgets, _, _ = qt()
        forum = self._forum()
        if forum is None:
            QtWidgets.QMessageBox.warning(self.widget, "forum", "start the node first")
            return
        name = self.author_box.currentText().strip()
        ident = self.controller.load_identity(name)
        text = self.post_field.text()
        if ident is None or not text:
            return
        from rnet.identity import fingerprint
        self.controller.idm.store.upsert_known(fingerprint(ident).hex(), fingerprint(ident),
                                               ident.get_public_key(), name, True)

        def work():
            return forum.post(ident, text)

        def on_done(r):
            if isinstance(r, Exception):
                QtWidgets.QMessageBox.warning(self.widget, "post failed", str(r))
            else:
                self.post_field.clear()
                self._refresh()

        offload(work, on_done=on_done)

    def _refresh(self) -> None:
        QtWidgets, _, _ = qt()
        self.recent.clear()
        forum = self._forum()
        if forum is None:
            self.recent.addItem("(start the node first)")
            return

        def work():
            return forum.recent()

        def on_done(r):
            if isinstance(r, Exception) or not r:
                return
            for p in r:
                self.recent.addItem(QtWidgets.QListWidgetItem(
                    f"{p['hash'][:12]}  {p['author'][:8]}…  {p['body']}"))

        offload(work, on_done=on_done)

    def _on_thread(self) -> None:
        QtWidgets, _, _ = qt()
        item = self.recent.currentItem()
        if item is None:
            return
        h = item.text().split("  ")[0]
        forum = self._forum()
        if forum is None:
            return
        self.thread_out.clear()

        def work():
            return forum.thread(bytes.fromhex(h))

        def on_done(r):
            if isinstance(r, Exception):
                self.thread_out.appendPlainText(f"failed: {r}")
                return
            for p in r:
                indent = "    " if p.reply_to else ""
                self.thread_out.appendPlainText(f"{indent}{p.ts}  {p.author[:8]}…  {p.body}")

        offload(work, on_done=on_done)

    def on_node_started(self) -> None:
        self._refresh_authors()
        self._refresh()