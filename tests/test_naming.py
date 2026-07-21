import asyncio
import os
import tempfile

import RNS

from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore
from rnet.naming import (
    FakeNameSource,
    NameRecord,
    NameRegistry,
    NamingService,
    is_transfer,
)
from rnet.errors import SignatureError


def _setup(tmp, name="db"):
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"keys_{name}"))
    return db, idm, NameRegistry(db), NamingService(NameRegistry(db), idm)


def _register(idm, ident, dest_hash, name):
    from rnet.identity import fingerprint
    idm.store.upsert_known(dest_hash, fingerprint(ident), ident.get_public_key(),
                           name, True)


def test_publish_sign_verify_cache():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        owner = idm.create("alice")
        from rnet.identity import fingerprint
        dest = fingerprint(owner).hex()
        _register(idm, owner, dest, "alice")

        rec = svc.publish(owner, "library", node_dest_hash="n" * 32,
                          services=[{"cap": "web", "dest": "d" * 32}], seq=1)
        assert rec.fp == __import__("rnet").identity.fingerprint(owner)
        # verify directly
        rec.verify(owner)
        # cached
        cached = reg.get("library")
        assert cached is not None
        assert cached.owner == dest
        assert reg.is_stale("library") is False


def test_resolve_cache_hit():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        owner = idm.create("bob")
        from rnet.identity import fingerprint
        _register(idm, owner, fingerprint(owner).hex(), "bob")
        svc.publish(owner, "news", node_dest_hash="n" * 32,
                    services=[{"cap": "web", "dest": "x" * 32}])
        resolved = asyncio.run(svc.resolve_name("news.rns"))
        assert resolved is not None
        assert resolved.name == "news"


def test_resolve_via_source_and_verifies():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        owner = idm.create("carol")
        from rnet.identity import fingerprint
        _register(idm, owner, fingerprint(owner).hex(), "carol")
        rec = svc.publish(owner, "forum", node_dest_hash="n" * 32,
                          services=[{"cap": "web", "dest": "y" * 32}])
        # Fresh client with empty cache but same identity cache.
        db2 = Database(os.path.join(tmp, "c2.db"))
        idm2 = IdentityManager(IdentityStore(db2), os.path.join(tmp, "k2"))
        _register(idm2, owner, fingerprint(owner).hex(), "carol")
        reg2 = NameRegistry(db2)
        svc2 = NamingService(reg2, idm2)
        src = FakeNameSource({rec.name: rec.to_bytes()})
        out = asyncio.run(svc2.resolve_name("forum", sources=[src]))
        assert out is not None
        assert out.name == "forum"
        assert reg2.get("forum") is not None  # cached after verify


def test_resolve_rejects_forged_record():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        owner = idm.create("dave")
        attacker = idm.create("eve")
        from rnet.identity import fingerprint
        _register(idm, owner, fingerprint(owner).hex(), "dave")
        _register(idm, attacker, fingerprint(attacker).hex(), "eve")
        # Attacker forges a record for a name owned by dave, signed by eve.
        forged = NameRecord(name="davelib", owner=fingerprint(owner).hex(),
                            node="n" * 32, services=[{"cap": "web", "dest": "z" * 32}],
                            seq=5, ts=svc.registry.db.now(), ttl=3600)
        forged.sign(attacker)  # wrong signer
        src = FakeNameSource({forged.name: forged.to_bytes()})
        out = asyncio.run(svc.resolve_name("davelib", sources=[src]))
        assert out is None  # verification fails -> not cached/returned


def test_seq_monotonic_higher_wins():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        owner = idm.create("frank")
        from rnet.identity import fingerprint
        _register(idm, owner, fingerprint(owner).hex(), "frank")
        r1 = svc.publish(owner, "shop", node_dest_hash="n" * 32, services=[], seq=1)
        r2 = svc.publish(owner, "shop", node_dest_hash="n" * 32,
                         services=[{"cap": "web", "dest": "w" * 32}], seq=3)
        cached = reg.get("shop")
        assert cached.seq == 3
        # lower seq rejected
        assert reg.put(r1) is False


def test_transfer_chains_via_prev():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        old = idm.create("old")
        new = idm.create("new")
        from rnet.identity import fingerprint
        _register(idm, old, fingerprint(old).hex(), "old")
        _register(idm, new, fingerprint(new).hex(), "new")
        r1 = svc.publish(old, "brand", node_dest_hash="n" * 32, services=[], seq=1)
        r2 = svc.transfer(new, r1, node_dest_hash="m" * 32,
                          services=[{"cap": "web", "dest": "q" * 32}])
        assert is_transfer(r2, r1)
        assert r2.prev == r1.fp
        r2.verify(new)
        cached = reg.get("brand")
        assert cached.seq == 2
        assert cached.owner == fingerprint(new).hex()


def test_record_tamper_detected():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, reg, svc = _setup(tmp)
        owner = idm.create("g")
        from rnet.identity import fingerprint
        _register(idm, owner, fingerprint(owner).hex(), "g")
        rec = svc.publish(owner, "x", node_dest_hash="n" * 32, services=[], seq=1)
        bad = NameRecord.from_bytes(rec.to_bytes())
        bad.node = "z" * 32  # mutate after signing
        try:
            bad.verify(owner)
            assert False
        except SignatureError:
            pass