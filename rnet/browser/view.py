"""PySide6 browser view. Imports Qt lazily so the model stays headless-safe.

Renders basic HTML via :class:`QTextBrowser` (no WebEngine dependency, fits the
low-bandwidth mesh web which is mostly text+links). Shows a per-page identity
verification indicator: ``verified`` (green) when the RHTTP response signature
checks out against the resolved ``.rns`` host identity, ``unverified`` (red)
otherwise.

:func:`BrowserWidget` is an embeddable QWidget (used by the dashboard and by
:func:`launch_browser`). The async model runs on a provided asyncio loop;
results are marshalled back to the GUI thread via a Qt signal.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from rnet.browser.model import BrowserModel, Page


def _import_qt():
    from PySide6 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui


class BrowserWidget:
    """Embeddable browser widget. Construct with a model + a running asyncio loop.

    Call :meth:`navigate` to load a URL. Emits ``page_ready`` (a Qt signal on
    the internal bridge) when a page loads; connect via ``widget.bridge``.
    """

    def __init__(self, model: BrowserModel, loop: asyncio.AbstractEventLoop):
        self.model = model
        self.loop = loop
        QtWidgets, QtCore, QtGui = _import_qt()
        self.QtWidgets = QtWidgets
        self.QtCore = QtCore

        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.widget)
        self.widget.setLayout(layout)

        bar = QtWidgets.QHBoxLayout()
        self.back_btn = QtWidgets.QPushButton("←")
        self.fwd_btn = QtWidgets.QPushButton("→")
        self.refresh_btn = QtWidgets.QPushButton("⟳")
        self.url_field = QtWidgets.QLineEdit()
        self.url_field.setPlaceholderText("name.rns or rhttp://name.rns/path")
        self.go_btn = QtWidgets.QPushButton("Go")
        for w in (self.back_btn, self.fwd_btn, self.refresh_btn):
            bar.addWidget(w)
        bar.addWidget(self.url_field, 1)
        bar.addWidget(self.go_btn)
        layout.addLayout(bar)

        self.view = QtWidgets.QTextBrowser()
        self.view.setOpenExternalLinks(False)
        layout.addWidget(self.view, 1)

        self.status = QtWidgets.QLabel("ready")
        layout.addWidget(self.status)

        # Signal bridge for cross-thread result delivery.
        class _Bridge(QtCore.QObject):
            page_ready = QtCore.Signal(object)

        self.bridge = _Bridge()
        self.bridge.page_ready.connect(self._render)

        self.go_btn.clicked.connect(lambda: self.navigate(self.url_field.text()))
        self.url_field.returnPressed.connect(lambda: self.navigate(self.url_field.text()))
        self.back_btn.clicked.connect(self._back)
        self.fwd_btn.clicked.connect(self._forward)
        self.refresh_btn.clicked.connect(lambda: self.navigate(self.url_field.text(), use_cache=False))
        self.view.anchorClicked.connect(lambda u: self.navigate(u.toString()))

    # -- navigation -------------------------------------------------------
    def navigate(self, raw_url: str, use_cache: bool = True) -> None:
        url = self.model.normalize_url(raw_url)
        self.url_field.setText(url)
        self.status.setText("loading…")
        self.view.setText("<i>loading…</i>")

        def worker():
            try:
                page = asyncio.run_coroutine_threadsafe(
                    self.model.navigate(raw_url, use_cache=use_cache), self.loop
                ).result(timeout=60)
            except Exception as exc:
                page = Page(url=url, error=str(exc))
            self.bridge.page_ready.emit(page)

        threading.Thread(target=worker, daemon=True).start()

    def _render(self, page: Page) -> None:
        if page.error:
            self.view.setText(f"<b>error:</b> {page.error}")
            self.status.setText("error")
            return
        self.view.setHtml(page.html or "")
        self.url_field.setText(page.final_url or page.url)
        color = "#0a0" if page.verified else "#a00"
        word = "verified" if page.verified else "unverified"
        self.status.setText(
            f"<span style='color:{color}'>● {word}</span>  {page.host}  "
            f"({page.status})  hash={page.content_hash.hex()[:12]}"
        )

    def _back(self) -> None:
        url = self.model.back_url()
        if url:
            self.navigate(url)

    def _forward(self) -> None:
        url = self.model.forward_url()
        if url:
            self.navigate(url)

    def add_bookmark(self) -> None:
        self.model.add_bookmark(self.url_field.text(), self.view.documentTitle() or self.url_field.text())


def launch_browser(model: BrowserModel) -> int:
    """Run the browser as a standalone window. Requires a display (or offscreen)."""
    QtWidgets, QtCore, QtGui = _import_qt()
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setWindowTitle("RNet Browser")

    bw = BrowserWidget(model, loop)

    # Bookmarks menu.
    menu = win.menuBar().addMenu("Bookmarks")
    add_bm = menu.addAction("Add current page")
    menu.addSeparator()

    def refresh_bookmarks():
        for a in menu.actions()[2:]:
            menu.removeAction(a)
        for b in model.bookmarks():
            act = menu.addAction(b["title"] or b["url"])
            act.triggered.connect(lambda _=False, u=b["url"]: bw.navigate(u))

    add_bm.triggered.connect(lambda: (bw.add_bookmark(), refresh_bookmarks()))
    refresh_bookmarks()

    win.setCentralWidget(bw.widget)
    win.show()
    return app.exec()