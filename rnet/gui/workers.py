"""Qt <-> asyncio marshalling helpers for the GUI."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Optional


# A QObject whose only job is to carry an arbitrary callable to the Qt GUI
# thread via a queued signal. ``offload``/``run_async`` resolve on a worker
# thread; if their ``on_done`` callbacks build Qt widgets directly (tables,
# status dots, dialogs) that happens off the GUI thread and the widgets are
# created with the wrong parent / thread affinity — manifesting as stray
# tiny top-level windows. Emitting through this signal queues the callback
# onto the GUI thread instead. Created lazily on the first call (which is on
# the GUI thread) so its thread affinity is correct.
_marshaller = None


try:
    from PySide6 import QtCore as _QtCore  # noqa: F401

    class _Marshaller(_QtCore.QObject):
        invoke = _QtCore.Signal(object)
except Exception:  # PySide6 not importable in this environment
    _Marshaller = None


def _ensure_marshaller() -> None:
    """Create the marshaller QObject on the GUI thread if not already.

    Safe to call from any thread: only creates when a QApplication exists AND
    the caller is on the application (GUI) thread, so the object's thread
    affinity is correct. No-op otherwise.
    """
    global _marshaller
    if _marshaller is not None:
        return
    try:
        from PySide6 import QtCore, QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is None or QtCore.QThread.currentThread() is not app.thread():
            return
        _marshaller = _Marshaller()
        _marshaller.invoke.connect(lambda f: f(), QtCore.Qt.QueuedConnection)
    except Exception:  # no Qt available at all
        pass


def _gui_marshal(fn: Callable) -> None:
    """Run ``fn`` on the Qt GUI thread if a QApplication exists, else now."""
    global _marshaller
    try:
        from PySide6 import QtWidgets
    except Exception:  # no Qt available at all
        fn()
        return
    if QtWidgets.QApplication.instance() is None:
        fn()
        return
    _ensure_marshaller()  # creates on GUI thread if we're on it
    if _marshaller is None:
        # App exists but marshaller not yet created (we're off the GUI thread
        # and no GUI-thread call has initialised it). Fall back to a direct
        # call rather than losing the callback.
        fn()
        return
    _marshaller.invoke.emit(fn)


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
                _gui_marshal(lambda: on_done(result))
        except BaseException as exc:  # noqa: BLE001 - surface to caller
            if on_error is not None:
                _gui_marshal(lambda: on_error(exc))

    t = threading.Thread(target=_watch, daemon=True)
    t.start()
    return fut


def offload(func: Callable, *args, loop: Optional[asyncio.AbstractEventLoop] = None,
            on_done: Optional[Callable[[Any], None]] = None,
            on_error: Optional[Callable[[BaseException], None]] = None,
            **kwargs) -> threading.Thread:
    """Run a blocking sync ``func`` off the GUI thread; call ``on_done`` with result.

    If ``on_error`` is given it is invoked with the exception (and ``on_done``
    is skipped) when ``func`` raises. Without ``on_error`` the exception is
    passed to ``on_done`` as its argument, preserving the original behaviour.
    """
    result = {}

    def _run():
        try:
            result["value"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    def _emit():
        if "error" in result:
            err = result["error"]
            if on_error is not None:
                _gui_marshal(lambda: on_error(err))
            elif on_done is not None:
                _gui_marshal(lambda: on_done(err))
        elif on_done is not None:
            val = result.get("value")
            _gui_marshal(lambda: on_done(val))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    if on_done is not None or on_error is not None:
        watcher = threading.Thread(target=lambda: (t.join(), _emit()), daemon=True)
        watcher.start()
    return t

# Best-effort eager creation on import (when a QApplication already exists and
# we're on the GUI thread). offload()/run_async() are imported after the
# QApplication is constructed in both the app and the test suite, so this
# usually succeeds and the marshaller's thread affinity is correct.
_ensure_marshaller()
