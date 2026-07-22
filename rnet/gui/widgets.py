"""Reusable themed widgets used across all tabs.

Keeps tab code short and the look consistent: status dots, avatars,
cards, chat bubbles, search fields, copy labels, and a non-blocking toast.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from PySide6 import QtWidgets, QtCore, QtGui


def qt():
    return QtWidgets, QtCore, QtGui


class StatusDot(QtWidgets.QLabel):
    """A colored circle indicating state (green/grey/red/amber)."""

    _COLORS = {
        "green": "#3bb26b",
        "grey": "#8a8f98",
        "red": "#b3413c",
        "amber": "#c9a13a",
    }

    def __init__(self, state: str = "grey", size: int = 10):
        super().__init__()
        self.setFixedSize(size, size)
        self.set_state(state)

    def set_state(self, state: str) -> None:
        color = self._COLORS.get(state, self._COLORS["grey"])
        self.setStyleSheet(
            f"background:{color}; border-radius:{self.width()//2}px;"
        )


class Avatar(QtWidgets.QLabel):
    """Round, color-from-hash avatar with initials."""

    def __init__(self, seed: str = "", text: str = "", size: int = 36):
        super().__init__()
        self.size_ = size
        self.setFixedSize(size, size)
        self.set(seed, text)

    def set(self, seed: str, text: str = "") -> None:
        QtWidgets, QtCore, QtGui = qt()
        h = hashlib.md5((seed or text or "?").encode()).digest()
        hue = int.from_bytes(h[:2], "big") % 360
        color = QtGui.QColor.fromHsl(hue, 120, 130)
        initials = "".join([w[0] for w in (text or "?").split() if w])[:2].upper() or "?"
        self.setText(initials)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet(
            f"background:{color.name()}; color:#fff; border-radius:{self.size_//2}px;"
            f"font-weight:600; font-size:{max(10, self.size_//3)}px;"
        )


class Card(QtWidgets.QFrame):
    """A rounded panel. No layout is created automatically — callers do
    ``QtWidgets.QVBoxLayout(card)`` so the card owns exactly one layout."""

    def __init__(self, layout_type="v"):
        super().__init__()
        self.setObjectName("card")

    def addWidget(self, w):
        self.layout().addWidget(w)
        return w

    def addLayout(self, l):
        self.layout().addLayout(l)
        return l


class SectionLabel(QtWidgets.QLabel):
    """A muted small-caps section header."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setStyleSheet(
            "color: palette(placeholder-text); font-size: 11px; "
            "font-weight:600; letter-spacing:1px; padding: 4px 0;"
        )


class IconButton(QtWidgets.QPushButton):
    """A small square icon button using a unicode glyph."""

    def __init__(self, glyph: str, tip: str = ""):
        super().__init__(glyph)
        self.setFixedSize(30, 30)
        self.setToolTip(tip)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    @property
    def QtCore(self):
        return QtCore


class CopyLabel(QtWidgets.QLabel):
    """A label whose text is copyable via context menu + click-to-copy."""

    def __init__(self, text: str = "", mono: bool = True):
        super().__init__(text)
        if mono:
            self.setStyleSheet("font-family: monospace; color: palette(placeholder-text);")
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.setContextMenuPolicy(QtCore.Qt.ActionsContextMenu)
        act = QtGui.QAction("Copy", self)
        act.triggered.connect(self._copy)
        self.addAction(act)

    def _copy(self):
        QtWidgets.QApplication.clipboard().setText(self.text())

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            QtWidgets.QApplication.clipboard().setText(self.text())
        super().mousePressEvent(e)


class SearchField(QtWidgets.QLineEdit):
    """A search box with a placeholder glyph."""

    def __init__(self, placeholder: str = "Search…"):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)


class BubbledMessage(QtWidgets.QWidget):
    """A chat bubble: text + timestamp + optional delivery tick.

    ``outgoing`` aligns right and uses the send color; incoming aligns left.
    """

    def __init__(self, text: str, ts: str = "", outgoing: bool = False,
                 state: str = ""):
        super().__init__()
        QtWidgets, QtCore, QtGui = qt()
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)

        bubble = QtWidgets.QFrame()
        bubble.setProperty("class", "bubble-out" if outgoing else "bubble-in")
        bubble.setStyleSheet(
            "border-radius: 10px; padding: 7px 11px;"
        )
        blay = QtWidgets.QVBoxLayout(bubble)
        blay.setContentsMargins(9, 6, 9, 6)
        body = QtWidgets.QLabel(text)
        body.setWordWrap(True)
        blay.addWidget(body)

        meta = QtWidgets.QLabel(ts + ("  ✓✓" if state == "acked" else
                                       ("  ✓" if state == "delivered" else "")))
        meta.setStyleSheet("color: palette(placeholder-text); font-size: 10px;")
        meta.setAlignment(QtCore.Qt.AlignRight)
        blay.addWidget(meta)

        row = QtWidgets.QHBoxLayout()
        if outgoing:
            row.addStretch(1)
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch(1)
        lay.addLayout(row)


class Toast(QtWidgets.QLabel):
    """A short-lived status label shown in a status bar or corner.

    Use ``Toast.show_in(parent, text)`` to flash a message for a few seconds.
    """

    @staticmethod
    def show_in(status_bar: QtWidgets.QStatusBar, text: str,
                timeout_ms: int = 4000) -> None:
        status_bar.showMessage(text, timeout_ms)


def confirm(parent: QtWidgets.QWidget, title: str, body: str) -> bool:
    """Modal yes/no confirm for destructive ops. Returns True on Yes."""
    QtWidgets, _, _ = qt()
    btns = QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No
    return QtWidgets.QMessageBox.question(parent, title, body, btns,
            QtWidgets.QMessageBox.StandardButton.No) == QtWidgets.QMessageBox.StandardButton.Yes


def warn(parent: QtWidgets.QWidget, title: str, body: str) -> None:
    QtWidgets, _, _ = qt()
    QtWidgets.QMessageBox.warning(parent, title, body)


def open_path(path: str) -> None:
    """Best-effort open a file/folder in the OS file manager."""
    import subprocess, sys
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass