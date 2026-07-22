"""Social tab: profile, post, follow/unfollow, feed with author names."""
from __future__ import annotations

import time

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, Avatar, Toast, warn
from rnet.gui.workers import offload


def _when(ts: int) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class SocialTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Left: profile + compose + follow.
        left = Card()
        ll = QtWidgets.QVBoxLayout(left)
        ll.addWidget(SectionLabel("Posting as"))
        self.author_box = QtWidgets.QComboBox()
        self.author_box.setEditable(True)
        self.author_box.currentTextChanged.connect(lambda _: self._refresh_following())
        ll.addWidget(self.author_box)
        self.avatar = Avatar("", "?", 40)
        ll.addWidget(self.avatar)
        self.post_field = QtWidgets.QPlainTextEdit()
        self.post_field.setFixedHeight(70)
        self.post_field.setPlaceholderText("what's happening on your mesh?")
        ll.addWidget(self.post_field)
        post_btn = QtWidgets.QPushButton("Post")
        post_btn.setObjectName("primary")
        post_btn.clicked.connect(self._on_post)
        ll.addWidget(post_btn)
        ll.addWidget(SectionLabel("Follow someone"))
        frow = QtWidgets.QHBoxLayout()
        self.follow_field = QtWidgets.QLineEdit()
        self.follow_field.setPlaceholderText("fingerprint hex")
        frow.addWidget(self.follow_field, 1)
        fb = QtWidgets.QPushButton("Follow")
        fb.clicked.connect(self._on_follow)
        frow.addWidget(fb)
        ll.addLayout(frow)
        ll.addWidget(SectionLabel("Following"))
        self.following_list = QtWidgets.QListWidget()
        self.following_list.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
        act_unf = QtGui.QAction("Unfollow", self.following_list)
        act_unf.triggered.connect(self._unfollow_selected)
        self.following_list.addAction(act_unf)
        ll.addWidget(self.following_list, 1)
        root.addWidget(left, 2)

        # Right: feed.
        right = Card()
        rl = QtWidgets.QVBoxLayout(right)
        rrow = QtWidgets.QHBoxLayout()
        rrow.addWidget(SectionLabel("Feed"))
        rrow.addStretch(1)
        refresh = QtWidgets.QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_feed)
        rrow.addWidget(refresh)
        rl.addLayout(rrow)
        self.feed = QtWidgets.QListWidget()
        self.feed.itemDoubleClicked.connect(self._on_feed_item)
        rl.addWidget(self.feed, 1)
        root.addWidget(right, 3)

        self._refresh_authors()
        self._refresh_feed()

    def _refresh_authors(self) -> None:
        QtWidgets, _, _ = qt()
        cur = self.author_box.currentText()
        self.author_box.blockSignals(True)
        self.author_box.clear()
        for r in self.controller.list_own_identities():
            self.author_box.addItem(r["name"])
        default = self.controller.default_identity_name()
        if default:
            idx = max(0, self.author_box.findText(default))
            self.author_box.setCurrentIndex(idx)
        elif cur:
            self.author_box.setEditText(cur)
        self.author_box.blockSignals(False)
        self.avatar.set(self.author_box.currentText() or "?", self.author_box.currentText() or "?")

    def _social(self):
        sdk = self.controller.sdk
        return sdk.social if sdk is not None else None

    def _author_fp_hex(self) -> str:
        name = self.author_box.currentText().strip()
        row = self.controller.idm.store.get_own_by_name(name)
        return row["dest_hash"] if row else ""

    def _resolve(self, fp_hex: str) -> str:
        try:
            row = self.controller.idm.store.get_known_by_fp(bytes.fromhex(fp_hex))
            if row and (row["display"] or row["name"]):
                return row["display"] or row["name"]
        except Exception:
            pass
        return fp_hex[:10] + "…"

    def _on_post(self) -> None:
        QtWidgets, _, _ = qt()
        social = self._social()
        if social is None:
            warn(self.widget, "social", "start the node first")
            return
        name = self.author_box.currentText().strip()
        text = self.post_field.toPlainText().strip()
        ident = self.controller.load_identity(name)
        if ident is None or not text:
            return
        from rnet.identity import fingerprint
        self.controller.idm.store.upsert_known(fingerprint(ident).hex(), fingerprint(ident),
                                               ident.get_public_key(), name, True)

        def work():
            social.publish_post(ident, text)
            return True

        def on_done(r):
            if isinstance(r, Exception):
                warn(self.widget, "post failed", str(r))
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
        fp = self.follow_field.text().strip().lower()
        if ident is None or not fp:
            return

        def work():
            social.follow(ident, fp)
            return True

        def on_done(r):
            if not isinstance(r, Exception):
                self.follow_field.clear()
                self._refresh_following()

        offload(work, on_done=on_done)

    def _refresh_following(self) -> None:
        QtWidgets, _, _ = qt()
        self.following_list.clear()
        social = self._social()
        my = self._author_fp_hex()
        if social is None or not my:
            return
        try:
            for fp in social.following(my):
                self.following_list.addItem(QtWidgets.QListWidgetItem(f"{self._resolve(fp)}  ({fp[:10]}…)"))
        except Exception:
            pass

    def _unfollow_selected(self) -> None:
        social = self._social()
        my = self._author_fp_hex()
        item = self.following_list.currentItem()
        if not item or social is None or not my:
            return
        text = item.text()
        fp = text.split("(")[-1].split(")")[0].replace("…", "").strip()
        # Reconstruct full fp from known identities by prefix match.
        full = self._find_fp_by_prefix(fp)
        if full:
            social.unfollow(my, full)
            self._refresh_following()

    def _find_fp_by_prefix(self, prefix: str) -> str:
        for r in self.controller.list_known(include_blocked=True):
            if r["dest_hash"].startswith(prefix):
                return r["dest_hash"]
        return prefix

    def _refresh_feed(self) -> None:
        QtWidgets, _, _ = qt()
        self.feed.clear()
        social = self._social()
        if social is None:
            self.feed.addItem("(start the node to see your feed)")
            return
        my = self._author_fp_hex()
        if not my:
            self.feed.addItem("(pick an author identity)")
            return

        def work():
            return social.feed(my)

        def on_done(r):
            if isinstance(r, Exception) or not r:
                self.feed.addItem("(no posts yet — follow someone, then refresh)")
                return
            for p in r:
                who = self._resolve(p.get("author", ""))
                body = p.get("body", "")
                when = _when(int(p.get("ts", 0)))
                self.feed.addItem(QtWidgets.QListWidgetItem(f"{who} · {when}\n{body}"))

        offload(work, on_done=on_done)

    def _on_feed_item(self, item) -> None:
        # Could open a thread view; keep simple for now.
        pass

    def on_node_started(self) -> None:
        self._refresh_authors()
        self._refresh_feed()
        self._refresh_following()

    def refresh(self) -> None:
        self._refresh_feed()
        self._refresh_following()