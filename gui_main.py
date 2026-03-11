#!/usr/bin/env python3
"""
Entry point for the PyQt-based Virtual Oscilloscope GUI.

This keeps the GUI classes in `oscilloscope_gui.py` and the launcher here,
similar to how the CLI entry point is separated in `main.py`.
"""

from __future__ import annotations

import sys
import platform
from PyQt6 import QtWidgets
from PyQt6.QtGui import QIcon

from oscilloscope_gui import OscilloscopeMainWindow


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QIcon("res/kamin_logo.png"))

    if platform.system() == "Windows":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "kamin.oscilloscope.app"
        )

    window = OscilloscopeMainWindow()
    window.setWindowIcon(QIcon("res/kamin_logo.png"))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

