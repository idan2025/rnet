"""Crawler: discover `web` services, fetch RHTTP pages, feed the indexer.

Discovers hosts from the peer registry (peers advertising `web`), fetches
their pages via a :class:`PageFetcher`, extracts text + links with the stdlib
HTML parser (no external dep), and indexes them. Maintains a frontier +
seen-set in SQLite so crawling is resumable and bounded.

Testable via :class:`FakePageFetcher`; the real fetcher wraps
:class:`rnet.web.WebClient`.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional, Set
from urllib.parse import urljoin

from rnet.db.connection import Database
from rnet.search.indexer import Indexer
from rnet.search.tokenizer import tokenize

log = logging.getLogger(__name__)


@dataclass
class FetchedPage:
    url: str
    host: str
    title: str = ""
    text: str = ""
    links: List[str] = field(default_factory=list)
    content_hash: bytes = b""


class _HTMLExtractor(HTMLParser):
    """Collects <title>, visible text, and href links."""

    def __init__(self):
        super().__init__()
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self.links: List[str] = []
        self._in_title = False
        self._skip = False  # skip script/style text

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag in ("script", "style"):
            self._skip = True
        elif tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)

    def result(self) -> tuple:
        title = " ".join("".join(self.title_parts).split())
        text = " ".join("".join(self.text_parts).split())
        return title, text, self.links


def parse_html(html: str, base_url: str = "") -> tuple:
    """Return (title, text, absolute_links) from an HTML string."""
    p = _HTMLExtractor()
    try:
        p.feed(html)
    except Exception:
        pass
    title, text, links = p.result()
    abs_links = []
    for href in links:
        if href.startswith(("rhttp://", "http://", "https://", "//")):
            abs_links.append(href)
        elif base_url and href.startswith("/"):
            # base_url like rhttp://host.rns
            abs_links.append(base_url.rstrip("/") + href)
        elif base_url and not href.startswith("#"):
            abs_links.append(base_url.rstrip("/") + "/" + href.lstrip("/"))
    return title, text, abs_links


class PageFetcher:
    async def fetch(self, url: str) -> Optional[FetchedPage]:
        raise NotImplementedError


class FakePageFetcher(PageFetcher):
    """Serves from an in-process dict of url -> html."""

    def __init__(self, pages: dict):
        self.pages = pages

    async def fetch(self, url: str) -> Optional[FetchedPage]:
        await asyncio.sleep(0)
        html = self.pages.get(url)
        if html is None:
            return None
        title, text, links = parse_html(html, base_url=url)
        from rnet.storage.cas import hash_data
        return FetchedPage(url=url, host=_host_of(url), title=title, text=text,
                           links=links, content_hash=hash_data(html.encode()))


def _host_of(url: str) -> str:
    # rhttp://library.rns/books -> library.rns
    m = re.match(r"[a-z]+://([^/]+)", url)
    return m.group(1) if m else url


class Crawler:
    """Frontier-driven crawler that indexes fetched pages."""

    def __init__(self, db: Database, indexer: Indexer, fetcher: PageFetcher,
                 max_pages: int = 1000, same_host_only: bool = True):
        self.db = db
        self.indexer = indexer
        self.fetcher = fetcher
        self.max_pages = max_pages
        self.same_host_only = same_host_only

    def seed(self, urls: List[str]) -> None:
        now = int(self.db.now())
        for u in urls:
            self.db.execute(
                "INSERT OR IGNORE INTO crawl_queue (url, host, queued_at, priority) "
                "VALUES (?,?,?,1)",
                (u, _host_of(u), now),
            )

    def _next_url(self) -> Optional[str]:
        row = self.db.query_one(
            "SELECT url FROM crawl_queue ORDER BY priority ASC, queued_at ASC LIMIT 1"
        )
        if not row:
            return None
        url = row["url"]
        self.db.execute("DELETE FROM crawl_queue WHERE url=?", (url,))
        return url

    def _mark_seen(self, url: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO crawl_seen (url, seen_at) VALUES (?,?)",
            (url, int(self.db.now())),
        )

    def _seen(self, url: str) -> bool:
        return self.db.query_one(
            "SELECT 1 FROM crawl_seen WHERE url=?", (url,)
        ) is not None

    def _enqueue(self, url: str) -> None:
        if self._seen(url):
            return
        self.db.execute(
            "INSERT OR IGNORE INTO crawl_queue (url, host, queued_at, priority) "
            "VALUES (?,?,?,1)",
            (url, _host_of(url), int(self.db.now())),
        )

    async def crawl(self, max_pages: Optional[int] = None) -> int:
        """Crawl until the frontier is empty or the limit is hit.

        Returns the number of pages indexed.
        """
        limit = max_pages if max_pages is not None else self.max_pages
        count = 0
        while count < limit:
            url = self._next_url()
            if url is None:
                break
            if self._seen(url):
                continue
            self._mark_seen(url)
            page = await self.fetcher.fetch(url)
            if page is None:
                continue
            self.indexer.index(page.url, page.host, page.title, page.text,
                               content_hash=page.content_hash)
            count += 1
            host = _host_of(url)
            for link in page.links:
                if self.same_host_only and _host_of(link) != host:
                    continue
                self._enqueue(link)
        return count

    def discover_web_hosts(self, registry) -> List[str]:
        """Seed the frontier from peers advertising the `web` capability."""
        peers = registry.list_by_capability("web")
        seeds = []
        for p in peers:
            dest = p["dest_hash"]
            # We address web hosts by their node dest hash; the browser resolves
            # a name to find a dest. For crawling we seed rhttp://<dest> URLs.
            seeds.append(f"rhttp://{dest}/")
        self.seed(seeds)
        return seeds