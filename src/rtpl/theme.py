"""Chart color themes (light and dark), loaded from TOML."""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class Palette:
    mode: str
    surface: str
    text_primary: str
    text_secondary: str
    grid: str
    total_line: str
    irmaa: str
    aca: str
    layers: dict[str, str]


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Theme:
    raw: dict

    @classmethod
    def load(cls, override: str | Path | None = None) -> "Theme":
        shipped = resources.files("rtpl") / "data" / "themes" / "default.toml"
        raw = tomllib.loads(shipped.read_text(encoding="utf-8"))
        if override and Path(override).is_file():
            with open(override, "rb") as fh:
                raw = _merge(raw, tomllib.load(fh))
        return cls(raw)

    def palette(self, mode: str) -> Palette:
        if mode not in ("light", "dark"):
            raise ValueError(mode)
        d = self.raw[mode]
        return Palette(
            mode=mode,
            surface=d["surface"],
            text_primary=d["text_primary"],
            text_secondary=d["text_secondary"],
            grid=d["grid"],
            total_line=d["total_line"],
            irmaa=d["irmaa"],
            aca=d["aca"],
            layers=dict(d["layers"]),
        )

    def set_layer_color(self, mode: str, layer: str, color: str) -> None:
        self.raw[mode]["layers"][layer] = color

    def to_toml(self) -> str:
        lines = [f'name = "{self.raw.get("name", "Custom")}"', ""]
        for mode in ("light", "dark"):
            d = self.raw[mode]
            lines.append(f"[{mode}]")
            lines += [f'{k} = "{v}"' for k, v in d.items() if not isinstance(v, dict)]
            lines += ["", f"[{mode}.layers]"]
            lines += [f'{k} = "{v}"' for k, v in d["layers"].items()]
            lines.append("")
        return "\n".join(lines)
