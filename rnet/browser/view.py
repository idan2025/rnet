"""PySide6 browser window. Imports Qt lazily so the model stays headless-safe.

Renders basic HTML via :class:`QTextBrowser` (no WebEngine dependency, fits the
low-bandwidth mesh web which is mostly text+links). Shows a per-page identity
verification indicator: ``verified`` (green) when the RHTTP response signature
checks out against the resolved ``.rns`` host identity, ``unverified`` (red)
otherwise.

The async model runs on a background asyncio loop; results are marshalled back
to the GUI thread via a Qt signal.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from rnet.browser.model import BrowserModel, Page


def _import_qt():
    from PySide6 import QtWidgets, QtCore, QtGui
    return QtWidgets, QtCore, QtGui


def launch_browser(model: BrowserModel) -> int:
    """Run the browser GUI. Requires a display (or QT_QPA_PLATFORM=offscreen)."""
    QtWidgets, QtCore, QtGui = _import_qt()

    class _Bridge(QtCore.QObject):
        page_ready = QtCore.Signal(object)

    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = QtWidgets.QMainWindow()
    win.setWindowTitle("RNet Browser")
    bridge = _Bridge()

    central = QtWidgets.QWidget()
    win.setCentralWidget(central)
    layout = QtWidgets.QVBoxLayout(central)

    bar = QtWidgets.QHBoxLayout()
    back_btn = QtWidgets.QPushButton("←")
    fwd_btn = QtWidgets.QPushButton("→")
    refresh_btn = QtWidgets.QPushButton("⟳")
    url_field = QtWidgets.QLineEdit()
    url_field.setPlaceholderText("name.rns or rhttp://name.rns/path")
    go_btn = QtWidgets.QPushButton("Go")
    for w in (back_btn, fwd_btn, refresh_btn):
        bar.addWidget(w)
    bar.addWidget(url_field, 1)
    bar.addWidget(go_btn)
    layout.addLayout(bar)

    view = QtWidgets.QTextBrowser()
    view.setOpenExternalLinks(False)
    layout.addWidget(view, 1)

    status = QtWidgets.QLabel("ready")
    layout.addWidget(status)

    bookmark_menu = win.menuBar().addMenu("Bookmarks")
    add_bm = bookmark_menu.addAction("Add current page")
    bookmark_menu.addSeparator()

    def render(page: Page):
        if page.error:
            view.setText(f"<b>error:</b> {page.error}")
            status.setText("error")
            return
        view.setHtml(page.html or "")
        url_field.setText(page.final_url or page.url)
        color = "#0a0" if page.verified else "#a00"
        word = "verified" if page.verified else "unverified"
        status.setText(
            f"<span style='color:{color}'>● {word}</span>  {page.host}  "
            f"({page.status})  hash={page.content_hash.hex()[:12]}"
        )

    bridge.page_ready.connect(render)

    def navigate(raw_url: str, use_cache: bool = True):
        url = model.normalize_url(raw_url)
        url_field.setText(url)
        status.setText("loading…")
        view.setText("<i>loading…</i>")

        def worker():
            try:
                page = asyncio.run_coroutine_threadsafe(
                    model.navigate(raw_url, use_cache=use_cache), loop
                ).result(timeout=60)
            except Exception as exc:
                page = Page(url=url, error=str(exc))
            bridge.page_ready.emit(page)

        threading.Thread(target=worker, daemon=True).start()

    def refresh_bookmarks():
        for a in bookmark_menu.actions()[2:]:
            bookmark_menu.removeAction(a)
        for b in model.bookmarks():
            act = bookmark_menu.addAction(b["title"] or b["url"])
            act.triggered.connect(lambda _=False, u=b["url"]: navigate(u))

    def go_back():
        url = model.back_url()
        if url:
            navigate(url)

    def go_forward():
        url = model.forward_url()
        if url:
            navigate(url)

    go_btn.clicked.connect(lambda: navigate(url_field.text()))
    url_field.returnPressed.connect(lambda: navigate(url_field.text()))
    back_btn.clicked.connect(go_back)
    fwd_btn.clicked.connect(go_forward)
    refresh_btn.clicked.connect(lambda: navigate(url_field.text(), use_cache=False))
    view.anchorClicked.connect(lambda u: navigate(u.toString()))
    add_bm.triggered.connect(lambda: (
        model.add_bookmark(url_field.text(), view.documentTitle() or url_field.text()),
        refresh_bookmarks(),
    ))

    refresh_bookmarks()
    win.show()
    return app.exec()