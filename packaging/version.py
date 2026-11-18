"""The app's version, read from its one home: src/rtpl/__init__.py.

Everything else gets the version from there: the package metadata
(pyproject.toml reads it), the About box, the Windows .exe version
resource and the macOS Info.plist (packaging/rtpl.spec), and the release
tag that GitHub makes (.github/workflows/build.yml).

    python packaging/version.py                  print it, e.g. 0.2.0
    python packaging/version.py --newer-than 0.1.0
                                                 fail unless it is higher
    python packaging/version.py --of FILE        read FILE instead
"""

import argparse
import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / "src" / "rtpl" / "__init__.py"
PATTERN = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)


def read(text: str) -> str:
    m = PATTERN.search(text)
    if not m:
        sys.exit('No __version__ = "MAJOR.MINOR.PATCH" line found')
    return ".".join(m.groups())


def parts(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in version.split("."))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--of", type=Path, default=INIT)
    ap.add_argument("--newer-than")
    args = ap.parse_args()
    version = read(args.of.read_text(encoding="utf-8"))
    if args.newer_than and parts(version) <= parts(args.newer_than):
        sys.exit(
            f"Version {version} is not higher than {args.newer_than} on main. "
            "Raise __version__ in src/rtpl/__init__.py (see docs/packaging.md)."
        )
    print(version)


if __name__ == "__main__":
    main()
