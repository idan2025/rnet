"""RNS wiring for the search service: answers distributed queries."""
from __future__ import annotations

import logging
from typing import Optional

import RNS

from rnet.core.events import LoopBridge
from rnet.search.indexer import Indexer
from rnet.search.query import SearchQuery, SearchResults, SearchService

log = logging.getLogger(__name__)

SEARCH_APP = "rnet"
SEARCH_ASPECT = "search"


class SearchServiceEndpoint:
    """Mounts a ``rnet.search`` request handler answering from the local index."""

    def __init__(self, indexer: Indexer, identity: RNS.Identity, bridge: LoopBridge,
                 self_dest_hash: str = ""):
        self.indexer = indexer
        self.identity = identity
        self.bridge = bridge
        self.service = SearchService(indexer, self_dest_hash=self_dest_hash)
        self.destination: Optional[RNS.Destination] = None

    def start(self) -> str:
        self.destination = RNS.Destination(
            self.identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            SEARCH_APP,
            SEARCH_ASPECT,
        )
        endpoint = self

        def generator(path, data, request_id, remote_identity, requested_at):
            try:
                q = SearchQuery.from_bytes(bytes(data))
                fut = endpoint.bridge.run_coroutine_threadsafe(
                    endpoint.service.query_local_async(q)
                )
                res = fut.result(timeout=20)
                return res.to_bytes()
            except Exception:
                log.exception("search handler crashed")
                return SearchResults(query="", results=[]).to_bytes()

        self.destination.register_request_handler(
            "search", response_generator=generator, allow=RNS.Destination.ALLOW_ALL
        )
        return self.destination.hash.hex()

    def stop(self) -> None:
        if self.destination is not None:
            try:
                self.destination.deregister_request_handler("search")
            except Exception:  # pragma: no cover
                pass
            self.destination = None

    async def query_local_async(self, q: SearchQuery) -> SearchResults:
        return self.service.query_local(q)