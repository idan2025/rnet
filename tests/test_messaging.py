import asyncio
import os
import tempfile

import RNS
import pytest

from rnet.config import NodeConfig
from rnet.core.events import EventBus, MESSAGE_RECEIVED, RECEIPT_RECEIVED
from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, fingerprint
from rnet.messaging import (
    FakeTransport,
    InboxStore,
    Mailbox,
    MailboxStore,
    Messenger,
    OutboxStore,
)
from rnet.protocol import Bandwidth, ReplayWindow


def _setup(tmp, name):
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"{name}_keys"))
    ident = idm.create(name, is_node=True)
    cfg = NodeConfig(name=name, datadir=tmp, announce_interval=100000)
    inbox = InboxStore(db)
    outbox = OutboxStore(db)
    mbox = MailboxStore(db)
    replay = ReplayWindow(db, window=64, clock_skew=0)
    bus = EventBus()
    return db, idm, ident, cfg, inbox, outbox, mbox, replay, bus


def _register_self(messenger: Messenger, dest_hash: str):
    """Make a messenger's own identity 'known' so receipts can verify."""
    messenger.idm.store.upsert_known(
        dest_hash=dest_hash,
        fingerprint_bytes=fingerprint(messenger.identity),
        pubkey=messenger.identity.get_public_key(),
        name=messenger.config.name,
        verified=True,
    )


def test_dm_delivery_and_receipt():
    with tempfile.TemporaryDirectory() as tmp:
        (adb, aidm, a_ident, acfg, ainbox, aout, ambox, areplay, abus) = _setup(tmp, "alice")
        (bdb, bidm, b_ident, bcfg, binbox, bout, bmbox, breplay, bbus) = _setup(tmp, "bob")

        a_dest = fingerprint(a_ident).hex()
        b_dest = fingerprint(b_ident).hex()
        # Each side knows the other's identity (in real life via announces).
        aidm.store.upsert_known(b_dest, fingerprint(b_ident), b_ident.get_public_key(), "bob", True)
        bidm.store.upsert_known(a_dest, fingerprint(a_ident), a_ident.get_public_key(), "alice", True)

        transport = FakeTransport()
        a = Messenger(acfg, a_ident, aidm, abus, ainbox, aout, ambox, areplay, transport)
        b = Messenger(bcfg, b_ident, bidm, bbus, binbox, bout, bmbox, breplay, transport)
        _register_self(a, a_dest)
        _register_self(b, b_dest)

        # Bob registers a receive handler at his dest hash.
        async def b_handler(recipient, frame):
            return await b.receive(frame)
        transport.register(b_dest, b_handler)

        received = []
        bbus.subscribe(MESSAGE_RECEIVED, lambda e: received.append(e))
        acks = []
        abus.subscribe(RECEIPT_RECEIVED, lambda e: acks.append(e))

        async def run():
            mid = await a.send_dm(b_dest, b_ident, "hello over the mesh", bw=int(Bandwidth.LOW))
            # Allow async event dispatch to flush.
            await asyncio.sleep(0)
            return mid
        mid = asyncio.run(run())

        # Bob received + stored
        assert len(received) == 1
        assert received[0]["text"] == "hello over the mesh"
        rows = binbox.list(b_dest)
        assert len(rows) == 1
        assert rows[0]["sender"] == a_dest
        # Alice got the receipt and marked acked
        assert len(acks) == 1
        assert aout.get(mid)["ack_received"] == 1
        assert aout.get(mid)["delivered"] == 1


