"""Start the desktop app: ``python -m rtpl.app`` or ``rtpl``."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from rtpl.app.main_window import APP_NAME, MainWindow, apply_app_theme

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    window = MainWindow()
    window.effective_mode = apply_app_theme(app, "system")
    window.redraw()
    window.resize(1400, 900)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
