import os
import tempfile

import RNS

from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, Profile, SignedProfile, fingerprint
from rnet.errors import SignatureError


def _make_mgr(tmp):
    db = Database(os.path.join(tmp, "rnet.db"))
    store = IdentityStore(db)
    keys = os.path.join(tmp, "keys")
    return IdentityManager(store, keys), db


def test_create_load_identity():
    with tempfile.TemporaryDirectory() as tmp:
        mgr, db = _make_mgr(tmp)
        ident = mgr.create("alice")
        fp = fingerprint(ident).hex()
        loaded = mgr.load(fp)
        assert loaded is not None
        assert fingerprint(loaded) == fingerprint(ident)
        assert len(mgr.list_own()) == 1


def test_profile_sign_verify():
    with tempfile.TemporaryDirectory() as tmp:
        mgr, db = _make_mgr(tmp)
        ident = mgr.create("bob")
        signed = mgr.make_profile(
            ident, name="bob", display="Bob", capabilities=["messaging", "storage"],
            bio="hi", node_dest_hash="deadbeef" * 4,
        )
        prof = IdentityManager.verify_profile(signed, ident)
        assert prof.name == "bob"
        assert "messaging" in prof.capabilities
        assert prof.fp == fingerprint(ident)

        # round-trip through bytes
        sp2 = SignedProfile.from_bytes(signed.to_bytes())
        prof2 = IdentityManager.verify_profile(sp2, ident)
        assert prof2.name == "bob"


def test_profile_tamper_detected():
    with tempfile.TemporaryDirectory() as tmp:
        mgr, db = _make_mgr(tmp)
        ident = mgr.create("carol")
        signed = mgr.make_profile(ident, name="carol")
        # flip a byte in the signed payload
        bad = SignedProfile(
            profile_bytes=bytearray(signed.profile_bytes),
            sig=signed.sig,
            fp=signed.fp,
        )
        bad.profile_bytes = bytes(bad.profile_bytes[:-1]) + bytes([bad.profile_bytes[-1] ^ 1])
        try:
            IdentityManager.verify_profile(bad, ident)
            assert False, "tampered profile must not verify"
        except SignatureError:
            pass


def test_profile_fp_mismatch_detected():
    with tempfile.TemporaryDirectory() as tmp:
        mgr, db = _make_mgr(tmp)
        a = mgr.create("a")
        b = mgr.create("b")
        signed = mgr.make_profile(a, name="a")
        try:
            IdentityManager.verify_profile(signed, b)
            assert False, "fp mismatch must raise"
        except SignatureError:
            pass


def test_profile_pubkey_verify():
    with tempfile.TemporaryDirectory() as tmp:
        mgr, db = _make_mgr(tmp)
        ident = mgr.create("d")
        signed = mgr.make_profile(ident, name="d")
        prof = IdentityManager.verify_profile_pubkey(signed, ident.get_public_key())
        assert prof.name == "d"


def test_profile_field_limits():
    with tempfile.TemporaryDirectory() as tmp:
        mgr, db = _make_mgr(tmp)
        ident = mgr.create("e")
        import pytest
        with pytest.raises(Exception):
            mgr.make_profile(ident, name="x" * 65)