"""Distributed search query + result merging.

A query is tokenized locally; results come from the local index plus any
:class:`SearchSource` peers (search-capable nodes). Results are merged by URL
(summing scores across sources) and ranked. No central search company: every
``search`` node answers from its own index.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional

import msgpack

from rnet.errors import WireError
from rnet.search.indexer import Indexer
from rnet.search.tokenizer import tokenize


@dataclass
class SearchQuery:
    query: str = ""
    limit: int = 20

    def to_bytes(self) -> bytes:
        return msgpack.packb({"q": self.query, "n": int(self.limit)}, use_bin_type=True)

    @classmethod
    def from_bytes(cls, raw: bytes) -> "SearchQuery":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad search query: {exc}") from exc
        return cls(query=str(d.get("q", "")), limit=int(d.get("n", 20)))


@dataclass
class SearchResults:
    query: str = ""
    results: List[dict] = field(default_factory=list)
    source: str = ""  # answering node dest hash

    def to_bytes(self) -> bytes:
        return msgpack.packb(
            {"q": self.query, "r": self.results, "s": self.source},
            use_bin_type=True,
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> "SearchResults":
        try:
            d = msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise WireError(f"bad search results: {exc}") from exc
        return cls(query=str(d.get("q", "")), results=list(d.get("r", [])),
                   source=str(d.get("s", "")))


class SearchSource:
    async def search(self, query: SearchQuery) -> Optional[SearchResults]:
        raise NotImplementedError


class FakeSearchSource(SearchSource):
    def __init__(self, results: SearchResults):
        self._results = results

    async def search(self, query: SearchQuery) -> Optional[SearchResults]:
        await asyncio.sleep(0)
        return self._results


class RNSSearchSource(SearchSource):
    """Queries a search peer's ``rnet.search`` destination over RNS."""
    REQUEST_PATH = "search"

    def __init__(self, peer_dest_hash: str, self_dest_hash: str = "",
                 timeout: float = 20.0):
        self.peer_dest_hash = peer_dest_hash
        self.self_dest_hash = self_dest_hash
        self.timeout = timeout

    def _resolve(self):
        import RNS
        ident = RNS.Identity.recall(bytes.fromhex(self.peer_dest_hash))
        if ident is None:
            return None
        return RNS.Destination(
            ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "rnet", "search"
        )

    async def search(self, query: SearchQuery) -> Optional[SearchResults]:
        import RNS
        loop = asyncio.get_running_loop()

        def _do():
            dest = self._resolve()
            if dest is None or not RNS.Transport.has_path(dest.hash):
                return None
            link = RNS.Link(dest)
            resp = link.request(self.REQUEST_PATH, data=query.to_bytes(),
                                timeout=self.timeout)
            return bytes(resp) if resp is not None else None

        try:
            raw = await asyncio.wait_for(loop.run_in_executor(None, _do),
                                         timeout=self.timeout)
        except asyncio.TimeoutError:
            return None
        if raw is None:
            return None
        return SearchResults.from_bytes(raw)


class SearchService:
    """Local query + distributed fan-out + merge."""

    def __init__(self, indexer: Indexer, self_dest_hash: str = ""):
        self.indexer = indexer
        self.self_dest_hash = self_dest_hash

    def query_local(self, query: SearchQuery) -> SearchResults:
        terms = tokenize(query.query)
        results = self.indexer.query(terms, limit=query.limit)
        return SearchResults(query=query.query, results=results,
                             source=self.self_dest_hash)

    async def query(self, query: SearchQuery,
                    sources: Optional[List[SearchSource]] = None) -> SearchResults:
        """Query local index + peers, merge, return ranked results."""
        merged = self.query_local(query)
        if sources:
            async def fetch_one(src: SearchSource) -> Optional[SearchResults]:
                try:
                    return await src.search(query)
                except Exception:
                    return None
            peer_results = await asyncio.gather(*[fetch_one(s) for s in sources])
            merged = merge_results([merged] + [r for r in peer_results if r],
                                   query=query, limit=query.limit)
        return merged


def merge_results(result_sets: List[SearchResults], query: SearchQuery,
                  limit: int = 20) -> SearchResults:
    """Merge by URL, summing scores and maxing matched-term counts."""
    by_url: dict = {}
    for rs in result_sets:
        for r in rs.results:
            url = r["url"]
            if url not in by_url:
                by_url[url] = {
                    "url": url,
                    "host": r.get("host", ""),
                    "title": r.get("title", ""),
                    "score": 0,
                    "matched": 0,
                    "sources": [],
                }
            entry = by_url[url]
            entry["score"] += int(r.get("score", 0))
            entry["matched"] = max(entry["matched"], int(r.get("matched", 0)))
            if rs.source and rs.source not in entry["sources"]:
                entry["sources"].append(rs.source)
            if not entry["title"] and r.get("title"):
                entry["title"] = r["title"]
    ranked = sorted(by_url.values(),
                    key=lambda e: (e["matched"], e["score"]), reverse=True)
    return SearchResults(query=query.query, results=ranked[:limit],
                         source="merged")