# App icon and packaging

## Where the icon comes from

The master art is `packaging/source-icon.ico` (256x256). The script
`packaging/make_icons.py` turns it into:

| File | Used for |
| --- | --- |
| `src/rtpl/data/icons/rtpl_16.png` ... `rtpl_256.png` | Title bar, taskbar, macOS Dock, About box, Linux menus |
| `src/rtpl/data/icons/rtpl.ico` (16-256 px inside) | Windows `.exe` icon and shortcuts |
| `packaging/rtpl.icns` | macOS `.app` bundle |

To change the icon, replace the master (a square PNG of 1024x1024 is best,
256x256 at least) and run:

```bash
uv run --with pillow python packaging/make_icons.py path/to/new-icon.png
```

## Running from source (`uv run rtpl`)

* **Title bar, taskbar, Dock:** the app sets its icon at start-up. On
  Windows it also sets an AppUserModelID, without which the taskbar would
  show Python's icon.
* **About box:** shows the icon.
* **Shortcut:** `uv run rtpl-shortcut` makes one with the icon.
  * Windows: on the Desktop and in the Start menu.
  * Linux: in the applications menu (and on `~/Desktop` if it exists).
  * macOS: not available from source; build the `.app` instead.

  Run it again if you move the project folder.
* **Linux on Wayland** shows the icon only once the `.desktop` file from
  `rtpl-shortcut` is installed, because Wayland ignores window icons.

## Stand-alone apps (PyInstaller)

Build on the OS you are building for (no cross-compiling):

```bash
uv run --with pyinstaller pyinstaller packaging/rtpl.spec --noconfirm
```

| OS | Result | Icon |
| --- | --- | --- |
| Windows | `dist/RetirementTaxPlannerLite/RetirementTaxPlannerLite.exe` | In the `.exe`, so Explorer, shortcuts and the taskbar show it |
| macOS | `dist/RetirementTaxPlannerLite.app` | In the bundle, so Finder, Dock and Launchpad show it |
| Linux | `dist/RetirementTaxPlannerLite/RetirementTaxPlannerLite` | Linux programs carry no icon; `sh packaging/linux/install.sh` installs the build with a menu entry and icons |

The **build apps** workflow on GitHub (Actions tab, "Run workflow") builds
all three, checks that each one starts, and offers them as downloads.

Next steps not done yet: a Windows installer (for example Inno Setup,
which would also make the Start-menu shortcut), macOS code signing and
notarization, and a Linux AppImage or Flatpak.
