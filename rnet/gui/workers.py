"""Qt <-> asyncio marshalling helpers for the GUI."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional


def run_async(coro, loop: asyncio.AbstractEventLoop,
              on_done: Optional[Callable[[Any], None]] = None,
              on_error: Optional[Callable[[BaseException], None]] = None) -> "asyncio.Future":
    """Schedule ``coro`` on ``loop`` from any thread.

    Returns the concurrent.futures Future. If ``on_done``/``on_error`` are
    given, they are invoked from a worker thread after the coro resolves —
    typically they emit a Qt signal to marshal back to the GUI thread.
    """
    fut = asyncio.run_coroutine_threadsafe(coro, loop)

    def _watch():
        try:
            result = fut.result()
            if on_done is not None:
                on_done(result)
        except BaseException as exc:  # noqa: BLE001 - surface to caller
            if on_error is not None:
                on_error(exc)

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return fut


def offload(func: Callable, *args, loop: Optional[asyncio.AbstractEventLoop] = None,
            on_done: Optional[Callable[[Any], None]] = None, **kwargs) -> threading.Thread:
    """Run a blocking sync ``func`` off the GUI thread; call ``on_done`` with result."""
    result = {}

    def _run():
        try:
            result["value"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    def _emit():
        if "error" in result and on_done is not None:
            on_done(result["error"])
        elif on_done is not None:
            on_done(result.get("value"))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    if on_done is not None:
        watcher = threading.Thread(target=lambda: (t.join(), _emit()), daemon=True)
        watcher.start()
    return t