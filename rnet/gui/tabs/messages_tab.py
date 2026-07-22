"""Conversations tab: per-peer threaded chat with timestamps + receipts."""
from __future__ import annotations

import time

from rnet.gui.tabs.base import BaseTab
from rnet.gui.widgets import qt, Card, SectionLabel, SearchField, BubbledMessage, Toast, warn


def _ts(clock: int) -> str:
    if not clock:
        return ""
    t = time.localtime(clock)
    return time.strftime("%H:%M", t)


def _body_text(raw: bytes) -> str:
    try:
        from rnet.protocol import Body
        return Body.from_bytes(bytes(raw)).text or "(empty)"
    except Exception:
        try:
            return bytes(raw).decode("utf-8", "replace")
        except Exception:
            return "(unreadable)"


class ConversationsTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(self.widget)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # Left: conversation list.
        left = Card()
        ll = QtWidgets.QVBoxLayout(left)
        ll.addWidget(SectionLabel("Conversations"))
        self.search = SearchField("Search conversations…")
        self.search.textChanged.connect(self._filter_convos)
        ll.addWidget(self.search)
        self.convos = QtWidgets.QListWidget()
        self.convos.itemClicked.connect(self._on_convo)
        ll.addWidget(self.convos, 1)
        new_btn = QtWidgets.QPushButton("New conversation")
        new_btn.clicked.connect(self._new_convo)
        ll.addWidget(new_btn)
        root.addWidget(left, 2)

        # Right: chat.
        right = Card()
        rl = QtWidgets.QVBoxLayout(right)
        self.peer_label = QtWidgets.QLabel("Select a conversation")
        f = self.peer_label.font(); f.setBold(True)
        self.peer_label.setFont(f)
        rl.addWidget(self.peer_label)
        self.chat = QtWidgets.QScrollArea()
        self.chat.setWidgetResizable(True)
        self.chat_inner = QtWidgets.QWidget()
        self.chat_lay = QtWidgets.QVBoxLayout(self.chat_inner)
        self.chat_lay.addStretch(1)
        self.chat.setWidget(self.chat_inner)
        rl.addWidget(self.chat, 1)
        comp = QtWidgets.QHBoxLayout()
        self.composer = QtWidgets.QPlainTextEdit()
        self.composer.setFixedHeight(64)
        self.composer.setPlaceholderText("Type a message…  (Ctrl+Enter to send)")
        comp.addWidget(self.composer, 1)
        self.send_btn = QtWidgets.QPushButton("Send")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self._on_send)
        comp.addWidget(self.send_btn)
        rl.addLayout(comp)
        root.addWidget(right, 3)

        self.current_peer = None
        self._sent_echo = []  # (peer, text, ts) transient outgoings

        if bridge is not None:
            bridge.message_received.connect(lambda e: self._on_message_event(e))
            bridge.receipt_received.connect(lambda e: self._on_receipt(e))

        self._refresh_convos()

    # -- conversations list ----------------------------------------------
    def _our_dest(self) -> str:
        node = self.controller.node
        return node.node_dest_hash if node and node.running and node.node_dest_hash else ""

    def _refresh_convos(self) -> None:
        QtWidgets, _, _ = qt()
        self.convos.clear()
        our = self._our_dest()
        if not our:
            self.convos.addItem("(start the node to see conversations)")
            return
        rows = self.controller.db.query(
            "SELECT sender, MAX(received_at) AS last, COUNT(*) AS n "
            "FROM inbox WHERE recipient=? GROUP BY sender ORDER BY last DESC",
            (our,),
        )
        if not rows:
            self.convos.addItem("(no conversations yet — discover peers, then say hi)")
            return
        for r in rows:
            peer = r["sender"]
            name = self._peer_name(peer)
            unread = self._unread(peer)
            last = self._last_text(our, peer)
            label = f"{name}{'  ●' + str(unread) if unread else ''}  ·  {last[:40]}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, peer)
            self.convos.addItem(item)

    def _filter_convos(self, needle: str) -> None:
        needle = (needle or "").lower()
        for i in range(self.convos.count()):
            item = self.convos.item(i)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _peer_name(self, peer: str) -> str:
        try:
            row = self.controller.idm.store.get_known(peer)
            if row and (row["display"] or row["name"]):
                return row["display"] or row["name"]
        except Exception:
            pass
        return peer[:10] + "…"

    def _unread(self, peer: str) -> int:
        our = self._our_dest()
        if not our:
            return 0
        row = self.controller.db.query_one(
            "SELECT COUNT(*) AS n FROM inbox WHERE recipient=? AND sender=? AND read_at IS NULL",
            (our, peer),
        )
        return int(row["n"]) if row else 0

    def _last_text(self, our: str, peer: str) -> str:
        row = self.controller.db.query_one(
            "SELECT body FROM inbox WHERE recipient=? AND sender=? "
            "ORDER BY received_at DESC LIMIT 1", (our, peer))
        return _body_text(row["body"]) if row else ""

    # -- chat view -------------------------------------------------------
    def _on_convo(self, item) -> None:
        peer = item.data(QtCore.Qt.UserRole)
        if not peer:
            return
        self.current_peer = peer
        self.peer_label.setText(self._peer_name(peer))
        self._load_chat(peer)
        self._mark_read(peer)
        self._refresh_convos()

    def _load_chat(self, peer: str) -> None:
        QtWidgets, _, _ = qt()
        # Clear existing bubbles.
        while self.chat_lay.count() > 1:
            child = self.chat_lay.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        our = self._our_dest()
        rows = self.controller.db.query(
            "SELECT * FROM inbox WHERE recipient=? AND sender=? ORDER BY received_at ASC",
            (our, peer),
        ) if our else []
        for r in rows:
            self._add_bubble(_body_text(r["body"]), r["received_at"], outgoing=False,
                             state="acked" if r["verified"] else "")
        # Transient sent echoes for this peer.
        for p, text, ts in self._sent_echo:
            if p == peer:
                self._add_bubble(text, ts, outgoing=True, state="sent")
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def _add_bubble(self, text: str, ts: int, outgoing: bool, state: str = "") -> None:
        b = BubbledMessage(text, _ts(ts), outgoing=outgoing, state=state)
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, b)

    def _mark_read(self, peer: str) -> None:
        our = self._our_dest()
        if not our:
            return
        self.controller.db.execute(
            "UPDATE inbox SET read_at=? WHERE recipient=? AND sender=? AND read_at IS NULL",
            (int(time.time()), our, peer),
        )

    # -- incoming -------------------------------------------------------
    def _on_message_event(self, event) -> None:
        if not isinstance(event, dict):
            return
        peer = event.get("sender", "")
        text = event.get("text", "")
        if self.current_peer == peer:
            self._add_bubble(text, int(time.time()), outgoing=False, state="acked")
            self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())
            self._mark_read(peer)
        self._refresh_convos()

    def _on_receipt(self, event) -> None:
        # Could mark a sent echo as delivered/acked; kept simple here.
        pass

    # -- send -----------------------------------------------------------
    def _new_convo(self) -> None:
        QtWidgets, _, _ = qt()
        # Pick from known peers.
        known = self.controller.list_known()
        if not known:
            warn(self.widget, "No contacts", "Discover peers first — they appear in Contacts once they announce.")
            return
        names = [f"{r['display'] or r['name'] or r['dest_hash'][:10]}  ({r['dest_hash'][:10]}…)" for r in known]
        choice, ok = QtWidgets.QInputDialog.getItem(self.widget, "New conversation",
                                                     "Choose a contact:", names, 0, False)
        if not ok:
            return
        idx = names.index(choice)
        peer = known[idx]["dest_hash"]
        self.current_peer = peer
        self.peer_label.setText(self._peer_name(peer))
        self._load_chat(peer)

    def _on_send(self) -> None:
        QtWidgets, _, _ = qt()
        peer = self.current_peer
        text = self.composer.toPlainText().strip()
        if not peer:
            warn(self.widget, "No conversation", "Pick or start a conversation first.")
            return
        if not text:
            return
        sdk = self.controller.sdk
        if sdk is None or sdk.messenger is None:
            warn(self.widget, "messaging", "node not running with messaging capability")
            return
        known = sdk.idm.store.get_known(peer)
        if not known or not known["pubkey"]:
            warn(self.widget, "unknown recipient",
                 f"{peer[:10]}… hasn't announced yet — wait for their announce.")
            return
        import RNS
        recip = RNS.Identity(create_keys=False)
        recip.load_public_key(bytes(known["pubkey"]))

        ts = int(time.time())
        self._sent_echo.append((peer, text, ts))
        self._add_bubble(text, ts, outgoing=True, state="sent")
        self.composer.clear()
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

        def on_done(_mid):
            pass

        def on_error(exc):
            warn(self.widget, "send failed", str(exc))

        self.controller.run_async(
            sdk.send_message(peer, recip, text),
            on_done=on_done, on_error=on_error,
        )

    # -- lifecycle ------------------------------------------------------
    def on_node_started(self) -> None:
        self._refresh_convos()

    def refresh(self) -> None:
        self._refresh_convos()
        if self.current_peer:
            self._load_chat(self.current_peer)

    def focus_search(self) -> None:
        self.search.setFocus()