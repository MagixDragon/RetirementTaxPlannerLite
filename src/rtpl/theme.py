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

    @staticmethod
    def default_raw() -> dict:
        shipped = resources.files("rtpl") / "data" / "themes" / "default.toml"
        return tomllib.loads(shipped.read_text(encoding="utf-8"))

    @classmethod
    def load(cls, override: str | Path | None = None) -> "Theme":
        raw = cls.default_raw()
        if override and Path(override).is_file():
            with open(override, "rb") as fh:
                raw = _merge(raw, tomllib.load(fh))
        return cls(raw)

    @property
    def linked(self) -> bool:
        """True when dark mode uses the light-mode layer colors."""
        return bool(self.raw.get("same_layer_colors", False))

    @linked.setter
    def linked(self, value: bool) -> None:
        self.raw["same_layer_colors"] = bool(value)

    def layer_colors(self, mode: str) -> dict[str, str]:
        return dict(self.raw["light" if self.linked else mode]["layers"])

    def reset_layers(self) -> None:
        """Put every layer color back to the shipped defaults."""
        defaults = self.default_raw()
        for mode in ("light", "dark"):
            self.raw[mode]["layers"] = dict(defaults[mode]["layers"])

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
            layers=self.layer_colors(mode),
        )

    def set_layer_color(self, mode: str, layer: str, color: str) -> None:
        self.raw[mode]["layers"][layer] = color

    def to_toml(self) -> str:
        lines = [
            f'name = "{self.raw.get("name", "Custom")}"',
            f"same_layer_colors = {'true' if self.linked else 'false'}",
            "",
        ]
        for mode in ("light", "dark"):
            d = self.raw[mode]
            lines.append(f"[{mode}]")
            lines += [f'{k} = "{v}"' for k, v in d.items() if not isinstance(v, dict)]
            lines += ["", f"[{mode}.layers]"]
            lines += [f'{k} = "{v}"' for k, v in d["layers"].items()]
            lines.append("")
        return "\n".join(lines)
