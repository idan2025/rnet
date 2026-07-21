import asyncio
import os
import tempfile

import RNS

from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, fingerprint
from rnet.storage import ContentStore, FakeChunkSource, ManifestStore
from rnet.web import (
    BAD_REQUEST,
    FakeWebTransport,
    NOT_FOUND,
    OK,
    RHTTPRequest,
    RHTTPResponse,
    RHTTPServer,
    WebClient,
    META,
    GET,
)
from rnet.web.protocol import INLINE_BODY_MAX
from rnet.storage.cas import hash_data


def _site(tmp):
    root = os.path.join(tmp, "site")
    os.makedirs(root)
    with open(os.path.join(root, "index.html"), "w") as f:
        f.write("<html><body>RNet</body></html>")
    with open(os.path.join(root, "style.css"), "w") as f:
        f.write("body{color:#000}")
    os.makedirs(os.path.join(root, "images"))
    with open(os.path.join(root, "images", "big.bin"), "wb") as f:
        f.write(os.urandom(INLINE_BODY_MAX + 4096))  # force CAS path
    return root


def _server(tmp, name="host"):
    db = Database(os.path.join(tmp, f"{name}.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, f"k_{name}"))
    ident = idm.create(name)
    store = ContentStore(db, os.path.join(tmp, f"cas_{name}"))
    ms = ManifestStore(db)
    return db, idm, ident, store, ms


def test_request_response_roundtrip():
    req = RHTTPRequest(method="GET", path="/books", query={"q": "mesh"},
                       headers={"Accept": "text/html"})
    back = RHTTPRequest.from_bytes(req.to_bytes())
    assert back.method == "GET" and back.path == "/books"
    resp = RHTTPResponse(status=OK, content_hash=b"\x01" * 32, size=4000, body=b"x")
    assert RHTTPResponse.from_bytes(resp.to_bytes()).status == OK


def test_response_signature():
    ident = RNS.Identity()
    resp = RHTTPResponse(status=OK, content_hash=b"\x02" * 32, size=10, body=b"hi")
    resp.sign(ident)
    assert resp.verify(ident)
    # tamper
    resp.status = 404
    assert resp.verify(ident) is False


def test_server_serves_small_file_inline():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp)
        srv = RHTTPServer(root, ident, store, ms, inline_max=INLINE_BODY_MAX)
        resp = srv.handle_request(RHTTPRequest(method=GET, path="/index.html"))
        assert resp.status == OK
        assert b"RNet" in resp.body
        assert hash_data(resp.body) == resp.content_hash
        assert resp.verify(ident)


def test_server_404_and_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp)
        srv = RHTTPServer(root, ident, store, ms)
        assert srv.handle_request(RHTTPRequest(path="/nope.html")).status == NOT_FOUND
        # traversal attempt must be blocked
        resp = srv.handle_request(RHTTPRequest(path="../../../../etc/passwd"))
        assert resp.status == NOT_FOUND


def test_server_large_file_uses_cas_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp)
        srv = RHTTPServer(root, ident, store, ms, inline_max=INLINE_BODY_MAX)
        resp = srv.handle_request(RHTTPRequest(path="/images/big.bin"))
        assert resp.status == OK
        assert resp.body == b""  # not inline
        manifest = ms.get(resp.content_hash)
        assert manifest is not None
        from rnet.storage.cas import assemble
        data = assemble(manifest, store)
        assert len(data) == resp.size


def test_server_range_resume():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp)
        srv = RHTTPServer(root, ident, store, ms)
        resp = srv.handle_request(RHTTPRequest(path="/style.css", range=[2, 6]))
        assert resp.status == OK
        assert resp.body == b"dy{co"
        assert resp.headers["Content-Range"].startswith("2-6/")


def test_server_meta_returns_no_body():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp)
        srv = RHTTPServer(root, ident, store, ms)
        resp = srv.handle_request(RHTTPRequest(method=META, path="/index.html"))
        assert resp.status == OK
        assert resp.body == b""
        assert resp.size > 0


def test_server_rejects_unknown_method():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp)
        srv = RHTTPServer(root, ident, store, ms)
        assert srv.handle_request(RHTTPRequest(method="DELETE", path="/x")).status == BAD_REQUEST


def test_client_get_inline_then_cas():
    """End-to-end: server hosts site; client fetches small + large file."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp, "host")
        host_dest = fingerprint(ident).hex()
        srv = RHTTPServer(root, ident, store, ms, inline_max=INLINE_BODY_MAX)

        transport = FakeWebTransport()
        transport.register(host_dest, srv.handle_request)

        # Client side
        cdb = Database(os.path.join(tmp, "client.db"))
        cidm = IdentityManager(IdentityStore(cdb), os.path.join(tmp, "ck"))
        cstore = ContentStore(cdb, os.path.join(tmp, "ccas"))
        cms = ManifestStore(cdb)
        client = WebClient(transport, cstore, cms, cidm)

        # register host pubkey so client can verify
        cidm.store.upsert_known(host_dest, fingerprint(ident), ident.get_public_key(),
                                "host", True)
        # small file
        resp = asyncio.run(client.get(host_dest, "/index.html",
                                      host_pubkey=ident.get_public_key()))
        assert resp is not None and resp.status == OK
        assert b"RNet" in resp.body
        # large file: client needs the manifest; server's store is a chunk source
        resp2 = asyncio.run(client.get(host_dest, "/images/big.bin",
                                       host_pubkey=ident.get_public_key(),
                                       sources=[FakeChunkSource(store)]))
        assert resp2 is not None and resp2.status == OK
        assert len(resp2.body) == resp2.size
        # content hash of assembled body matches response.content_hash? No:
        # for CAS responses content_hash is the manifest hash, not body hash.
        # Instead verify by re-assembling via the manifest the client cached.
        manifest = cms.get(resp2.content_hash)
        from rnet.storage.cas import assemble
        assert assemble(manifest, cstore) == resp2.body


def test_client_rejects_forged_signature():
    with tempfile.TemporaryDirectory() as tmp:
        root = _site(tmp)
        db, idm, ident, store, ms = _server(tmp, "host")
        host_dest = fingerprint(ident).hex()
        srv = RHTTPServer(root, ident, store, ms)
        # A different host identity signs the response (forgery).
        attacker = RNS.Identity()
        bad_srv = RHTTPServer(root, attacker, store, ms)
        transport = FakeWebTransport()
        transport.register(host_dest, bad_srv.handle_request)

        cdb = Database(os.path.join(tmp, "c.db"))
        cidm = IdentityManager(IdentityStore(cdb), os.path.join(tmp, "ck"))
        cstore = ContentStore(cdb, os.path.join(tmp, "cc"))
        cms = ManifestStore(cdb)
        client = WebClient(transport, cstore, cms, cidm)
        resp = asyncio.run(client.get(host_dest, "/index.html",
                                      host_pubkey=ident.get_public_key()))
        assert resp is None  # signature mismatch -> rejected