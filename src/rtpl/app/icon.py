"""The app icon: window, taskbar, Dock, About box and desktop shortcuts.

The icon files ship in ``rtpl/data/icons`` (regenerate them with
``packaging/make_icons.py``).
"""

from __future__ import annotations

import sys
from importlib import resources
from pathlib import Path

# Windows groups taskbar buttons by this id. Without it, a window started
# from Python shows Python's icon in the taskbar instead of ours.
APP_USER_MODEL_ID = "RetirementTaxPlannerLite.RTPL"
# Name of the Linux .desktop file (without ".desktop"). Wayland shells look
# the window's icon up through it, because they ignore window icons.
DESKTOP_FILE_NAME = "rtpl"


def icons_dir() -> Path:
    return Path(str(resources.files("rtpl") / "data" / "icons"))


def icon_png_paths() -> list[Path]:
    """The PNG sizes, smallest first."""
    paths = icons_dir().glob("rtpl_*.png")
    return sorted(paths, key=lambda p: int(p.stem.split("_")[1]))


def largest_png() -> Path:
    return icon_png_paths()[-1]


def ico_path() -> Path:
    return icons_dir() / "rtpl.ico"


def app_icon():
    """A QIcon holding every size, so Qt picks a sharp one for each use."""
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for path in icon_png_paths():
        icon.addFile(str(path))
    return icon


def about_pixmap(size: int = 64, device_pixel_ratio: float = 1.0):
    """The icon for the About box, sharp on high-DPI screens too."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    px = round(size * device_pixel_ratio)
    pix = QPixmap(str(largest_png())).scaled(
        px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    pix.setDevicePixelRatio(device_pixel_ratio)
    return pix


def set_windows_app_id() -> None:
    """Call before any window opens. Does nothing outside Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def install_app_icon(app) -> None:
    """Give the whole application our icon (title bars, taskbar, Dock)."""
    app.setWindowIcon(app_icon())
    if sys.platform.startswith("linux"):
        app.setDesktopFileName(DESKTOP_FILE_NAME)
