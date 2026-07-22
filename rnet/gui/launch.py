"""Launch the RNet GUI dashboard."""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from rnet.config import default_datadir
from rnet.gui.settings_store import SettingsStore
from rnet.gui import theme

log = logging.getLogger(__name__)


def main(argv: Optional[list] = None) -> int:
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
        sys.argv if argv is None else argv
    )
    app.setApplicationName("RNet")
    app.setQuitOnLastWindowClosed(True)

    # Theme from persisted settings before any window is shown so there is
    # no light flash on a dark-preferring user.
    datadir = os.environ.get("RNET_DATADIR") or default_datadir()
    settings = SettingsStore(os.path.join(datadir, "settings.json"))
    theme.apply_theme(app, settings.get("theme", "dark"))

    from rnet.gui.bridge import make_bridge
    from rnet.gui.controller import GuiController
    from rnet.gui.app import MainWindow

    bridge = make_bridge()
    rns_configdir = os.environ.get("RNET_RNS_CONFIGDIR")
    controller = GuiController(
        datadir=os.environ.get("RNET_DATADIR"),
        rns_configdir=rns_configdir,
        bridge=bridge,
    )

    window = MainWindow(controller, bridge, app, settings)
    window.show()

    def _about_to_quit():
        controller.shutdown()

    app.aboutToQuit.connect(_about_to_quit)

    # Auto-start the node so the app "just works" like meshchatX. Errors are
    # surfaced in the status bar / Status tab log, not a crash.
    if controller.settings.get("autostart", True):
        def _on_err(exc):
            if bridge is not None:
                bridge.log.emit(f"autostart failed: {exc}")

        # Defer slightly so the window paints first.
        from PySide6 import QtCore
        QtCore.QTimer.singleShot(150, lambda: controller.autostart(
            on_done=lambda _n: bridge.log.emit("node started") if bridge else None,
            on_error=_on_err))

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())