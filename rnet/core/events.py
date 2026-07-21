"""Asyncio event bus + thread-safe bridge from RNS callbacks.

RNS drives I/O on its own internal threads and calls back into RNet from
there. RNet service logic is async and lives on a single event loop. This
module provides:

- :class:`EventBus`: a typed pub/sub bus for intra-node events.
- :class:`LoopBridge`: a thread-safe funnel so RNS callbacks running on any
  thread can hand work to the asyncio loop without races.

The bridge is the only sanctioned way for RNS callbacks to touch the loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# A handler is sync (called via loop.call_soon) or async (scheduled on the loop).
EventHandler = Callable[[Any], None]


class EventBus:
    """Lightweight in-process event bus.

    Events are dispatched on the loop thread. Subscribers may be sync or async;
    async subscribers are scheduled as tasks. Use ``emit`` from loop context
    and ``emit_threadsafe`` from RNS callbacks.
    """

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        self._subs: Dict[str, List[EventHandler]] = defaultdict(list)
        self._loop = loop
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            self._subs[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            subs = self._subs.get(event_type, [])
            if handler in subs:
                subs.remove(handler)

    def emit(self, event_type: str, event: Any) -> None:
        """Dispatch on the current (loop) thread."""
        with self._lock:
            subs = list(self._subs.get(event_type, []))
        for h in subs:
            self._dispatch(h, event)

    def emit_threadsafe(self, event_type: str, event: Any) -> None:
        """Dispatch from another thread onto the bound loop."""
        if self._loop is None:
            # No loop yet: queue by dispatching synchronously (best effort).
            self.emit(event_type, event)
            return
        self._loop.call_soon_threadsafe(self.emit, event_type, event)

    def _dispatch(self, handler: EventHandler, event: Any) -> None:
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                if self._loop is not None and self._loop.is_running():
                    asyncio.ensure_future(result, loop=self._loop)
                else:
                    # No running loop: close the coroutine to avoid warnings.
                    result.close()
        except Exception:  # pragma: no cover - defensive
            log.exception("event handler for %r raised", event)


@dataclass
class Event:
    """Base event. Subclass for specific events."""

    type: str


# Common event types (string constants; events carry a dict payload or a
# dataclass). Keeping them as constants avoids typos in subscribe/emit.
PEER_DISCOVERED = "peer.discovered"
PEER_LOST = "peer.lost"
MESSAGE_RECEIVED = "message.received"
RECEIPT_RECEIVED = "receipt.received"
ANNOUNCE_RECEIVED = "announce.received"
NODE_STARTED = "node.started"
NODE_STOPPED = "node.stopped"


class LoopBridge:
    """Thread-safe funnel from RNS threads into the asyncio loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, bus: EventBus):
        self.loop = loop
        self.bus = bus

    def post_event(self, event_type: str, event: Any) -> None:
        """Safe to call from any thread (RNS callbacks)."""
        self.bus.emit_threadsafe(event_type, event)

    def run_coroutine_threadsafe(
        self, coro: Awaitable
    ) -> "asyncio.Future":
        """Schedule a coroutine from a non-loop thread and return a future."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)