def test_offline_store_and_forward():
    with tempfile.TemporaryDirectory() as tmp:
        (adb, aidm, a_ident, acfg, ainbox, aout, ambox, areplay, abus) = _setup(tmp, "alice")
        (bdb, bidm, b_ident, bcfg, binbox, bout, bmbox, breplay, bbus) = _setup(tmp, "bob")
        a_dest = fingerprint(a_ident).hex()
        b_dest = fingerprint(b_ident).hex()
        aidm.store.upsert_known(b_dest, fingerprint(b_ident), b_ident.get_public_key(), "bob", True)
        bidm.store.upsert_known(a_dest, fingerprint(a_ident), a_ident.get_public_key(), "alice", True)

        transport = FakeTransport()  # nobody registered => unreachable
        a = Messenger(acfg, a_ident, aidm, abus, ainbox, aout, ambox, areplay, transport)
        b = Messenger(bcfg, b_ident, bidm, bbus, binbox, bout, bmbox, breplay, transport)
        _register_self(a, a_dest)
        _register_self(b, b_dest)

        # Bob hosts a mailbox for himself.
        bmailbox = Mailbox(MailboxStore(bdb), bcfg)
        bmailbox.host(b_dest, b_ident)

        async def run():
            # Alice sends -> unreachable -> queued in outbox.
            mid = await a.send_dm(b_dest, b_ident, "offline hello", bw=int(Bandwidth.LOW))
            # The message is queued undelivered; fetch its frame.
            row = aout.get(mid)
            assert row["delivered"] == 0
            frame = row["envelope"]
            # Deposit happens at Bob's mailbox (in real life via a request).
            assert bmailbox.is_hosted(b_dest)
            bmailbox.deposit(b_dest, a_dest, bytes(frame))
            # Bob comes online, polls mailbox, ingests.
            n = await b.poll_mailbox(b_dest, b_ident)
            return mid, n
        mid, n = asyncio.run(run())

        assert n == 1
        rows = binbox.list(b_dest)
        assert len(rows) == 1
        assert rows[0]["sender"] == a_dest


def test_replay_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        (adb, aidm, a_ident, acfg, ainbox, aout, ambox, areplay, abus) = _setup(tmp, "alice")
        (bdb, bidm, b_ident, bcfg, binbox, bout, bmbox, breplay, bbus) = _setup(tmp, "bob")
        a_dest = fingerprint(a_ident).hex()
        b_dest = fingerprint(b_ident).hex()
        aidm.store.upsert_known(b_dest, fingerprint(b_ident), b_ident.get_public_key(), "bob", True)
        bidm.store.upsert_known(a_dest, fingerprint(a_ident), a_ident.get_public_key(), "alice", True)

        transport = FakeTransport()
        a = Messenger(acfg, a_ident, aidm, abus, ainbox, aout, ambox, areplay, transport)
        b = Messenger(bcfg, b_ident, bidm, bbus, binbox, bout, bmbox, breplay, transport)
        _register_self(a, a_dest)
        _register_self(b, b_dest)

        async def b_handler(recipient, frame):
            return await b.receive(frame)
        transport.register(b_dest, b_handler)

        async def run():
            frame, _env = a.build_message_frame(b_ident, b_dest, "once")
            r1 = await b.receive(frame)
            assert r1  # receipt
            from rnet.errors import ReplayError
            try:
                await b.receive(frame)  # exact replay
                return False
            except ReplayError:
                return True
        assert asyncio.run(run())


def test_unknown_sender_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        (bdb, bidm, b_ident, bcfg, binbox, bout, bmbox, breplay, bbus) = _setup(tmp, "bob")
        # Stranger builds a frame; Bob doesn't know the sender identity.
        stranger = RNS.Identity()
        s_cfg = NodeConfig(name="stranger", datadir=tmp, announce_interval=100000)
        s_idm = IdentityManager(IdentityStore(Database(os.path.join(tmp, "s.db"))),
                                os.path.join(tmp, "s_keys"))
        s_out = OutboxStore(s_idm.store.db)
        s_inbox = InboxStore(s_idm.store.db)
        s_mbox = MailboxStore(s_idm.store.db)
        s_replay = ReplayWindow(s_idm.store.db, clock_skew=0)
        stranger_m = Messenger(s_cfg, stranger, s_idm, EventBus(), s_inbox, s_out, s_mbox, s_replay, FakeTransport())
        b_dest = fingerprint(b_ident).hex()
        # Bob does NOT register stranger as known.
        b_m = Messenger(bcfg, b_ident, bidm, bbus, binbox, bout, bmbox, breplay, FakeTransport())
        frame, _ = stranger_m.build_message_frame(b_ident, b_dest, "hi")
        from rnet.errors import MessageError
        try:
            asyncio.run(b_m.receive(frame))
            assert False, "must reject unknown sender"
        except MessageError:
            pass