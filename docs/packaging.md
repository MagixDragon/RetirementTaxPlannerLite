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

## Builds on GitHub

The **build apps** workflow builds all three, checks that each one starts,
and zips them. It runs:

* **On every push to `main`** (so after each merged pull request). The zips
  are on that run's page in the Actions tab, under *Artifacts*, for 30 days.
  You must be signed in to GitHub to download them.
* **On a version tag**, which makes a GitHub Release. The zips are attached
  to the release on the Releases page, named like
  `RetirementTaxPlannerLite-0.2.0-Windows.zip`, and stay there for good.
  Anyone who can see the repository can download them.
* On pull requests that change packaging or app code, and by hand
  (Actions tab, "build apps", "Run workflow").

### Making a release

1. Set the new version in both `pyproject.toml` and `src/rtpl/__init__.py`,
   and merge that to `main`.
2. Tag it and push the tag (the tag must be `v` plus that version, or the
   build stops):

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

   Or on GitHub: Releases, "Draft a new release", type `v0.2.0` as a new
   tag on `main`, and publish. The workflow then attaches the zips to it
   (if you publish this way, the workflow adds the zips to your release
   instead of making a second one).

### Opening a downloaded build

The builds are not code-signed, so each OS warns the first time:

* **Windows:** unzip, run `RetirementTaxPlannerLite.exe`. If SmartScreen
  says "Windows protected your PC", click *More info*, then *Run anyway*.
* **macOS** (Apple Silicon): unzip, drag the app to Applications, then
  right-click it, *Open*, and *Open* again. On recent macOS, if that does
  not offer *Open*, go to System Settings, Privacy & Security, and click
  *Open Anyway*.
* **Linux** (x86-64): unzip and run
  `RetirementTaxPlannerLite/RetirementTaxPlannerLite`.

Next steps not done yet: an Intel Mac build, a Windows installer (for example Inno Setup,
which would also make the Start-menu shortcut), macOS code signing and
notarization, and a Linux AppImage or Flatpak.
