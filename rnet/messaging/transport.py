"""Message transport abstraction.

Decouples the messenger from RNS link mechanics so the crypto/queue logic is
unit-testable with a fake, while the real :class:`RNSLinkTransport` drives
RNS links in production.

A transport delivers a signed-frame blob to a recipient's messaging
destination and returns the recipient's response (a signed receipt frame), or
raises :class:`DeliveryError` if the peer is unreachable. Callers fall back to
store-and-forward on :class:`PeerUnreachable`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import RNS

from rnet.errors import MessageError

log = logging.getLogger(__name__)


class DeliveryError(MessageError):
    """Delivery failed but may be retried."""


class PeerUnreachable(DeliveryError):
    """No route to peer right now; use store-and-forward."""


class MessageTransport:
    """Abstract transport."""

    async def deliver(self, recipient_dest_hash: str, frame_bytes: bytes,
                      timeout: float = 30.0) -> bytes:
        raise NotImplementedError


class FakeTransport(MessageTransport):
    """In-process transport for tests: routes via a registry of handlers.

    Each "recipient" registers a callable handler that receives the frame
    bytes and returns response bytes (the signed receipt). Used to wire two
    Messengers together in tests without RNS links.
    """

    def __init__(self):
        self._handlers: dict = {}

    def register(self, recipient_dest_hash: str,
                 handler: Callable[[str, bytes], bytes]) -> None:
        self._handlers[recipient_dest_hash] = handler

    def unregister(self, recipient_dest_hash: str) -> None:
        self._handlers.pop(recipient_dest_hash, None)

    async def deliver(self, recipient_dest_hash: str, frame_bytes: bytes,
                      timeout: float = 30.0) -> bytes:
        h = self._handlers.get(recipient_dest_hash)
        if h is None:
            raise PeerUnreachable(f"no handler for {recipient_dest_hash}")
        # Simulate a tiny async hop.
        await asyncio.sleep(0)
        result = h(recipient_dest_hash, frame_bytes)
        if asyncio.iscoroutine(result):
            result = await result
        return result


class RNSLinkTransport(MessageTransport):
    """Real transport: establishes an RNS Link and issues a request.

    Recipient identity is recalled from the network (via announces). If the
    peer is not currently reachable, raises :class:`PeerUnreachable` so the
    caller can fall back to store-and-forward.
    """

    REQUEST_PATH = "msg"

    def __init__(self, request_timeout: float = 30.0):
        self.request_timeout = request_timeout

    def _resolve(self, recipient_dest_hash: str) -> Optional[RNS.Destination]:
        ident = RNS.Identity.recall(bytes.fromhex(recipient_dest_hash))
        if ident is None:
            return None
        dest = RNS.Destination(
            ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
            "rnet", "msg",
        )
        return dest

    async def deliver(self, recipient_dest_hash: str, frame_bytes: bytes,
                      timeout: float = 30.0) -> bytes:
        loop = asyncio.get_running_loop()

        def _do() -> bytes:
            dest = self._resolve(recipient_dest_hash)
            if dest is None:
                raise PeerUnreachable(f"cannot recall identity for {recipient_dest_hash}")
            if not RNS.Transport.has_path(dest.hash):
                # Request a path; delivery may succeed on a later retry.
                RNS.Transport.request_path(dest.hash)
                raise PeerUnreachable(f"no path to {recipient_dest_hash}")
            link = RNS.Link(dest)
            # Synchronous-ish: rely on RNS' internal request with a timeout.
            response = link.request(
                self.REQUEST_PATH, data=frame_bytes, timeout=self.request_timeout
            )
            if response is None:
                raise DeliveryError(f"no response from {recipient_dest_hash}")
            return bytes(response)

        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _do), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise DeliveryError(f"delivery timed out to {recipient_dest_hash}") from exc