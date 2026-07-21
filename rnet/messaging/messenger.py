"""Encrypted messaging: direct messages, receipts, store-and-forward.

Flow (Phase 1, direct messages):

    sender                                  recipient
      |  Body(text, bw)                       |
      |  encrypt to recipient identity        |
      |  Envelope + SignedFrame(node identity)|
      |  ---- frame via transport/link ---->  |
      |                                       |  verify signature
      |                                       |  anti-replay check
      |                                       |  decrypt, store inbox
      |                                       |  build+sign Receipt
      |  <---- signed receipt frame --------  |
      |  mark outbox acked                    |

If the recipient is unreachable, the frame is left in the outbox and also
deposited at a relay's mailbox (store-and-forward) for the recipient to pull
on reconnect. Relays handle only ciphertext encrypted to the recipient.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import RNS

from rnet.config import NodeConfig
from rnet.core.events import EventBus, MESSAGE_RECEIVED, RECEIPT_RECEIVED
from rnet.errors import MessageError, ReplayError, SignatureError, WireError
from rnet.identity import IdentityManager, fingerprint
from rnet.messaging.store import InboxStore, MailboxStore, OutboxStore
from rnet.messaging.transport import (
    DeliveryError,
    FakeTransport,
    MessageTransport,
    PeerUnreachable,
)
from rnet.protocol import (
    Body,
    Envelope,
    FrameType,
    MessageKind,
    PRIORITY_NORMAL,
    PRIORITY_CONTROL,
    Receipt,
    ReplayWindow,
    build_signed_frame,
    pack_signed_frame,
    unpack_signed_frame,
)

log = logging.getLogger(__name__)


class Messenger:
    """Sends and receives signed, encrypted direct messages."""

    def __init__(
        self,
        config: NodeConfig,
        identity: RNS.Identity,
        idm: IdentityManager,
        bus: EventBus,
        inbox: InboxStore,
        outbox: OutboxStore,
        mailbox: MailboxStore,
        replay: ReplayWindow,
        transport: Optional[MessageTransport] = None,
        group_registry=None,
    ):
        self.config = config
        self.identity = identity
        self.idm = idm
        self.bus = bus
        self.inbox = inbox
        self.outbox = outbox
        self.mailbox = mailbox
        self.replay = replay
        self.transport = transport or FakeTransport()
        self.group_registry = group_registry  # maps group dest hash -> RNS.Identity
        self._seq = self._load_seq()

    # -- sequence persistence --------------------------------------------
    def _load_seq(self) -> int:
        row = self.idm.store.db.query_one(
            "SELECT value FROM cache WHERE key=?", ("meta:send_seq",)
        )
        return int(row["value"].decode()) if row else 0

    def _next_seq(self) -> int:
        self._seq += 1
        self.idm.store.db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at, kind) "
            "VALUES (?,?,?,?,?)",
            ("meta:send_seq", str(self._seq).encode(),
             2**31, int(time.time()), "meta"),
        )
        return self._seq

    @property
    def dest_hash_hex(self) -> str:
        # The node's own dest hash is set by the Node; for standalone use we
        # derive a stable id from the identity fingerprint.
        return fingerprint(self.identity).hex()

    # -- sending ----------------------------------------------------------
    def build_message_frame(
        self,
        recipient_identity: RNS.Identity,
        recipient_dest_hash: str,
        text: str,
        kind: int = int(MessageKind.DM),
        bw: int = 0,
        files: Optional[list] = None,
        reply: bytes = b"",
    ) -> tuple:
        """Build a signed, encrypted message frame. Returns (frame_bytes, env)."""
        body = Body(text=text, files=list(files or []), reply=reply, bw=int(bw))
        plaintext = body.to_bytes()
        ciphertext = recipient_identity.encrypt(plaintext)
        env = Envelope(
            sender=self.dest_hash_hex,
            recipient=recipient_dest_hash,
            kind=int(kind),
            id=Envelope.new_id(),
            ts=int(time.time()),
            ciphertext=ciphertext,
            nonce=os.urandom(16),
        )
        sf = build_signed_frame(
            FrameType.MESSAGE,
            seq=self._next_seq(),
            ts=int(time.time()),
            payload=env.to_bytes(),
            sign_fn=self.identity.sign,
            fp=fingerprint(self.identity),
            priority=PRIORITY_NORMAL,
        )
        return pack_signed_frame(sf), env

    async def send_dm(
        self,
        recipient_dest_hash: str,
        recipient_identity: RNS.Identity,
        text: str,
        bw: int = 0,
    ) -> str:
        """Queue an encrypted DM and attempt immediate delivery.

        Returns the message id (hex). On unreachable peer the message stays
        queued for store-and-forward / later retry.
        """
        frame_bytes, env = self.build_message_frame(
            recipient_identity, recipient_dest_hash, text, bw=int(bw)
        )
        self.outbox.queue(env.message_id_hex(), recipient_dest_hash, frame_bytes,
                          max_attempts=self.config.outbox_max_attempts)
        await self._try_deliver(env.message_id_hex(), recipient_dest_hash, frame_bytes)
        return env.message_id_hex()

    async def _try_deliver(self, message_id: str, recipient: str, frame_bytes: bytes) -> None:
        try:
            response = await self.transport.deliver(recipient, frame_bytes)
        except PeerUnreachable as exc:
            self.outbox.mark_attempt(message_id, error=str(exc),
                                     base_delay=self.config.outbox_base_delay)
            log.info("peer %s unreachable; queued for S&F", recipient)
            return
        except (DeliveryError, Exception) as exc:
            self.outbox.mark_attempt(message_id, error=str(exc),
                                     base_delay=self.config.outbox_base_delay)
            log.warning("delivery failed to %s: %s", recipient, exc)
            return
        # Response is a signed receipt frame; process it.
        self.outbox.mark_delivered(message_id)
        if response:
            await self.process_receipt(response)

    async def process_receipt(self, receipt_frame_bytes: bytes) -> None:
        try:
            sf = unpack_signed_frame(receipt_frame_bytes)
        except WireError:
            log.warning("malformed receipt frame")
            return
        if sf.frame.type != int(FrameType.RECEIPT):
            return
        # Verify the receipt's signature against the stored sender identity.
        receipt = Receipt.from_bytes(sf.frame.payload)
        by_ident_row = self.idm.store.get_known(receipt.by)
        if by_ident_row and by_ident_row["pubkey"]:
            ident = RNS.Identity(create_keys=False)
            ident.load_public_key(by_ident_row["pubkey"])
            if ident.validate(receipt.sig, receipt.signing_bytes()):
                self.outbox.mark_acked(receipt.message_id.hex())
                self.bus.emit(RECEIPT_RECEIVED, {"message_id": receipt.message_id.hex(),
                                                 "by": receipt.by})
                return
        log.warning("receipt signature unverified for %s", receipt.by)

    async def poll_outbox(self) -> int:
        """Retry pending outbox items. Returns number attempted."""
        pending = self.outbox.pending()
        for row in pending:
            await self._try_deliver(row["id"], row["recipient"], row["envelope"])
        return len(pending)

    # -- receiving --------------------------------------------------------
    async def receive(self, frame_bytes: bytes) -> bytes:
        """Process an incoming signed message frame.

        Verifies signature + anti-replay, decrypts, stores in the inbox, and
        returns a signed receipt frame for the sender (or b"" to decline).
        """
        try:
            sf = unpack_signed_frame(frame_bytes)
        except WireError as exc:
            raise MessageError(f"bad message frame: {exc}") from exc
        if sf.frame.type != int(FrameType.MESSAGE):
            raise MessageError("not a message frame")

        env = Envelope.from_bytes(sf.frame.payload)
        # Resolve sender identity from cache; require a known pubkey.
        sender_row = self.idm.store.get_known(env.sender)
        if not sender_row or not sender_row["pubkey"]:
            raise MessageError(f"unknown sender identity: {env.sender}")
        sender_ident = RNS.Identity(create_keys=False)
        sender_ident.load_public_key(sender_row["pubkey"])

        # Verify the frame signature over the packed (compressed) frame.
        from rnet.protocol.wire import pack_frame
        if not sender_ident.validate(sf.sig, pack_frame(sf.frame)):
            raise SignatureError("message frame signature invalid")
        if sf.fp != fingerprint(sender_ident):
            raise SignatureError("frame fingerprint mismatch")

        # Anti-replay (clock_skew=0: trust sender clock for radio nodes).
        try:
            self.replay.check_and_remember(env.sender, sf.frame)
        except ReplayError as exc:
            log.warning("replay rejected from %s: %s", env.sender, exc)
            raise

        # Decrypt the body. DMs are encrypted to our identity; group/channel
        # messages are encrypted to the group identity (looked up via the
        # group registry).
        if env.kind in (int(MessageKind.GROUP), int(MessageKind.CHANNEL)) and \
                self.group_registry is not None:
            group_ident = self.group_registry.get(env.recipient)
            if group_ident is None:
                raise MessageError(f"unknown group {env.recipient}")
            plaintext = group_ident.decrypt(env.ciphertext)
        else:
            plaintext = self.identity.decrypt(env.ciphertext)
        if plaintext is None:
            raise MessageError("could not decrypt message (not for us?)")
        body = Body.from_bytes(plaintext)

        self.inbox.put(
            message_id=env.message_id_hex(),
            sender=env.sender,
            recipient=env.recipient,
            kind=env.kind,
            ts=env.ts,
            body=body.to_bytes(),
            ciphertext=env.ciphertext,
            signature=sf.sig,
            verified=True,
        )
        self.bus.emit(MESSAGE_RECEIVED, {
            "id": env.message_id_hex(), "sender": env.sender, "text": body.text,
        })

        # Build + sign a receipt and return it as a signed frame.
        receipt = Receipt(
            message_id=env.id,
            by=self.dest_hash_hex,
            ts=int(time.time()),
        )
        receipt.sig = self.identity.sign(receipt.signing_bytes())
        rsf = build_signed_frame(
            FrameType.RECEIPT,
            seq=self._next_seq(),
            ts=int(time.time()),
            payload=receipt.to_bytes(),
            sign_fn=self.identity.sign,
            fp=fingerprint(self.identity),
            priority=PRIORITY_CONTROL,
        )
        return pack_signed_frame(rsf)

    # -- mailbox (store-and-forward) -------------------------------------
    def deposit_mailbox(self, recipient: str, sender: str, item: bytes) -> int:
        """Relay path: hold an encrypted item for a hosted identity."""
        return self.mailbox.deposit(recipient, sender, item, ttl=self.config.mailbox_ttl)

    async def poll_mailbox(self, hosted_identity_dest_hash: str,
                           hosted_identity: RNS.Identity) -> int:
        """Pull pending mailbox items for a hosted identity and ingest them.

        ``item`` is a signed message frame encrypted to ``hosted_identity``.
        Returns the number ingested.
        """
        rows = self.mailbox.pending_for(hosted_identity_dest_hash)
        n = 0
        for row in rows:
            try:
                # Temporarily act as the hosted identity for decryption.
                saved = self.identity
                self.identity = hosted_identity
                try:
                    await self.receive(bytes(row["item"]))
                finally:
                    self.identity = saved
                self.mailbox.mark_delivered(int(row["id"]))
                n += 1
            except (MessageError, SignatureError, ReplayError) as exc:
                log.warning("mailbox item %s rejected: %s", row["id"], exc)
        return n