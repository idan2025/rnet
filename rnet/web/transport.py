"""RHTTP transport: carry requests/responses over RNS links (or a fake)."""
from __future__ import annotations

import asyncio
from typing import Callable, Optional

import RNS

from rnet.errors import RNetError
from rnet.web.protocol import RHTTPResponse, RHTTPRequest

WEB_APP = "rnet"
WEB_ASPECT = "http"


class WebTransport:
    async def request(self, host_dest_hash: str, req: RHTTPRequest,
                      timeout: float = 30.0) -> RHTTPResponse:
        raise NotImplementedError


class FakeWebTransport(WebTransport):
    """In-process transport: routes to a registered server handler."""

    def __init__(self):
        self._servers: dict = {}

    def register(self, host_dest_hash: str,
                 handler: Callable[[RHTTPRequest], RHTTPResponse]) -> None:
        self._servers[host_dest_hash] = handler

    def unregister(self, host_dest_hash: str) -> None:
        self._servers.pop(host_dest_hash, None)

    async def request(self, host_dest_hash: str, req: RHTTPRequest,
                      timeout: float = 30.0) -> RHTTPResponse:
        h = self._servers.get(host_dest_hash)
        if h is None:
            return RHTTPResponse(status=503, headers={"X-Error": "no server"},
                                 content_hash=b"\x00" * 32, size=0)
        await asyncio.sleep(0)
        return h(req)


class RNSWebTransport(WebTransport):
    """Real transport: RNS link request to the host's ``rnet.http`` dest."""

    REQUEST_PATH = "http"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def _resolve(self, host_dest_hash: str) -> Optional[RNS.Destination]:
        ident = RNS.Identity.recall(bytes.fromhex(host_dest_hash))
        if ident is None:
            return None
        return RNS.Destination(
            ident, RNS.Destination.OUT, RNS.Destination.SINGLE, WEB_APP, WEB_ASPECT
        )

    async def request(self, host_dest_hash: str, req: RHTTPRequest,
                      timeout: float = 30.0) -> RHTTPResponse:
        loop = asyncio.get_running_loop()

        def _do() -> RHTTPResponse:
            dest = self._resolve(host_dest_hash)
            if dest is None:
                raise RNetError(f"cannot recall host identity {host_dest_hash}")
            if not RNS.Transport.has_path(dest.hash):
                RNS.Transport.request_path(dest.hash)
                raise RNetError(f"no path to host {host_dest_hash}")
            link = RNS.Link(dest)
            resp = link.request(self.REQUEST_PATH, data=req.to_bytes(),
                                timeout=self.timeout)
            if resp is None:
                raise RNetError(f"no response from {host_dest_hash}")
            return RHTTPResponse.from_bytes(bytes(resp))

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise RNetError(f"RHTTP request timed out to {host_dest_hash}") from exc