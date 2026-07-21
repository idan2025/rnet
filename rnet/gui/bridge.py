"""Qt signal bridge: RNet EventBus events -> Qt signals (GUI thread)."""
from __future__ import annotations

from typing import Any


def make_bridge():
    """Build a QObject signal bridge. Lazily imports Qt.

    Returns an object with signals:
      ``peer_discovered(object)``, ``message_received(object)``,
      ``receipt_received(object)``, ``node_started(object)``,
      ``node_stopped(object)``, ``log(str)``.
    """
    from PySide6 import QtCore

    class _Bridge(QtCore.QObject):
        peer_discovered = QtCore.Signal(object)
        message_received = QtCore.Signal(object)
        receipt_received = QtCore.Signal(object)
        node_started = QtCore.Signal(object)
        node_stopped = QtCore.Signal(object)
        log = QtCore.Signal(str)

    return _Bridge()