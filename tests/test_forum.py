import os
import tempfile

import RNS

from rnet.apps import AppManifest, ForumApp, RNet
from rnet.apps.forum import ForumApp as FA


def _sdk(tmp, name="forum"):
    import RNS
    if RNS.Reticulum.get_instance() is None:
        rns_dir = os.path.join(tmp, f"rns_{name}")
        os.makedirs(rns_dir, exist_ok=True)
        RNS.Reticulum(rns_dir)
    from rnet.config import NodeConfig
    from rnet.core.events import EventBus, LoopBridge
    from rnet.db.connection import Database
    from rnet.discovery import PeerRegistry
    from rnet.identity import IdentityManager, IdentityStore
    import asyncio
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"k_{name}"))
    ident = idm.create(name, is_node=True)
    cfg = NodeConfig(name=name, datadir=tmp)
    bus = EventBus(); loop = asyncio.new_event_loop(); bus.bind(loop)
    bridge = LoopBridge(loop, bus)
    return RNet(cfg, ident, db, idm, bus, bridge, registry=PeerRegistry(db)), idm, ident


def test_forum_post_thread_recent():
    with tempfile.TemporaryDirectory() as tmp:
        sdk, idm, ident = _sdk(tmp)
        author = idm.create("alice")
        from rnet.identity import fingerprint
        idm.store.upsert_known(fingerprint(author).hex(), fingerprint(author),
                               author.get_public_key(), "alice", True)
        forum = ForumApp(community_dest_hash="comm" * 16, name="forum")
        sdk.register_service(forum)
        root = forum.post(author, "thread root", ts=100)
        reply = forum.post(author, "a reply", reply_to=root.hash, ts=200)
        reply2 = forum.post(author, "second reply", reply_to=root.hash, ts=300)
        # recent lists community posts newest first
        recent = forum.recent()
        assert len(recent) == 3
        assert recent[0]["body"] == "second reply"
        # thread = root + replies in order
        thread = forum.thread(root.hash)
        assert [p.body for p in thread] == ["thread root", "a reply", "second reply"]
        sdk.stop_apps()


def test_forum_handle_request_post_and_recent():
    with tempfile.TemporaryDirectory() as tmp:
        sdk, idm, ident = _sdk(tmp)
        author = idm.create("bob")
        from rnet.identity import fingerprint
        idm.store.upsert_known(fingerprint(author).hex(), fingerprint(author),
                               author.get_public_key(), "bob", True)
        forum = ForumApp(community_dest_hash="c" * 32, name="board")
        sdk.register_service(forum)
        root = forum.post(author, "via api", ts=10)
        # peer submits a reply via request handler
        reply_bytes = forum.post(author, "peer reply", reply_to=root.hash, ts=20).to_bytes()
        # ingest a fresh post via handler
        new_post = forum.social.publish_post(author, "handler post", community=forum.community, ts=30)
        resp = forum.handle_request("post", new_post.to_bytes())
        import msgpack
        d = msgpack.unpackb(resp, raw=False)
        assert d["ok"] is True
        # recent via handler
        resp = forum.handle_request("recent", b"10")
        rows = msgpack.unpackb(resp, raw=False)
        assert any(r["body"] == "handler post" for r in rows)
        # thread via handler
        resp = forum.handle_request("thread", root.hash)
        posts = msgpack.unpackb(resp, raw=False)
        assert len(posts) >= 1
        sdk.stop_apps()