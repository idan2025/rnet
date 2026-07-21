"""Mailbox service: store-and-forward endpoint for offline peers.

A node offering the ``messaging`` capability runs a mailbox destination that
accepts encrypted items addressed to identities it hosts. Relays and direct
senders deposit items here when the recipient's link is unavailable; the
recipient pulls them on reconnect.
"""
from __future__ import annotations

import logging

import RNS

from rnet.config import NodeConfig
from rnet.errors import MessageError
from rnet.messaging.store import MailboxStore

log = logging.getLogger(__name__)


class Mailbox:
    """Wraps a :class:`MailboxStore` with hosted-identity tracking.

    The live RNS wiring (a destination + request handler that accepts
    ``deposit`` requests for hosted identities) is added in
    :class:`rnet.messaging.service.MessagingService`. This class holds the
    policy and storage so it is unit-testable without RNS.
    """

    def __init__(self, store: MailboxStore, config: NodeConfig):
        self.store = store
        self.config = config
        # dest_hash -> RNS.Identity for identities this node hosts.
        self._hosted: dict = {}

    def host(self, dest_hash: str, identity: RNS.Identity) -> None:
        self._hosted[dest_hash] = identity

    def unhost(self, dest_hash: str) -> None:
        self._hosted.pop(dest_hash, None)

    def is_hosted(self, dest_hash: str) -> bool:
        return dest_hash in self._hosted

    def deposit(self, recipient: str, sender: str, item: bytes) -> int:
        if not self.is_hosted(recipient):
            raise MessageError(f"mailbox does not host {recipient}")
        return self.store.deposit(recipient, sender, item, ttl=self.config.mailbox_ttl)

    def expire(self) -> int:
        return self.store.expire()

    @property
    def hosted(self):
        return list(self._hosted.keys())