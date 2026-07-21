import asyncio
import os
import tempfile

from rnet.db.connection import Database
from rnet.search import (
    Crawler,
    FakePageFetcher,
    FakeSearchSource,
    Indexer,
    SearchQuery,
    SearchResults,
    SearchService,
    merge_results,
    parse_html,
    tokenize,
)


def _db(tmp, name="db"):
    return Database(os.path.join(tmp, f"{name}.db"))


def test_tokenize_drops_stopwords_and_normalizes():
    toks = tokenize("The cats are running on the mesh networks")
    assert "cat" in toks and "mesh" in toks and "network" in toks
    assert "the" not in toks and "are" not in toks


def test_indexer_index_and_query():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idx = Indexer(db)
        idx.index("rhttp://news.rns/", "news.rns", "Mesh News",
                   "LoRa mesh networks deliver messages over radio")
        idx.index("rhttp://library.rns/books", "library.rns", "Books",
                   "Books about delay tolerant networking and mesh routing")
        res = idx.query(tokenize("mesh network"))
        assert any(r["url"] == "rhttp://news.rns/" for r in res)
        # both docs mention mesh/network-ish; ranking by matched terms
        urls = [r["url"] for r in res]
        assert "rhttp://news.rns/" in urls


def test_indexer_remove():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idx = Indexer(db)
        idx.index("rhttp://x.rns/", "x.rns", "X", "topic alpha beta")
        assert idx.query(tokenize("alpha"))
        idx.remove("rhttp://x.rns/")
        assert idx.query(tokenize("alpha")) == []


def test_parse_html_extracts_title_text_links():
    html = "<html><head><title>Home</title></head><body><p>Hello mesh</p>" \
           "<a href='/page'>page</a><a href='rhttp://other.rns/x'>other</a></body></html>"
    title, text, links = parse_html(html, base_url="rhttp://host.rns")
    assert title == "Home"
    assert "Hello mesh" in text
    assert "rhttp://host.rns/page" in links
    assert "rhttp://other.rns/x" in links


def test_crawler_indexes_and_follows_links():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idx = Indexer(db)
        pages = {
            "rhttp://site.rns/": "<html><head><title>Home</title></head>"
                "<body>welcome to the mesh home <a href='/about'>about</a></body></html>",
            "rhttp://site.rns/about": "<html><head><title>About</title></head>"
                "<body>about the mesh network project</body></html>",
        }
        fetcher = FakePageFetcher(pages)
        cr = Crawler(db, idx, fetcher, max_pages=10)
        cr.seed(["rhttp://site.rns/"])
        n = asyncio.run(cr.crawl())
        assert n == 2
        docs = {d["url"] for d in idx.list_documents()}
        assert "rhttp://site.rns/" in docs
        assert "rhttp://site.rns/about" in docs
        res = idx.query(tokenize("mesh network"))
        assert len(res) >= 1


def test_crawler_same_host_only_blocks_external():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idx = Indexer(db)
        pages = {
            "rhttp://a.rns/": "<body>a <a href='rhttp://b.rns/x'>b</a></body>",
        }
        cr = Crawler(db, idx, FakePageFetcher(pages), max_pages=10, same_host_only=True)
        cr.seed(["rhttp://a.rns/"])
        n = asyncio.run(cr.crawl())
        assert n == 1  # external link not followed
        assert idx.query(tokenize("b")) == [] or all(
            r["url"] != "rhttp://b.rns/x" for r in idx.query(tokenize("b"))
        )


def test_distributed_query_merges_sources():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        idx = Indexer(db)
        idx.index("rhttp://local.rns/", "local.rns", "Local", "mesh radio local")
        svc = SearchService(idx, self_dest_hash="self")
        peer = FakeSearchSource(SearchResults(
            query="mesh",
            results=[{"url": "rhttp://peer.rns/", "host": "peer.rns",
                      "title": "Peer", "score": 5, "matched": 1}],
            source="peerA",
        ))
        out = asyncio.run(svc.query(SearchQuery("mesh", limit=10), sources=[peer]))
        urls = [r["url"] for r in out.results]
        assert "rhttp://local.rns/" in urls
        assert "rhttp://peer.rns/" in urls
        # peer result carries its source
        peer_entry = next(r for r in out.results if r["url"] == "rhttp://peer.rns/")
        assert "peerA" in peer_entry["sources"]


def test_merge_results_sums_scores():
    r1 = SearchResults(query="x", results=[
        {"url": "u", "host": "h", "title": "t", "score": 3, "matched": 1}], source="a")
    r2 = SearchResults(query="x", results=[
        {"url": "u", "host": "h", "title": "t", "score": 4, "matched": 2}], source="b")
    merged = merge_results([r1, r2], SearchQuery("x"))
    assert merged.results[0]["score"] == 7
    assert merged.results[0]["matched"] == 2
    assert set(merged.results[0]["sources"]) == {"a", "b"}


def test_search_query_roundtrip():
    q = SearchQuery("delay tolerant mesh", limit=5)
    back = SearchQuery.from_bytes(q.to_bytes())
    assert back.query == "delay tolerant mesh" and back.limit == 5