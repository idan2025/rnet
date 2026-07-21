import asyncio
import os
import tempfile

import RNS

from rnet.config import NodeConfig
from rnet.core.events import EventBus
from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, fingerprint
from rnet.messaging import (
    FakeTransport,
    GroupManager,
    GroupRegistry,
    InboxStore,
    MailboxStore,
    Messenger,
    OutboxStore,
)
from rnet.protocol import Bandwidth, MessageKind, ReplayWindow


def _messenger(tmp, name, group_registry=None, transport=None):
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"k_{name}"))
    ident = idm.create(name, is_node=True)
    cfg = NodeConfig(name=name, datadir=tmp)
    return db, idm, ident, Messenger(
        cfg, ident, idm, EventBus(), InboxStore(db), OutboxStore(db),
        MailboxStore(db), ReplayWindow(db, clock_skew=0),
        transport=transport, group_registry=group_registry,
    )


def _know(a_idm, b_idm, b_ident, name):
    bfp = fingerprint(b_ident).hex()
    a_idm.store.upsert_known(bfp, fingerprint(b_ident), b_ident.get_public_key(), name, True)
    return bfp


def test_create_invite_join_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        dbf, idmf, founder, _ = _messenger(tmp, "founder")
        keys_dir = os.path.join(tmp, "gkeys")
        reg = GroupRegistry(Database(os.path.join(tmp, "gd.db")), keys_dir)
        gm = GroupManager(reg)

        group = gm.create_group(founder, "mesh-team")
        assert group.dest_hash == fingerprint(group.identity).hex()
        assert reg.get(group.dest_hash) is not None

        # invite a member
        dbm, idmm, member, _ = _messenger(tmp, "member")
        invite = gm.invite_bytes(group, member)
        # member accepts on its own registry
        reg2 = GroupRegistry(Database(os.path.join(tmp, "gd2.db")), os.path.join(tmp, "gk2"))
        gm2 = GroupManager(reg2)
        joined = gm2.accept_invite(member, "mesh-team", fingerprint(founder).hex(), invite)
        assert joined.dest_hash == group.dest_hash
        # member can now decrypt group messages
        env = gm.build_group_envelope(member, group.identity, "hi team")
        body = gm2.open_envelope(env)
        assert body is not None
        from rnet.protocol import Body
        assert Body.from_bytes(body).text == "hi team"


def test_non_member_cannot_decrypt():
    with tempfile.TemporaryDirectory() as tmp:
        _, idmf, founder, _ = _messenger(tmp, "founder")
        reg = GroupRegistry(Database(os.path.join(tmp, "gd.db")), os.path.join(tmp, "gk"))
        gm = GroupManager(reg)
        group = gm.create_group(founder, "private")
        env = gm.build_group_envelope(founder, group.identity, "secret")
        # outsider registry doesn't have the group key
        reg_out = GroupRegistry(Database(os.path.join(tmp, "gdo.db")), os.path.join(tmp, "gko"))
        assert reg_out.get(group.dest_hash) is None
        from rnet.errors import MessageError
        # messenger with outsider group registry raises on receive
        dbx, idmx, outsider, mx = _messenger(tmp, "outsider",
                                             group_registry=reg_out, transport=FakeTransport())
        # register sender (founder) as known so signature verifies
        _know(idmx, idmf, founder, "founder")
        from rnet.protocol.wire import build_signed_frame, pack_signed_frame, FrameType, PRIORITY_NORMAL
        sf = build_signed_frame(FrameType.MESSAGE, seq=1, ts=env.ts,
                                payload=env.to_bytes(), sign_fn=founder.sign,
                                fp=fingerprint(founder), priority=PRIORITY_NORMAL)
        try:
            asyncio.run(mx.receive(pack_signed_frame(sf)))
            assert False, "non-member must not decrypt group message"
        except MessageError:
            pass


def test_group_message_delivered_to_member_via_messenger():
    with tempfile.TemporaryDirectory() as tmp:
        # founder + member each have a group registry sharing the group key
        reg_f = GroupRegistry(Database(os.path.join(tmp, "gdf.db")), os.path.join(tmp, "gkf"))
        reg_m = GroupRegistry(Database(os.path.join(tmp, "gdm.db")), os.path.join(tmp, "gkm"))
        dbf, idmf, founder, _ = _messenger(tmp, "founder", group_registry=reg_f)
        dbm, idmm, member, mb = _messenger(tmp, "member", group_registry=reg_m)
        _know(idmf, idmm, member, "member")
        _know(idmm, idmf, founder, "founder")

        group = GroupManager(reg_f).create_group(founder, "team")
        # distribute group key to member via invite
        invite = GroupManager(reg_f).invite_bytes(group, member)
        GroupManager(reg_m).accept_invite(member, "team", fingerprint(founder).hex(), invite)

        # founder builds a group envelope and sends it as a signed MESSAGE frame
        env = GroupManager(reg_f).build_group_envelope(founder, group.identity, "team hello")
        from rnet.protocol.wire import build_signed_frame, pack_signed_frame, FrameType, PRIORITY_NORMAL
        sf = build_signed_frame(FrameType.MESSAGE, seq=1, ts=env.ts,
                                payload=env.to_bytes(), sign_fn=founder.sign,
                                fp=fingerprint(founder), priority=PRIORITY_NORMAL)
        out = asyncio.run(mb.receive(pack_signed_frame(sf)))
        assert out  # receipt returned
        rows = mb.inbox.list()
        assert len(rows) == 1
        from rnet.protocol import Body
        assert Body.from_bytes(bytes(rows[0]["body"])).text == "team hello"
        assert rows[0]["kind"] == int(MessageKind.GROUP)