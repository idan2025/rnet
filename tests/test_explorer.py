import os
import tempfile
import time

from rnet.db.connection import Database
from rnet.discovery import PeerRegistry
from rnet.explorer import ExplorerModel
from rnet.protocol.capabilities import Bandwidth, CapabilityAdvertisement


def _seed_peers(db):
    reg = PeerRegistry(db, stale_seconds=60)
    a = CapabilityAdvertisement(name="alpha", caps=["web", "messaging"],
                                fp=b"\x01" * 8, max_bw=int(Bandwidth.MEDIUM))
    b = CapabilityAdvertisement(name="beta", caps=["storage", "relay"],
                                fp=b"\x02" * 8, max_bw=int(Bandwidth.LOW), low_power=1)
    reg.upsert_from_announce(a, "aa" * 16, now=int(time.time()))
    reg.upsert_from_announce(b, "bb" * 16, now=int(time.time()) - 30)
    return reg


def test_summary_counts_and_histogram():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "db.db"))
        _seed_peers(db)
        model = ExplorerModel(db)
        s = model.summary()
        assert s["nodes"] == 2
        assert s["reachable"] == 2
        assert s["capabilities"]["web"] == 1
        assert s["capabilities"]["storage"] == 1
        assert s["capabilities"]["messaging"] == 1


def test_rtt_attached_and_services():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "db.db"))
        _seed_peers(db)
        model = ExplorerModel(db)
        model.set_rtt("aa" * 16, 320.0)
        peers = {p["dest_hash"]: p for p in model.peers()}
        assert peers["aa" * 16]["rtt_ms"] == 320.0
        assert peers["bb" * 16]["rtt_ms"] is None
        services = model.services()
        caps = {s["cap"] for s in services}
        assert {"web", "messaging", "storage", "relay"} <= caps


def test_stale_peer_marked_unreachable():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "db.db"))
        reg = PeerRegistry(db, stale_seconds=60)
        adv = CapabilityAdvertisement(name="old", caps=["messaging"], fp=b"\x03" * 8)
        reg.upsert_from_announce(adv, "cc" * 16, now=int(time.time()) - 1000)
        model = ExplorerModel(db)
        s = model.summary()
        assert s["reachable"] == 0