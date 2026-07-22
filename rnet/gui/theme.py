"""App-wide dark/light theming via QSS.

``apply_theme(app, mode)`` sets the palette + stylesheet on the QApplication.
``toggle(app, current)`` flips between dark and light. Both honor the
palette so native widgets and custom QSS agree.
"""
from __future__ import annotations

DARK = "dark"
LIGHT = "light"


def _palette_dark():
    from PySide6 import QtGui

    p = QtGui.QPalette()
    p.setColor(QtGui.QPalette.Window, QtGui.QColor("#1e1f22"))
    p.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#e6e6e6"))
    p.setColor(QtGui.QPalette.Base, QtGui.QColor("#26282c"))
    p.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#2b2e33"))
    p.setColor(QtGui.QPalette.Text, QtGui.QColor("#e6e6e6"))
    p.setColor(QtGui.QPalette.Button, QtGui.QColor("#2b2e33"))
    p.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#e6e6e6"))
    p.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#3d6ea8"))
    p.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    p.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#2b2e33"))
    p.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor("#e6e6e6"))
    p.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor("#8a8f98"))
    return p


def _palette_light():
    from PySide6 import QtGui

    p = QtGui.QPalette()
    p.setColor(QtGui.QPalette.Window, QtGui.QColor("#f4f5f7"))
    p.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#1c1d20"))
    p.setColor(QtGui.QPalette.Base, QtGui.QColor("#ffffff"))
    p.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#eef0f3"))
    p.setColor(QtGui.QPalette.Text, QtGui.QColor("#1c1d20"))
    p.setColor(QtGui.QPalette.Button, QtGui.QColor("#e7e9ec"))
    p.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#1c1d20"))
    p.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#3d6ea8"))
    p.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    p.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#ffffff"))
    p.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor("#1c1d20"))
    p.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor("#8a8f98"))
    return p


# Shared structural QSS — colors driven by palette via QPalette roles where
# possible, with a few hard-coded accents per mode.
_QSS = """
QWidget { font-size: 13px; }
QMainWindow, QDialog { background: palette(window); color: palette(window-text); }
QLabel { color: palette(window-text); background: transparent; }
QListWidget#sidebar {
    background: palette(alternate-base); border: none; border-right: 1px solid palette(mid);
    outline: 0; font-size: 13px;
}
QListWidget#sidebar::item { padding: 10px 14px; border-radius: 6px; margin: 2px 6px; }
QListWidget#sidebar::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QListWidget#sidebar::item:hover { background: palette(mid); }
QStackedWidget { background: palette(window); }
QTextEdit, QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: palette(base); color: palette(text);
    border: 1px solid palette(mid); border-radius: 6px; padding: 5px 7px;
    selection-background-color: palette(highlight);
}
QTextEdit[readOnly="true"], QPlainTextEdit[readOnly="true"] { background: palette(alternate-base); }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: palette(base); color: palette(text);
    selection-background-color: palette(highlight); border: 1px solid palette(mid); }
QPushButton {
    background: palette(button); color: palette(button-text);
    border: 1px solid palette(mid); border-radius: 6px; padding: 6px 14px;
}
QPushButton:hover { background: palette(mid); }
QPushButton:pressed { background: palette(dark); }
QPushButton:disabled { color: palette(placeholder-text); }
QPushButton#primary { background: palette(highlight); color: palette(highlighted-text); border: none; }
QPushButton#primary:hover { background: palette(dark); }
QPushButton#danger { border: 1px solid #b3413c; }
QTableWidget { background: palette(base); color: palette(text);
    gridline-color: palette(mid); border: 1px solid palette(mid); border-radius: 6px;
    selection-background-color: palette(highlight); selection-color: palette(highlighted-text); }
QHeaderView::section { background: palette(alternate-base); color: palette(window-text);
    padding: 6px; border: none; border-right: 1px solid palette(mid); border-bottom: 1px solid palette(mid); }
QTabWidget::pane { border: 1px solid palette(mid); border-radius: 6px; }
QTabBar::tab { background: palette(alternate-base); color: palette(window-text);
    padding: 6px 14px; border-radius: 6px 6px 0 0; }
QTabBar::tab:selected { background: palette(base); }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: palette(mid); border-radius: 5px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: palette(mid); border-radius: 5px; min-width: 24px; }
QStatusBar { background: palette(alternate-base); color: palette(window-text); }
QStatusBar QLabel { color: palette(window-text); }
QMenu { background: palette(base); color: palette(text); border: 1px solid palette(mid); }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QToolBar { background: palette(window); border: none; spacing: 4px; }
QGroupBox { border: 1px solid palette(mid); border-radius: 6px; margin-top: 10px;
    color: palette(window-text); }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QFrame#card { background: palette(base); border: 1px solid palette(mid); border-radius: 8px; }
"""


def _qss_for(mode: str) -> str:
    if mode == LIGHT:
        return _QSS + """
        .bubble-in { background: #eef0f3; color: #1c1d20; }
        .bubble-out { background: #3d6ea8; color: #ffffff; }
        """
    return _QSS + """
        .bubble-in { background: #2b2e33; color: #e6e6e6; }
        .bubble-out { background: #3d6ea8; color: #ffffff; }
        """


def apply_theme(app, mode: str) -> None:
    """Apply ``mode`` (dark/light) to a QApplication."""
    from PySide6 import QtGui

    if mode == LIGHT:
        app.setPalette(_palette_light())
    else:
        app.setPalette(_palette_dark())
    app.setStyleSheet(_qss_for(mode))


def toggle(app, current: str) -> str:
    nxt = LIGHT if current == DARK else DARK
    apply_theme(app, nxt)
    return nxt