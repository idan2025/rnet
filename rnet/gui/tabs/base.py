"""Base tab: owns a QWidget, exposes lifecycle hooks."""
from __future__ import annotations


class BaseTab:
    """Subclasses build ``self.widget`` and optional ``on_node_started/stopped``."""

    def __init__(self, controller, bridge):
        self.controller = controller
        self.bridge = bridge
        self.widget = None  # set by subclass

    def on_node_started(self) -> None:
        """Override: refresh when the node starts."""

    def on_node_stopped(self) -> None:
        """Override: refresh when the node stops."""


def qt():
    from PySide6 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui