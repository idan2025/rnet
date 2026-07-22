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

    def __init__(self, timeout: float = 30.0, pubkey_lookup=None):
        self.timeout = timeout
        # Optional callable(dest_hash_hex) -> pubkey bytes, used to build the
        # host identity when RNS.Identity.recall fails (the host's announce
        # hasn't been stored in RNS's known_destinations yet, but RNet's own
        # announce handler already stored the pubkey in known_identities).
        self.pubkey_lookup = pubkey_lookup

    def _resolve(self, host_dest_hash: str) -> Optional[RNS.Destination]:
        ident = RNS.Identity.recall(bytes.fromhex(host_dest_hash))
        if ident is None and self.pubkey_lookup is not None:
            pubkey = self.pubkey_lookup(host_dest_hash)
            if pubkey:
                ident = RNS.Identity(create_keys=False)
                ident.load_public_key(bytes(pubkey))
        if ident is None:
            return None
        return RNS.Destination(
            ident, RNS.Destination.OUT, RNS.Destination.SINGLE, WEB_APP, WEB_ASPECT
        )

    async def request(self, host_dest_hash: str, req: RHTTPRequest,
                      timeout: float = 30.0) -> RHTTPResponse:
        import time
        loop = asyncio.get_running_loop()

        def _do() -> RHTTPResponse:
            dest = self._resolve(host_dest_hash)
            if dest is None:
                raise RNetError(f"cannot recall host identity {host_dest_hash}")
            # The rnet.http destination is not announced (only rnet.node is),
            # so peers learn a path to it via a path request, which the host
            # answers because it owns the destination. Request the path and
            # actually wait for the response instead of failing immediately.
            if not RNS.Transport.has_path(dest.hash):
                RNS.Transport.request_path(dest.hash)
                path_deadline = time.time() + 15
                while time.time() < path_deadline and not RNS.Transport.has_path(dest.hash):
                    time.sleep(0.25)
                if not RNS.Transport.has_path(dest.hash):
                    raise RNetError(f"no path to host {host_dest_hash}")
            link = RNS.Link(dest)
            # Wait for the link handshake to complete (status ACTIVE); until
            # then link.mdu is unset and link.request() raises AttributeError.
            link_deadline = time.time() + 15
            while time.time() < link_deadline and link.status != RNS.Link.ACTIVE:
                time.sleep(0.1)
            if link.status != RNS.Link.ACTIVE:
                raise RNetError(f"link to {host_dest_hash} not established")
            receipt = link.request(self.REQUEST_PATH, data=req.to_bytes(),
                                   timeout=self.timeout)
            if receipt is False:
                raise RNetError(f"request not sent to {host_dest_hash}")
            # link.request returns immediately with a RequestReceipt; poll it
            # until the response arrives (READY) or the request fails.
            req_deadline = time.time() + self.timeout + 5
            while time.time() < req_deadline and receipt.status not in (
                RNS.RequestReceipt.READY, RNS.RequestReceipt.FAILED
            ):
                time.sleep(0.1)
            if receipt.status != RNS.RequestReceipt.READY or receipt.response is None:
                raise RNetError(f"no response from {host_dest_hash}")
            return RHTTPResponse.from_bytes(bytes(receipt.response))

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=timeout + 20
            )
        except asyncio.TimeoutError as exc:
            raise RNetError(f"RHTTP request timed out to {host_dest_hash}") from exc