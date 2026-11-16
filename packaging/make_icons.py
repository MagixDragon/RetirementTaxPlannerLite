"""Regenerate the app icon set from one square master image.

    uv run --with pillow python packaging/make_icons.py [master.png|master.ico]

The master defaults to ``packaging/source-icon.ico``. Use the largest art
you have (1024x1024 is ideal, 256x256 is the minimum). Writes, into
``src/rtpl/data/icons``:

* ``rtpl_<N>.png`` for N in 16..1024, up to the master's size (window,
  taskbar, About box, Linux menus)
* ``rtpl.ico`` with 16-256 px images (Windows exe and shortcuts)

and ``packaging/rtpl.icns`` (macOS app bundle; only the build needs it).

Pillow is only needed to run this script, not to run the app.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "rtpl" / "data" / "icons"
PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def load_master(path: Path) -> Image.Image:
    im = Image.open(path)
    if path.suffix.lower() == ".ico":  # take the biggest image in the file
        im.size = max(im.info.get("sizes", {im.size}))
    im = im.convert("RGBA")
    if im.width != im.height:
        raise SystemExit(f"{path} is {im.width}x{im.height}; the icon must be square")
    return im


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else ROOT / "packaging" / "source-icon.ico"
    master = load_master(src)
    print(f"master: {src} ({master.width}x{master.height})")
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("rtpl_*.png"):
        old.unlink()
    for n in [n for n in PNG_SIZES if n <= master.width]:
        master.resize((n, n), Image.LANCZOS).save(OUT / f"rtpl_{n}.png", optimize=True)
    big = master.resize((256, 256), Image.LANCZOS)
    big.save(OUT / "rtpl.ico", sizes=[(n, n) for n in ICO_SIZES])
    master.save(ROOT / "packaging" / "rtpl.icns")
    print(f"wrote {OUT} and packaging/rtpl.icns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
