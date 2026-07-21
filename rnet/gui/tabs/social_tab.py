"""Social tab: post, follow, view feed."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt
from rnet.gui.workers import offload


class SocialTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        # Author selector
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Author:"))
        self.author_box = QtWidgets.QComboBox()
        self.author_box.setEditable(True)
        row.addWidget(self.author_box, 1)
        v.addLayout(row)

        # Compose
        row = QtWidgets.QHBoxLayout()
        self.post_field = QtWidgets.QLineEdit()
        self.post_field.setPlaceholderText("what's happening on your mesh?")
        row.addWidget(self.post_field, 1)
        self.post_btn = QtWidgets.QPushButton("Post")
        row.addWidget(self.post_btn)
        v.addLayout(row)

        # Follow
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Follow fingerprint:"))
        self.follow_field = QtWidgets.QLineEdit()
        row.addWidget(self.follow_field, 1)
        self.follow_btn = QtWidgets.QPushButton("Follow")
        row.addWidget(self.follow_btn)
        v.addLayout(row)

        # Feed
        v.addWidget(QtWidgets.QLabel("Feed:"))
        self.feed = QtWidgets.QListWidget()
        v.addWidget(self.feed, 1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh feed")
        v.addWidget(self.refresh_btn)

        self.post_btn.clicked.connect(self._on_post)
        self.follow_btn.clicked.connect(self._on_follow)
        self.refresh_btn.clicked.connect(self._refresh_feed)
        self._refresh_authors()

    def _refresh_authors(self) -> None:
        QtWidgets, _, _ = qt()
        self.author_box.clear()
        for r in self.controller.list_own_identities():
            self.author_box.addItem(r["name"])

    def _social(self):
        sdk = self.controller.sdk
        return sdk.social if sdk is not None else None

    def _on_post(self) -> None:
        QtWidgets, _, _ = qt()
        social = self._social()
        if social is None:
            QtWidgets.QMessageBox.warning(self.widget, "social", "start the node first")
            return
        name = self.author_box.currentText().strip()
        text = self.post_field.text()
        ident = self.controller.load_identity(name)
        if ident is None:
            return
        from rnet.identity import fingerprint
        self.controller.idm.store.upsert_known(fingerprint(ident).hex(), fingerprint(ident),
                                               ident.get_public_key(), name, True)

        def work():
            social.publish_post(ident, text)
            return True

        def on_done(r):
            if isinstance(r, Exception):
                QtWidgets.QMessageBox.warning(self.widget, "post failed", str(r))
            else:
                self.post_field.clear()
                self._refresh_feed()

        offload(work, on_done=on_done)

    def _on_follow(self) -> None:
        QtWidgets, _, _ = qt()
        social = self._social()
        if social is None:
            return
        name = self.author_box.currentText().strip()
        ident = self.controller.load_identity(name)
        fp = self.follow_field.text().strip()
        if ident is None or not fp:
            return

        def work():
            social.follow(ident, fp)
            return True

        def on_done(r):
            if not isinstance(r, Exception):
                self.follow_field.clear()

        offload(work, on_done=on_done)

    def _refresh_feed(self) -> None:
        QtWidgets, _, _ = qt()
        self.feed.clear()
        social = self._social()
        if social is None:
            self.feed.addItem("(start the node to see your feed)")
            return
        name = self.author_box.currentText().strip()
        row = self.controller.idm.store.get_own_by_name(name)
        if not row:
            return

        def work():
            return social.feed(row["dest_hash"])

        def on_done(r):
            if isinstance(r, Exception) or not r:
                return
            for p in r:
                self.feed.addItem(QtWidgets.QListWidgetItem(
                    f"{p['ts']}  {p['author'][:8]}…  {p['body']}"))

        offload(work, on_done=on_done)

    def on_node_started(self) -> None:
        self._refresh_authors()
        self._refresh_feed()