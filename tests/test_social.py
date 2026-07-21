import asyncio
import os
import tempfile
import time

import RNS

from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, fingerprint
from rnet.social import (
    FakePostSource,
    Follow,
    FollowStore,
    Post,
    PostStore,
    SocialService,
)
from rnet.storage import ContentStore


def _setup(tmp, name="db"):
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"k_{name}"))
    cas = ContentStore(db, os.path.join(tmp, f"cas_{name}"))
    return db, idm, cas, PostStore(db, cas), FollowStore(db), SocialService(PostStore(db, cas), FollowStore(db), idm)


def _register(idm, ident, fp_hex, name):
    idm.store.upsert_known(fp_hex, fingerprint(ident), ident.get_public_key(), name, True)


def test_post_sign_verify_content_addressed():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, cas, posts, follows, svc = _setup(tmp)
        author = idm.create("alice")
        fp = fingerprint(author).hex()
        _register(idm, author, fp, "alice")
        post = svc.publish_post(author, "hello mesh")
        assert post.author == fp
        post.verify(author)
        # content-addressed: stored as a CAS block keyed by post hash
        h = post.hash
        assert cas.get_block(h) is not None
        # round-trip
        loaded = posts.get(h)
        assert loaded is not None
        assert loaded.body == "hello mesh"
        assert loaded.author == fp


def test_post_tamper_detected():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, cas, posts, follows, svc = _setup(tmp)
        author = idm.create("bob")
        _register(idm, author, fingerprint(author).hex(), "bob")
        post = svc.publish_post(author, "original")
        bad = Post.from_bytes(post.to_bytes())
        bad.body = "forged"
        from rnet.errors import SignatureError
        try:
            bad.verify(author)
            assert False
        except SignatureError:
            pass


def test_follow_and_feed():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, cas, posts, follows, svc = _setup(tmp)
        alice = idm.create("alice")
        bob = idm.create("bob")
        afp = fingerprint(alice).hex()
        bfp = fingerprint(bob).hex()
        _register(idm, alice, afp, "alice")
        _register(idm, bob, bfp, "bob")
        # alice follows bob; bob posts
        svc.follow(alice, bfp)
        assert svc.following(afp) == [bfp]
        svc.publish_post(bob, "bob post 1", ts=1000)
        svc.publish_post(bob, "bob post 2", ts=2000)
        feed = svc.feed(afp)
        assert len(feed) == 2
        assert feed[0]["body"] == "bob post 2"  # newest first
        # unfollow
        svc.unfollow(afp, bfp)
        assert svc.feed(afp) == []


def test_ingest_verifies_signature():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, cas, posts, follows, svc = _setup(tmp)
        author = idm.create("carol")
        afp = fingerprint(author).hex()
        _register(idm, author, afp, "carol")
        post = svc.publish_post(author, "from carol")
        # Client side: fresh store, same identity cache.
        db2 = Database(os.path.join(tmp, "c2.db"))
        idm2 = IdentityManager(IdentityStore(db2), os.path.join(tmp, "k2"))
        cas2 = ContentStore(db2, os.path.join(tmp, "cas2"))
        _register(idm2, author, afp, "carol")
        svc2 = SocialService(PostStore(db2, cas2), FollowStore(db2), idm2)
        ingested = svc2.ingest(post.to_bytes())
        assert ingested is not None
        assert ingested.body == "from carol"
        # forged post rejected
        forged = Post.from_bytes(post.to_bytes())
        forged.body = "fake"
        forged.sig = b"\x00" * 64
        assert svc2.ingest(forged.to_bytes()) is None


def test_pull_feed_from_source():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, cas, posts, follows, svc = _setup(tmp, "a")
        alice = idm.create("alice")
        bob = idm.create("bob")
        afp = fingerprint(alice).hex()
        bfp = fingerprint(bob).hex()
        _register(idm, alice, afp, "alice")
        _register(idm, bob, bfp, "bob")
        svc.publish_post(bob, "bob 1", ts=100)
        svc.publish_post(bob, "bob 2", ts=200)
        svc.follow(alice, bfp)

        # Client side pulls from a FakePostSource backed by bob's store.
        db2 = Database(os.path.join(tmp, "c2.db"))
        idm2 = IdentityManager(IdentityStore(db2), os.path.join(tmp, "k2"))
        cas2 = ContentStore(db2, os.path.join(tmp, "cas2"))
        _register(idm2, bob, bfp, "bob")
        svc2 = SocialService(PostStore(db2, cas2), FollowStore(db2), idm2)
        svc2.follow(alice, bfp)  # alice (same fp) follows bob on client too
        src = FakePostSource(posts, bfp)
        n = asyncio.run(svc2.pull_feed(afp, sources=[src]))
        assert n == 2
        feed = svc2.feed(afp)
        assert len(feed) == 2


def test_thread_replies():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, cas, posts, follows, svc = _setup(tmp)
        author = idm.create("d")
        _register(idm, author, fingerprint(author).hex(), "d")
        root = svc.publish_post(author, "root", ts=100)
        svc.publish_post(author, "reply1", reply_to=root.hash, ts=200)
        svc.publish_post(author, "reply2", reply_to=root.hash, ts=300)
        thread = svc.thread(root.hash)
        assert len(thread) == 3
        assert thread[0].body == "root"


def test_follow_record_roundtrip():
    a = RNS.Identity(); b = RNS.Identity()
    f = Follow(followed=fingerprint(b).hex(), ts=123)
    f.sign(a)
    back = Follow.from_bytes(f.to_bytes())
    back.verify(a)
    assert back.followed == fingerprint(b).hex()