"""Entry point for PyInstaller builds (see packaging/rtpl.spec)."""

from rtpl.app.__main__ import main

raise SystemExit(main())
