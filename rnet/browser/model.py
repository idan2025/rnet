"""Browser model: URL parsing, resolution, fetch, cache, history, bookmarks.

Testable headless (no Qt). The PySide6 view in :mod:`rnet.browser.view` binds
to this model.

URLs accepted:
  - ``rhttp://name.rns/path``  (explicit scheme)
  - ``name.rns``               (bare name -> index page)
  - ``name.rns/path``          (name + path)

A navigation resolves the name to a host dest hash (via :class:`NamingService`),
fetches the page over RHTTP (:class:`WebClient`), verifies the response
signature against the resolved host identity, caches it, and records history.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from rnet.db.connection import Database
from rnet.errors import RNetError
from rnet.identity import IdentityManager
from rnet.naming import NamingService, NameSource


@dataclass
class Page:
    url: str
    final_url: str = ""
    title: str = ""
    html: str = ""
    content_hash: bytes = b""
    status: int = 0
    verified: bool = False
    host: str = ""
    error: str = ""


class BrowserModel:
    """Navigation state + cache, decoupled from the Qt UI."""

    SCHEME = "rhttp://"

    def __init__(self, db: Database, idm: IdentityManager,
                 web_client, naming: NamingService,
                 name_sources: Optional[List[NameSource]] = None):
        self.db = db
        self.idm = idm
        self.web = web_client
        self.naming = naming
        self.name_sources = name_sources or []
        # In-memory back/forward stack for the current session.
        self._back: List[str] = []
        self._fwd: List[str] = []

    # -- URL normalization ------------------------------------------------
    @classmethod
    def normalize_url(cls, raw: str) -> str:
        s = raw.strip()
        if s.startswith(cls.SCHEME):
            return s
        if "://" in s:
            return s  # other scheme; pass through
        # bare name or name/path
        if "/" in s:
            name, _, path = s.partition("/")
            return f"{cls.SCHEME}{name}.rns/{path}" if "." not in name else f"{cls.SCHEME}{s}"
        return f"{cls.SCHEME}{s}.rns" if ".rns" not in s else f"{cls.SCHEME}{s}"

    @staticmethod
    def split_url(url: str) -> tuple:
        """(host_name, path) from an rhttp URL. host_name includes .rns."""
        m = re.match(r"rhttp://([^/]+)(/.*)?", url)
        if not m:
            raise RNetError(f"not an rhttp URL: {url}")
        return m.group(1), (m.group(2) or "/")

    # -- cache ------------------------------------------------------------
    def _cache_key(self, url: str) -> str:
        return f"rhttp:{url}"

    def _cache_get(self, url: str):
        return self.db.query_one(
            "SELECT value FROM cache WHERE key=?", (self._cache_key(url),)
        )

    def _cache_put(self, url: str, page: Page) -> None:
        import msgpack
        blob = msgpack.packb({
            "url": page.final_url or url, "title": page.title, "html": page.html,
            "status": page.status, "verified": 1 if page.verified else 0,
            "host": page.host, "content_hash": page.content_hash,
        }, use_bin_type=True)
        self.db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at, kind) "
            "VALUES (?,?,?,?,?)",
            (self._cache_key(url), blob, 2**31, int(self.db.now()), "rhttp"),
        )

    def cache_get(self, url: str) -> Optional[Page]:
        row = self._cache_get(url)
        if not row:
            return None
        import msgpack
        d = msgpack.unpackb(bytes(row["value"]), raw=False)
        return Page(url=url, final_url=d.get("url", url), title=d.get("title", ""),
                    html=d.get("html", ""), status=int(d.get("status", 0)),
                    verified=bool(d.get("verified", 0)), host=d.get("host", ""),
                    content_hash=d.get("content_hash", b"") or b"")

    # -- history + bookmarks ----------------------------------------------
    def record_history(self, url: str, title: str) -> None:
        self.db.execute(
            "INSERT INTO browser_history (url, title, visited) VALUES (?,?,?)",
            (url, title, int(self.db.now())),
        )

    def history(self, limit: int = 100) -> List[dict]:
        rows = self.db.query(
            "SELECT url, title, visited FROM browser_history ORDER BY visited DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    def add_bookmark(self, url: str, title: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO bookmarks (url, title, added) VALUES (?,?,?)",
            (url, title, int(self.db.now())),
        )

    def remove_bookmark(self, url: str) -> None:
        self.db.execute("DELETE FROM bookmarks WHERE url=?", (url,))

    def bookmarks(self) -> List[dict]:
        rows = self.db.query("SELECT url, title, added FROM bookmarks ORDER BY added DESC")
        return [dict(r) for r in rows]

    # -- navigation -------------------------------------------------------
    async def navigate(self, raw_url: str, use_cache: bool = True) -> Page:
        url = self.normalize_url(raw_url)
        if use_cache:
            cached = self.cache_get(url)
            if cached is not None:
                self._push_history(url)
                return cached
        host_name, path = self.split_url(url)
        # Resolve the name to a host dest hash.
        record = await self.naming.resolve_name(host_name, sources=self.name_sources)
        if record is None:
            return Page(url=url, error=f"could not resolve {host_name}", status=0)
        # Find the web service dest hash for the resolved owner.
        web_dest = None
        for s in record.services:
            if s.get("cap") == "web":
                web_dest = s.get("dest") or record.node
                break
        if web_dest is None:
            web_dest = record.node
        if not web_dest:
            return Page(url=url, error=f"no web service for {host_name}", status=0)
        # Look up the host pubkey for signature verification.
        host_row = self.idm.store.get_known_by_fp(record.fp) or self.idm.store.get_known(record.owner)
        host_pubkey = bytes(host_row["pubkey"]) if host_row and host_row["pubkey"] else None
        # Fetch.
        resp = await self.web.get(web_dest, path, host_pubkey=host_pubkey)
        if resp is None:
            return Page(url=url, host=host_name, error="response signature invalid",
                        status=0)
        html = resp.body.decode("utf-8", errors="replace") if resp.body else ""
        # Extract title for history.
        from rnet.search.crawler import parse_html
        title, _text, _links = parse_html(html, base_url=url)
        verified = bool(host_pubkey is not None) and resp.verify_pubkey(host_pubkey) if host_pubkey else False
        page = Page(
            url=url, final_url=url, title=title or host_name, html=html,
            content_hash=resp.content_hash, status=resp.status, verified=verified,
            host=host_name,
        )
        self._cache_put(url, page)
        self.record_history(url, page.title)
        self._push_history(url)
        return page

    def _push_history(self, url: str) -> None:
        self._back.append(url)
        self._fwd.clear()

    def can_back(self) -> bool:
        return len(self._back) > 1

    def can_forward(self) -> bool:
        return bool(self._fwd)

    def back_url(self) -> Optional[str]:
        if not self.can_back():
            return None
        self._fwd.append(self._back.pop())
        return self._back[-1]

    def forward_url(self) -> Optional[str]:
        if not self.can_forward():
            return None
        url = self._fwd.pop()
        self._back.append(url)
        return url