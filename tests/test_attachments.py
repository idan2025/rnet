import os
import tempfile

import RNS

from rnet.db.connection import Database
from rnet.errors import MessageError
from rnet.messaging import decrypt_attachment, encrypt_attachment
from rnet.storage import ContentStore, ManifestStore


def _stores(tmp, name="db"):
    db = Database(os.path.join(tmp, f"{name}.db"))
    return db, ContentStore(db, os.path.join(tmp, f"cas_{name}")), ManifestStore(db)


def test_attachment_encrypt_decrypt_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db, cas, ms = _stores(tmp, "a")
        recipient = RNS.Identity()
        data = b"large attachment payload " * 500  # > 1 chunk
        ref = encrypt_attachment(data, recipient, cas, ms, name="file.bin")
        assert ref.size == len(data)
        # recipient decrypts
        out = decrypt_attachment(ref, recipient, cas, ms)
        assert out == data


def test_attachment_only_recipient_can_decrypt():
    with tempfile.TemporaryDirectory() as tmp:
        db, cas, ms = _stores(tmp, "a")
        recipient = RNS.Identity()
        attacker = RNS.Identity()
        ref = encrypt_attachment(b"secret file", recipient, cas, ms, name="s")
        try:
            decrypt_attachment(ref, attacker, cas, ms)
            assert False, "attacker must not decrypt"
        except MessageError:
            pass


def test_attachment_ciphertext_in_cas_is_opaque():
    """CAS peers see only ciphertext; key is wrapped, not in CAS."""
    with tempfile.TemporaryDirectory() as tmp:
        db, cas, ms = _stores(tmp, "a")
        recipient = RNS.Identity()
        data = b"plaintext to hide " * 50
        ref = encrypt_attachment(data, recipient, cas, ms, name="f")
        manifest = ms.get(ref.manifest_hash)
        ct = b"".join(cas.get_block(c.hash) for c in manifest.chunks)
        assert data not in ct  # plaintext not present in CAS
        assert b"plaintext to hide" not in ct


def test_attachment_replicate_then_decrypt():
    """Recipient lacks chunks; replicates from a source store, then decrypts."""
    with tempfile.TemporaryDirectory() as tmp:
        # sender side
        dbs, cass, mss = _stores(tmp, "s")
        recipient = RNS.Identity()
        data = b"replicated attachment " * 100
        ref = encrypt_attachment(data, recipient, cass, mss, name="r")
        # recipient side: empty CAS, but manifest known + sender as chunk source
        dbr, casr, msr = _stores(tmp, "r")
        msr.put(mss.get(ref.manifest_hash))  # copy manifest index
        from rnet.storage import FakeChunkSource
        out = decrypt_attachment(ref, recipient, casr, msr,
                                 sources=[FakeChunkSource(cass)])
        assert out == data