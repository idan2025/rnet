import os
import tempfile
import time

import RNS
import pytest

from rnet.config import NodeConfig
from rnet.core import EventBus, Node
from rnet.db.connection import Database
from rnet.discovery import PeerRegistry, ServiceDiscovery, NODE_ASPECT
from rnet.identity import IdentityManager, IdentityStore
from rnet.protocol.capabilities import Bandwidth, CapabilityAdvertisement


def _db(tmp):
    return Database(os.path.join(tmp, "rnet.db"))


def _idm(tmp, db):
    return IdentityManager(IdentityStore(db), os.path.join(tmp, "keys"))


def test_registry_upsert_and_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        reg = PeerRegistry(db, stale_seconds=60)
        adv = CapabilityAdvertisement(name="forest-node",
                                      caps=["web", "messaging", "relay"],
                                      fp=b"\x01" * 8, max_bw=int(Bandwidth.MEDIUM))
        reg.upsert_from_announce(adv, "aabb" * 8, now=1000)
        peers = reg.list_all()
        assert len(peers) == 1
        assert peers[0]["name"] == "forest-node"
        assert "messaging" in peers[0]["capabilities"]
        assert reg.list_by_capability("storage") == []
        assert len(reg.list_by_capability("web")) == 1


def test_registry_prune_stale():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        reg = PeerRegistry(db, stale_seconds=10)
        adv = CapabilityAdvertisement(name="n", caps=["messaging"], fp=b"x" * 8)
        reg.upsert_from_announce(adv, "11" * 16, now=1000)
        # far in the future -> stale
        n = reg.prune_stale(now=2000)
        assert n == 1
        p = reg.get("11" * 16)
        assert p["reachable"] == 0


def test_build_capadv_size_limit():
    bus = EventBus()
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idm = _idm(tmp, db)
        sd = ServiceDiscovery(bus, PeerRegistry(db), idm)
        adv = sd.build_capadv(name="n", caps=["messaging"], profile_sig=b"\x00" * 64,
                              fp=b"\x00" * 8, ts=1, max_bw=int(Bandwidth.LOW),
                              low_power=False)
        assert len(adv.to_bytes()) <= 223


def test_handle_announce_parses_and_persists():
    bus = EventBus()
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idm = _idm(tmp, db)
        reg = PeerRegistry(db)
        sd = ServiceDiscovery(bus, reg, idm)

        # Simulate a remote node: identity + its capability ad.
        remote = RNS.Identity()
        remote_fp = RNS.Identity.full_hash(remote.get_public_key())[:8]
        adv = CapabilityAdvertisement(
            name="remote", caps=["messaging", "storage"], fp=remote_fp,
            max_bw=int(Bandwidth.MEDIUM), low_power=1,
        )
        # Compute a dest hash the way RNS would (app "rnet", aspect "node").
        dest_hash = RNS.Destination.hash(remote, "rnet", "node")

        sd.handle_announce(dest_hash, remote, adv.to_bytes())

        peers = reg.list_all()
        assert len(peers) == 1
        assert peers[0]["name"] == "remote"
        known = idm.store.get_known(dest_hash.hex())
        assert known is not None
        assert known["verified"] == 1
        assert known["name"] == "remote"


@pytest.mark.asyncio
async def test_node_start_announce_and_stop():
    with tempfile.TemporaryDirectory() as tmp:
        rns_dir = os.path.join(tmp, "rns")
        os.makedirs(rns_dir)
        datadir = os.path.join(tmp, "data")
        os.makedirs(datadir)
        cfg = NodeConfig(
            name="test-node",
            capabilities=["messaging", "relay"],
            rns_configdir=rns_dir,
            datadir=datadir,
            announce_interval=100000,  # don't re-announce during test
            low_power=False,
            max_bandwidth=int(Bandwidth.MEDIUM),
        )
        db = Database(os.path.join(datadir, "rnet.db"))
        idm = IdentityManager(IdentityStore(db), os.path.join(datadir, "keys"))
        ident = idm.create("test-node", is_node=True)
        node = Node(cfg, ident, db, identity_manager=idm)
        await node.start()
        try:
            assert node.running
            assert node.node_dest_hash is not None
            assert len(node.node_dest_hash) == 32  # 16 bytes hex
            # self should be in known identities
            known = idm.store.get_known(node.node_dest_hash)
            assert known["name"] == "test-node"
            # capability ad builds within budget
            adv_bytes = node._build_capadv()
            assert len(adv_bytes) <= 223
            adv = CapabilityAdvertisement.from_bytes(adv_bytes)
            assert "messaging" in adv.caps
        finally:
            await node.stop()
        assert not node.running