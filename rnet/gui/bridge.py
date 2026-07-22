"""Qt signal bridge: RNet EventBus events -> Qt signals (GUI thread).

Also carries GUI-level events (theme changes, interface changes, send
status) that don't come from the EventBus but still need to cross from the
asyncio/worker threads to the Qt main thread.
"""
from __future__ import annotations


def make_bridge():
    """Build a QObject signal bridge. Lazily imports Qt.

    Returns an object with signals:
      ``peer_discovered(object)``, ``message_received(object)``,
      ``receipt_received(object)``, ``node_started(object)``,
      ``node_stopped(object)``, ``log(str)``, ``announce_sent(object)``,
      ``send_status(object)``, ``interface_changed(object)``,
      ``theme_changed(str)``, ``settings_changed(object)``.
    """
    from PySide6 import QtCore

    class _Bridge(QtCore.QObject):
        peer_discovered = QtCore.Signal(object)
        message_received = QtCore.Signal(object)
        receipt_received = QtCore.Signal(object)
        node_started = QtCore.Signal(object)
        node_stopped = QtCore.Signal(object)
        log = QtCore.Signal(str)
        announce_sent = QtCore.Signal(object)
        send_status = QtCore.Signal(object)
        interface_changed = QtCore.Signal(object)
        theme_changed = QtCore.Signal(str)
        settings_changed = QtCore.Signal(object)

    return _Bridge()