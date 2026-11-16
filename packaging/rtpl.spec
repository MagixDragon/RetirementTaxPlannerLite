# PyInstaller recipe for a stand-alone RetirementTaxPlannerLite app.
#
#   uv run --with pyinstaller pyinstaller packaging/rtpl.spec --noconfirm
#
# Run it on each platform you want to ship (PyInstaller does not
# cross-compile). Output goes to dist/:
#   Windows  dist/RetirementTaxPlannerLite/RetirementTaxPlannerLite.exe
#   macOS    dist/RetirementTaxPlannerLite.app
#   Linux    dist/RetirementTaxPlannerLite/RetirementTaxPlannerLite
# See docs/packaging.md for icons, shortcuts and installers.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
APP_NAME = "RetirementTaxPlannerLite"

if sys.platform == "win32":
    exe_icon = str(ROOT / "src" / "rtpl" / "data" / "icons" / "rtpl.ico")
elif sys.platform == "darwin":
    exe_icon = str(ROOT / "packaging" / "rtpl.icns")
else:
    exe_icon = None  # Linux executables carry no icon; the .desktop file does

a = Analysis(
    [str(ROOT / "packaging" / "launch_rtpl.py")],
    pathex=[str(ROOT / "src")],
    datas=collect_data_files("rtpl"),  # tax law, themes and icons
    hiddenimports=["matplotlib.backends.backend_qtagg"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=exe_icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

if sys.platform == "darwin":
    from rtpl import __version__

    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=exe_icon,
        bundle_identifier="io.github.magixdragon.rtpl",
        version=__version__,
        info_plist={"NSHighResolutionCapable": True},
    )
