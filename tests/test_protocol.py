import os
import tempfile
import time

import RNS

from rnet.db.connection import Database
from rnet.protocol import (
    Bandwidth,
    Body,
    CapabilityAdvertisement,
    CapabilitySet,
    Envelope,
    FragmentSpec,
    Frame,
    FrameType,
    MessageKind,
    PROTOCOL_VERSION,
    PRIORITY_CONTROL,
    PRIORITY_BULK,
    Receipt,
    Reassembler,
    ReplayWindow,
    SignedFrame,
    build_signed_frame,
    fragment,
    pack_frame,
    pack_signed_frame,
    unpack_frame,
    unpack_signed_frame,
)
from rnet.protocol.fragment import FragmentHeader
from rnet.errors import ReplayError, WireError


def _ident():
    return RNS.Identity()


# -- wire --------------------------------------------------------------------
def test_frame_roundtrip_compressed():
    payload = b"x" * 1000  # big => compressed
    f = Frame(version=PROTOCOL_VERSION, type=int(FrameType.MESSAGE), seq=5,
              ts=int(time.time()), payload=payload, compressed=True)
    raw = pack_frame(f)
    back = unpack_frame(raw)
    assert back.type == FrameType.MESSAGE
    assert back.seq == 5
    assert back.priority == 1
    assert back.compressed is True


def test_signed_frame_signs_compressed_bytes():
    ident = _ident()
    sf = build_signed_frame(
        FrameType.MESSAGE, seq=1, ts=int(time.time()),
        payload=b"hello " * 100, sign_fn=ident.sign, priority=PRIORITY_BULK,
    )
    raw = pack_signed_frame(sf)
    back = unpack_signed_frame(raw)
    assert ident.validate(back.sig, pack_frame(back.frame)) is True
    assert back.frame.priority == PRIORITY_BULK
    assert back.frame.compressed is True  # big payload compressed


def test_signed_frame_tamper_fails_verify():
    ident = _ident()
    sf = build_signed_frame(FrameType.MESSAGE, seq=1, ts=int(time.time()),
                            payload=b"small", sign_fn=ident.sign)
    # Forge a frame with a bad signature (valid 64 bytes, wrong content).
    forged = SignedFrame(frame=sf.frame, sig=os.urandom(64), fp=sf.fp)
    back = unpack_signed_frame(pack_signed_frame(forged))
    assert ident.validate(back.sig, pack_frame(back.frame)) is False


def test_bad_frame_version_rejected():
    import msgpack
    raw = msgpack.packb({"v": 99, "t": 0, "n": 0, "ts": 0, "p": b"", "c": 0, "pr": 1})
    try:
        unpack_frame(raw)
        assert False
    except WireError:
        pass


def test_bad_signature_length_rejected():
    import msgpack
    f = Frame(version=PROTOCOL_VERSION, type=0, seq=0, ts=0, payload=b"")
    raw = msgpack.packb([pack_frame(f), b"short", b""])
    try:
        unpack_signed_frame(raw)
        assert False
    except WireError:
        pass


# -- capabilities ------------------------------------------------------------
def test_capability_ad_roundtrip():
    cap = CapabilityAdvertisement(
        name="forest-node", caps=["web", "messaging", "relay"],
        prof_sig=b"\x01" * 64, fp=b"\x02" * 8, ts=123, max_bw=int(Bandwidth.MEDIUM),
        low_power=0,
    )
    back = CapabilityAdvertisement.from_bytes(cap.to_bytes())
    assert back.name == "forest-node"
    assert set(back.caps) == {"web", "messaging", "relay"}
    assert back.max_bandwidth() == Bandwidth.MEDIUM
    assert back.caps_set().max_bandwidth() == Bandwidth.MEDIUM


