import asyncio
import os
import tempfile

import RNS

from rnet.browser import BrowserModel, Page
from rnet.db.connection import Database
from rnet.identity import IdentityManager, IdentityStore, fingerprint
from rnet.naming import NameRegistry, NamingService
from rnet.storage import ContentStore, ManifestStore
from rnet.web import FakeWebTransport, RHTTPServer, WebClient, OK


def _setup(tmp, host_name="library"):
    db = Database(os.path.join(tmp, "db.db"))
    idm = IdentityManager(IdentityStore(db), os.path.join(tmp, "k"))
    ident = idm.create(host_name)
    store = ContentStore(db, os.path.join(tmp, "cas"))
    ms = ManifestStore(db)
    naming = NamingService(NameRegistry(db), idm)
    return db, idm, ident, store, ms, naming


def test_normalize_url():
    assert BrowserModel.normalize_url("library.rns") == "rhttp://library.rns"
    assert BrowserModel.normalize_url("library.rns/books") == "rhttp://library.rns/books"
    assert BrowserModel.normalize_url("rhttp://library.rns/books") == "rhttp://library.rns/books"


def test_split_url():
    host, path = BrowserModel.split_url("rhttp://news.rns/feed/latest")
    assert host == "news.rns"
    assert path == "/feed/latest"
    host, path = BrowserModel.split_url("rhttp://news.rns")
    assert path == "/"


def test_navigate_resolves_fetches_verifies_caches():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, store, ms, naming = _setup(tmp)
        host_dest = fingerprint(ident).hex()
        # Make a tiny site.
        root = os.path.join(tmp, "site")
        os.makedirs(root)
        with open(os.path.join(root, "index.html"), "w") as f:
            f.write("<html><head><title>Library</title></head>"
                    "<body>welcome to the library</body></html>")
        srv = RHTTPServer(root, ident, store, ms)
        transport = FakeWebTransport()
        transport.register(host_dest, srv.handle_request)

        # Publish name + register host identity pubkey for the client.
        naming.publish(ident, "library", node_dest_hash=host_dest,
                       services=[{"cap": "web", "dest": host_dest}])
        idm.store.upsert_known(host_dest, fingerprint(ident), ident.get_public_key(),
                               "library", True)

        client_store = ContentStore(Database(os.path.join(tmp, "cdb.db")),
                                    os.path.join(tmp, "ccas"))
        client_ms = ManifestStore(Database(os.path.join(tmp, "cdb.db")))
        cidm = IdentityManager(IdentityStore(Database(os.path.join(tmp, "cdb.db"))),
                               os.path.join(tmp, "ck"))
        cidm.store.upsert_known(host_dest, fingerprint(ident), ident.get_public_key(),
                                "library", True)
        client_naming = NamingService(NameRegistry(Database(os.path.join(tmp, "cdb.db"))), cidm)
        # mirror the name record into the client's cache
        rec = naming.registry.get("library")
        client_naming.registry.put(rec)

        web = WebClient(transport, client_store, client_ms, cidm)
        model = BrowserModel(Database(os.path.join(tmp, "cdb.db")), cidm, web, client_naming)

        page = asyncio.run(model.navigate("library.rns"))
        assert page.error == ""
        assert page.status == OK
        assert "welcome to the library" in page.html
        assert page.verified is True
        assert page.title == "Library"
        # cached + history recorded
        assert model.cache_get("rhttp://library.rns") is not None
        assert any(h["url"] == "rhttp://library.rns" for h in model.history())


def test_normalize_url_dest_hash():
    # A 32-hex dest hash (16-byte RNS destination) is NOT a .rns name.
    d = "556687be305553bcc5fa0d5169b286fe"
    assert BrowserModel.normalize_url(d) == f"rhttp://{d}/"
    assert BrowserModel.normalize_url(d + "/about.html") == f"rhttp://{d}/about.html"
    assert BrowserModel.normalize_url("rhttp://" + d + "/") == f"rhttp://{d}/"


def test_navigate_by_dest_hash_skips_naming():
    """rhttp://<dest-hash>/ fetches directly without a published .rns name."""
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, store, ms, naming = _setup(tmp)
        host_dest = RNS.Destination.hash(ident, "rnet", "node").hex()
        root = os.path.join(tmp, "site")
        os.makedirs(root)
        with open(os.path.join(root, "index.html"), "w") as f:
            f.write("<html><head><title>Hashed</title></head>"
                    "<body>dest-hash host</body></html>")
        srv = RHTTPServer(root, ident, store, ms)
        transport = FakeWebTransport()
        transport.register(host_dest, srv.handle_request)
        # Client knows the host pubkey (as it would after receiving an announce).
        cidm = IdentityManager(IdentityStore(Database(os.path.join(tmp, "cdb.db"))),
                               os.path.join(tmp, "ck"))
        cidm.store.upsert_known(host_dest, fingerprint(ident), ident.get_public_key(),
                                "hashed", True)
        web = WebClient(transport, ContentStore(Database(os.path.join(tmp, "cdb.db")),
                                                os.path.join(tmp, "ccas")),
                        ManifestStore(Database(os.path.join(tmp, "cdb.db"))), cidm)
        model = BrowserModel(Database(os.path.join(tmp, "cdb.db")), cidm, web,
                             NamingService(NameRegistry(Database(os.path.join(tmp, "cdb.db"))), cidm))
        page = asyncio.run(model.navigate(f"rhttp://{host_dest}/"))
        assert page.error == ""
        assert page.status == OK
        assert "dest-hash host" in page.html
        assert page.verified is True
        assert page.title == "Hashed"


def test_navigate_unresolvable_name():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, store, ms, naming = _setup(tmp)
        web = WebClient(FakeWebTransport(), store, ms, idm)
        model = BrowserModel(db, idm, web, naming)
        page = asyncio.run(model.navigate("ghost.rns"))
        assert page.error != ""
        assert "resolve" in page.error


def test_bookmarks_and_back_forward():
    with tempfile.TemporaryDirectory() as tmp:
        db, idm, ident, store, ms, naming = _setup(tmp)
        web = WebClient(FakeWebTransport(), store, ms, idm)
        model = BrowserModel(db, idm, web, naming)
        model.add_bookmark("rhttp://a.rns", "A")
        model.add_bookmark("rhttp://b.rns", "B")
        bms = model.bookmarks()
        assert len(bms) == 2
        model.remove_bookmark("rhttp://a.rns")
        assert len(model.bookmarks()) == 1
        # simulate navigation stack
        model._push_history("rhttp://x.rns")
        model._push_history("rhttp://y.rns")
        assert model.can_back()
        assert model.back_url() == "rhttp://x.rns"
        assert model.can_forward()