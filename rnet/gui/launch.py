"""Launch the RNet GUI dashboard."""
from __future__ import annotations

import os
import sys
from typing import Optional


def main(argv: Optional[list] = None) -> int:
    from PySide6 import QtWidgets

    # Offscreen-safe: constructing the app + window works without a display.
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv if argv is None else argv)

    from rnet.gui.bridge import make_bridge
    from rnet.gui.controller import GuiController
    from rnet.gui.app import MainWindow

    bridge = make_bridge()
    datadir = os.environ.get("RNET_DATADIR")
    rns_configdir = os.environ.get("RNET_RNS_CONFIGDIR")
    controller = GuiController(datadir=datadir, rns_configdir=rns_configdir, bridge=bridge)

    window = MainWindow(controller, bridge)
    window.show()

    def _about_to_quit():
        controller.shutdown()

    app.aboutToQuit.connect(_about_to_quit)
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())