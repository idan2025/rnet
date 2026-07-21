"""Messages tab: send DM, list inbox, live incoming."""
from __future__ import annotations

from rnet.gui.tabs.base import BaseTab, qt


class MessagesTab(BaseTab):
    def __init__(self, controller, bridge):
        super().__init__(controller, bridge)
        QtWidgets, QtCore, QtGui = qt()
        self.widget = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(self.widget)

        # Compose
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Recipient (fingerprint/dest hash):"))
        self.recipient_field = QtWidgets.QLineEdit()
        self.recipient_field.setPlaceholderText("e.g. 12e95047d5eb53ad")
        row.addWidget(self.recipient_field, 1)
        v.addLayout(row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Message:"))
        self.text_field = QtWidgets.QLineEdit()
        row.addWidget(self.text_field, 1)
        self.send_btn = QtWidgets.QPushButton("Send")
        row.addWidget(self.send_btn)
        v.addLayout(row)

        # Inbox
        v.addWidget(QtWidgets.QLabel("Inbox:"))
        self.inbox = QtWidgets.QListWidget()
        v.addWidget(self.inbox, 1)

        self.send_btn.clicked.connect(self._on_send)
        self._refresh_inbox()

        if bridge is not None:
            bridge.message_received.connect(lambda e: self._on_message_event(e))

    def _refresh_inbox(self) -> None:
        QtWidgets, _, _ = qt()
        self.inbox.clear()
        sdk = self.controller.sdk
        if sdk is None or sdk.messenger is None:
            self.inbox.addItem("(start the node with messaging capability)")
            return
        rows = sdk.messenger.inbox.list()
        for r in rows:
            from rnet.protocol import Body
            try:
                body = Body.from_bytes(bytes(r["body"]))
                text = body.text
            except Exception:
                text = "(unreadable)"
            self.inbox.addItem(QtWidgets.QListWidgetItem(
                f"{r['sender'][:12]}…  {text}"))

    def _on_message_event(self, event) -> None:
        # event: {"id","sender","text"}
        QtWidgets, _, _ = qt()
        if isinstance(event, dict):
            self.inbox.addItem(QtWidgets.QListWidgetItem(
                f"{event.get('sender','')[:12]}…  {event.get('text','')}"))

    def _on_send(self) -> None:
        QtWidgets, _, _ = qt()
        recipient = self.recipient_field.text().strip().lower()
        text = self.text_field.text()
        if not recipient or not text:
            return
        sdk = self.controller.sdk
        if sdk is None or sdk.messenger is None:
            QtWidgets.QMessageBox.warning(self.widget, "messaging",
                                          "start the node with the messaging capability first")
            return
        # Look up recipient identity from known cache.
        known = sdk.idm.store.get_known(recipient)
        if not known or not known["pubkey"]:
            QtWidgets.QMessageBox.warning(self.widget, "unknown recipient",
                f"recipient {recipient} not in known-identity cache; wait for their announce")
            return
        import RNS
        recip_ident = RNS.Identity(create_keys=False)
        recip_ident.load_public_key(bytes(known["pubkey"]))

        def on_done(mid):
            self.text_field.clear()
            self.recipient_field.clear()

        def on_error(exc):
            QtWidgets.QMessageBox.warning(self.widget, "send failed", str(exc))

        self.controller.run_async(
            sdk.send_message(recipient, recip_ident, text),
            on_done=on_done, on_error=on_error,
        )

    def on_node_started(self) -> None:
        self._refresh_inbox()