def test_capability_bandwidth_defaults():
    cs = CapabilitySet(["messaging", "web", "video"])
    assert cs.bandwidth("messaging") == Bandwidth.LOW
    assert cs.bandwidth("web") == Bandwidth.MEDIUM
    # unknown token defaults to MEDIUM
    assert cs.bandwidth("video") == Bandwidth.MEDIUM
    assert cs.max_bandwidth() == Bandwidth.MEDIUM


def test_low_power_flag_roundtrips():
    cap = CapabilityAdvertisement(name="solar", caps=["messaging"], low_power=1)
    back = CapabilityAdvertisement.from_bytes(cap.to_bytes())
    assert back.low_power == 1


# -- envelope ----------------------------------------------------------------
def test_envelope_body_roundtrip():
    body = Body(text="hi over the mesh", files=[{"hash": b"a" * 32, "size": 10, "name": "f"}],
                reply=b"b" * 16, bw=int(Bandwidth.LOW))
    env = Envelope(sender="a" * 16, recipient="b" * 16, kind=int(MessageKind.DM),
                   id=Envelope.new_id(), ts=int(time.time()),
                   ciphertext=b"encrypted", nonce=b"n" * 16)
    assert Envelope.from_bytes(env.to_bytes()).message_id_hex() == env.message_id_hex()
    assert Body.from_bytes(body.to_bytes()).text == "hi over the mesh"


def test_receipt_signing_bytes_stable():
    r = Receipt(message_id=b"m" * 16, by="a" * 16, ts=42, sig=b"")
    assert Receipt.from_bytes(r.to_bytes()).signing_bytes() == r.signing_bytes()


# -- fragmentation -----------------------------------------------------------
def test_fragment_reassemble_roundtrip():
    payload = os.urandom(500)
    frags = fragment(payload, FragmentSpec(fragment_size=80))
    assert len(frags) == 7  # 500/80 = 6.25 -> 7
    assert frags[-1].final is True
    re = Reassembler()
    out = b""
    # deliver out of order
    import random
    rng = random.Random(1)
    order = list(range(len(frags)))
    rng.shuffle(order)
    completed = None
    for i in order:
        completed = re.add(frags[i])
    assert completed == payload


def test_fragment_missing_for_resume():
    payload = os.urandom(300)
    frags = fragment(payload, FragmentSpec(fragment_size=100))
    assert len(frags) == 3
    re = Reassembler()
    re.add(frags[0])
    re.add(frags[2])
    assert re.missing(frags[0].transfer_id) == [1]
    # and resuming with the missing fragment completes the transfer
    assert re.add(frags[1]) == payload


def test_fragment_header_roundtrip():
    h = FragmentHeader(transfer_id=b"t" * 16, index=2, total=5, payload=b"abc", final=False)
    back = FragmentHeader.from_bytes(h.to_bytes())
    assert back.index == 2 and back.total == 5 and back.payload == b"abc"


# -- replay ------------------------------------------------------------------
def test_replay_window_accepts_then_rejects():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "rnet.db"))
        rw = ReplayWindow(db, window=8, clock_skew=0)
        now = 1000
        mk = lambda n: Frame(version=PROTOCOL_VERSION, type=0, seq=n, ts=now, payload=b"")

        rw.check_and_remember("peerA", mk(10), now=now)
        # replay
        try:
            rw.check_and_remember("peerA", mk(10), now=now)
            assert False
        except ReplayError:
            pass
        # below window
        rw.check_and_remember("peerA", mk(11), now=now)
        rw.check_and_remember("peerA", mk(12), now=now)
        try:
            rw.check_and_remember("peerA", mk(2), now=now)
            assert False
        except ReplayError:
            pass
        # independent sender
        rw.check_and_remember("peerB", mk(10), now=now)


def test_replay_clock_skew_rejects_stale():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "rnet.db"))
        rw = ReplayWindow(db, window=8, clock_skew=300)
        now = 100000
        stale = Frame(version=PROTOCOL_VERSION, type=0, seq=1, ts=now - 100000, payload=b"")
        try:
            rw.check_and_remember("peerA", stale, now=now)
            assert False
        except ReplayError:
            pass