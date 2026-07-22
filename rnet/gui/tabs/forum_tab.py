"""Forum tab: boards, recent threads, thread view with replies."""
from __future__ import annotations

import time

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, warn
from rnet.gui.workers import offload


def _when(ts: int) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


class ForumTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(self.widget)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Left: author + board + compose + recent.
        left = Card()
        ll = QtWidgets.QVBoxLayout(left)
        ll.addWidget(SectionLabel("Author"))
        self.author_box = QtWidgets.QComboBox()
        self.author_box.setEditable(True)
        ll.addWidget(self.author_box)
        ll.addWidget(SectionLabel("Board"))
        brow = QtWidgets.QHBoxLayout()
        self.board_field = QtWidgets.QLineEdit("forum")
        brow.addWidget(self.board_field, 1)
        ll.addLayout(brow)
        self.post_field = QtWidgets.QPlainTextEdit()
        self.post_field.setFixedHeight(60)
        self.post_field.setPlaceholderText("start a new thread…")
        ll.addWidget(self.post_field)
        pb = QtWidgets.QPushButton("Post thread")
        pb.setObjectName("primary")
        pb.clicked.connect(self._on_post)
        ll.addWidget(pb)
        ll.addWidget(SectionLabel("Recent threads"))
        self.recent = QtWidgets.QListWidget()
        self.recent.itemClicked.connect(self._on_thread)
        ll.addWidget(self.recent, 1)
        rb = QtWidgets.QPushButton("Refresh")
        rb.clicked.connect(self._refresh)
        ll.addWidget(rb)
        root.addWidget(left, 2)

        # Right: thread view + reply.
        right = Card()
        rl = QtWidgets.QVBoxLayout(right)
        rl.addWidget(SectionLabel("Thread"))
        self.thread_out = QtWidgets.QPlainTextEdit()
        self.thread_out.setReadOnly(True)
        rl.addWidget(self.thread_out, 1)
        rl.addWidget(SectionLabel("Reply"))
        self.reply_field = QtWidgets.QPlainTextEdit()
        self.reply_field.setFixedHeight(60)
        rl.addWidget(self.reply_field)
        rrow = QtWidgets.QHBoxLayout()
        rrow.addStretch(1)
        self.reply_btn = QtWidgets.QPushButton("Reply")
        self.reply_btn.clicked.connect(self._on_reply)
        rrow.addWidget(self.reply_btn)
        rl.addLayout(rrow)
        root.addWidget(right, 3)

        self._current_root = b""
        self._refresh_authors()
        self._refresh()

    def _refresh_authors(self) -> None:
        QtWidgets, _, _ = qt()
        self.author_box.clear()
        default = self.controller.default_identity_name()
        for r in self.controller.list_own_identities():
            self.author_box.addItem(r["name"])
        if default:
            idx = max(0, self.author_box.findText(default))
            self.author_box.setCurrentIndex(idx)

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
            warn(self.widget, "forum", "start the node first")
            return
        name = self.author_box.currentText().strip()
        ident = self.controller.load_identity(name)
        text = self.post_field.toPlainText().strip()
        if ident is None or not text:
            return
        from rnet.identity import fingerprint
        self.controller.idm.store.upsert_known(fingerprint(ident).hex(), fingerprint(ident),
                                               ident.get_public_key(), name, True)

        def work():
            return forum.post(ident, text)

        def on_done(r):
            if isinstance(r, Exception):
                warn(self.widget, "post failed", str(r))
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
                self.recent.addItem("(no threads yet — start one)")
                return
            for p in r:
                who = self._resolve(p.get("author", ""))
                body = (p.get("body") or "")[:60]
                h = bytes(p["hash"]).hex() if isinstance(p.get("hash"), (bytes, bytearray)) else p.get("hash", "")
                self.recent.addItem(QtWidgets.QListWidgetItem(f"{who} · {body}\n{h}"))

        offload(work, on_done=on_done)

    def _resolve(self, fp_hex: str) -> str:
        try:
            row = self.controller.idm.store.get_known_by_fp(bytes.fromhex(fp_hex))
            if row and (row["display"] or row["name"]):
                return row["display"] or row["name"]
        except Exception:
            pass
        return fp_hex[:10] + "…"

    def _on_thread(self, item) -> None:
        QtWidgets, _, _ = qt()
        # Hash is on the second line of the item text.
        lines = item.text().split("\n")
        h = lines[-1].strip() if len(lines) > 1 else ""
        try:
            self._current_root = bytes.fromhex(h)
        except Exception:
            return
        forum = self._forum()
        if forum is None:
            return
        self.thread_out.clear()

        def work():
            return forum.thread(self._current_root)

        def on_done(r):
            if isinstance(r, Exception):
                self.thread_out.appendPlainText(f"failed: {r}")
                return
            for p in r:
                indent = "    ↳ " if p.reply_to else ""
                self.thread_out.appendPlainText(f"{indent}{self._resolve(p.author)} · {_when(p.ts)}")
                self.thread_out.appendPlainText(f"{p.body}\n")

        offload(work, on_done=on_done)

    def _on_reply(self) -> None:
        QtWidgets, _, _ = qt()
        if not self._current_root:
            warn(self.widget, "reply", "select a thread first")
            return
        forum = self._forum()
        if forum is None:
            return
        name = self.author_box.currentText().strip()
        ident = self.controller.load_identity(name)
        text = self.reply_field.toPlainText().strip()
        if ident is None or not text:
            return

        def work():
            return forum.post(ident, text, reply_to=self._current_root)

        def on_done(r):
            if isinstance(r, Exception):
                warn(self.widget, "reply failed", str(r))
            else:
                self.reply_field.clear()
                self._on_thread(QtWidgets.QListWidgetItem(self._current_root.hex()))

        offload(work, on_done=on_done)

    def on_node_started(self) -> None:
        self._refresh_authors()
        self._refresh()