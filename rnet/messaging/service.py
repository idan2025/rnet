"""Live RNS wiring for messaging: request handler + poll loops.

Bridges the async :class:`Messenger` to RNS' synchronous, thread-based
request callbacks. Registered on the node's ``rnet.msg`` destination when the
node offers the ``messaging`` capability.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import RNS

from rnet.config import NodeConfig
from rnet.core.events import LoopBridge
from rnet.db.connection import Database
from rnet.errors import MessageError
from rnet.identity import IdentityManager
from rnet.messaging.messenger import Messenger
from rnet.messaging.mailbox import Mailbox
from rnet.messaging.store import InboxStore, MailboxStore, OutboxStore
from rnet.messaging.transport import RNSLinkTransport
from rnet.protocol import ReplayWindow

log = logging.getLogger(__name__)

MSG_APP = "rnet"
MSG_ASPECT = "msg"


class MessagingService:
    """Owns the messaging destination, messenger, and poll loops for a node."""

    def __init__(self, config: NodeConfig, identity: RNS.Identity, db: Database,
                 idm: IdentityManager, bridge: LoopBridge):
        self.config = config
        self.identity = identity
        self.db = db
        self.idm = idm
        self.bridge = bridge
        self.inbox = InboxStore(db)
        self.outbox = OutboxStore(db)
        self.mailbox_store = MailboxStore(db)
        self.replay = ReplayWindow(db, window=config.replay_window, clock_skew=0)
        self.transport = RNSLinkTransport()
        self.bus = bridge.bus
        self.messenger = Messenger(
            config, identity, idm, self.bus, self.inbox, self.outbox,
            self.mailbox_store, self.replay, self.transport,
        )
        self.mailbox = Mailbox(self.mailbox_store, config)
        self.destination: Optional[RNS.Destination] = None
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def dest_hash_hex(self) -> str:
        return self.messenger.dest_hash_hex

    def start(self) -> None:
        """Create the messaging destination and register the request handler.

        Called on the loop thread during node start.
        """
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            MSG_APP,
            MSG_ASPECT,
        )
        # Host our own identity in the mailbox so peers can S&F to us.
        self.mailbox.host(self.dest_hash_hex, self.identity)
        self.idm.store.upsert_known(
            dest_hash=self.dest_hash_hex,
            fingerprint_bytes=self._fp(),
            pubkey=self.identity.get_public_key(),
            name=self.config.name,
            verified=True,
        )

        service = self

        def response_generator(path, data, request_id, remote_identity, requested_at):
            """RNS calls this on its thread; marshal to the loop and block."""
            try:
                fut = service.bridge.run_coroutine_threadsafe(
                    service.messenger.receive(bytes(data))
                )
                response = fut.result(timeout=service.config.request_timeout
                                      if hasattr(service.config, "request_timeout") else 30)
                return response
            except (MessageError, asyncio.TimeoutError) as exc:
                log.warning("messaging request failed: %s", exc)
                return None
            except Exception:  # pragma: no cover
                log.exception("messaging handler crashed")
                return None

        self.destination.register_request_handler(
            "msg", response_generator=response_generator, allow=RNS.Destination.ALLOW_ALL
        )
        # Optional forward secrecy via RNS identity ratchets.
        if getattr(self.config, "ratchets_path", None):
            try:
                self.destination.enable_ratchets(self.config.ratchets_path)
                self.destination.enforce_ratchets()
                log.info("messaging ratchets enabled")
            except Exception as exc:  # pragma: no cover - depends on RNS build
                log.warning("could not enable ratchets: %s", exc)

    def _fp(self) -> bytes:
        from rnet.identity.util import fingerprint
        return fingerprint(self.identity)

    async def start_loops(self) -> None:
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self.destination is not None:
            try:
                self.destination.deregister_request_handler("msg")
            except Exception:  # pragma: no cover
                pass

    async def _poll_loop(self) -> None:
        """Retry the outbox and expire the mailbox on a slow cadence."""
        while True:
            try:
                await asyncio.sleep(self.config.outbox_base_delay)
                await self.messenger.poll_outbox()
                self.mailbox.expire()
            except asyncio.CancelledError:
                return
            except Exception:  # pragma: no cover
                log.exception("messaging poll loop error")
                await asyncio.sleep(5)