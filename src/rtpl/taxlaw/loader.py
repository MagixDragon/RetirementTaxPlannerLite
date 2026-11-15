"""Load federal and state tax law from TOML files.

Search order: any user folders passed in (newest wins), then the data that
ships with the package. A user folder that contains ``federal/2026.toml``
replaces the shipped 2026 file entirely, so people can correct or extend the
law without touching code.

Years with no data file are projected from the newest enacted year using
``federal/projection.toml``: indexed amounts grow with inflation and policy
switches listed under ``[sunsets]`` turn off after their last year.
"""

from __future__ import annotations

import copy
import math
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Iterable

FILING_STATUSES = ("single", "mfj", "mfs", "hoh")


def status_key(filing_status: str) -> str:
    """Map a filing status to the key used in the data files."""
    fs = filing_status.lower()
    if fs == "qss":  # qualifying surviving spouse uses joint values
        return "mfj"
    if fs not in FILING_STATUSES:
        raise ValueError(f"Unknown filing status: {filing_status!r}")
    return fs


@dataclass(frozen=True)
class Schedule:
    """A progressive rate schedule: sorted ``(lower_bound, rate)`` pairs."""

    brackets: tuple[tuple[float, float], ...]

    @classmethod
    def from_pairs(cls, pairs: Iterable[Iterable[float]]) -> "Schedule":
        rows = tuple(sorted((float(a), float(r)) for a, r in pairs))
        if not rows or rows[0][0] != 0:
            raise ValueError("A schedule must start at 0")
        return cls(rows)

    def tax(self, amount: float) -> float:
        """Tax on ``amount`` under this schedule."""
        return self.tax_between(0.0, amount)

    def tax_between(self, lo: float, hi: float) -> float:
        """Tax on the slice of income from ``lo`` to ``hi`` (stacked)."""
        if hi <= lo:
            return 0.0
        total = 0.0
        for i, (start, rate) in enumerate(self.brackets):
            end = self.brackets[i + 1][0] if i + 1 < len(self.brackets) else math.inf
            a, b = max(lo, start), min(hi, end)
            if b > a:
                total += (b - a) * rate
        return total

    def rate_at(self, amount: float) -> float:
        rate = self.brackets[0][1]
        for start, r in self.brackets:
            if amount >= start:
                rate = r
        return rate

    def thresholds(self) -> list[float]:
        return [b for b, _ in self.brackets[1:]]

    def scaled(self, factor: float, step: float) -> "Schedule":
        return Schedule(
            tuple((_round_down(b * factor, step) if b else 0.0, r) for b, r in self.brackets)
        )


def _round_down(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step + 1e-9) * step


def _by_status(table: dict, cast=float) -> dict[str, float]:
    return {k: cast(table[k]) for k in FILING_STATUSES}


@dataclass(frozen=True)
class IrmaaTier:
    single: float
    mfj: float
    mfs: float
    part_b: float
    part_d: float

    def threshold(self, filing_status: str) -> float:
        key = status_key(filing_status)
        return {"single": self.single, "hoh": self.single, "mfj": self.mfj, "mfs": self.mfs}[key]


@dataclass(frozen=True)
class FederalLaw:
    year: int
    status: str  # "enacted" or "projected"
    sources: tuple[str, ...]
    personal_exemption: float
    senior_deduction_enabled: bool
    aca_enhanced_subsidies: bool
    ordinary: dict[str, Schedule]
    standard_deduction: dict[str, float]
    additional_std_unmarried: float
    additional_std_married: float
    senior_amount: float
    senior_phaseout_rate: float
    senior_phaseout_per_person: bool
    senior_first_year: int
    senior_last_year: int
    senior_mfs_eligible: bool
    senior_phaseout_start: dict[str, float]
    capital_gains: dict[str, Schedule]
    capital_loss_limit: dict[str, float]
    niit_rate: float
    niit_threshold: dict[str, float]
    ss_base: dict[str, float]
    ss_adjusted: dict[str, float]
    irmaa_premium_year: int
    irmaa_lookback: int
    irmaa_part_b_standard: float
    irmaa_tiers: tuple[IrmaaTier, ...]
    aca_fpl_first: float
    aca_fpl_additional: float
    aca_medicaid_pct: float
    aca_csr_pct: float
    aca_cliff_pct: float
    projected_from: int | None = None
    inflation_used: float | None = None

    @property
    def senior_deduction_active(self) -> bool:
        return (
            self.senior_deduction_enabled
            and self.senior_first_year <= self.year <= self.senior_last_year
        )

    def fpl(self, household_size: int) -> float:
        return self.aca_fpl_first + self.aca_fpl_additional * max(0, household_size - 1)


def _parse_federal(raw: dict) -> FederalLaw:
    pol = raw.get("policy", {})
    sd = raw["senior_deduction"]
    irmaa = raw["irmaa"]
    aca = raw["aca"]
    cg = raw["capital_gains"]
    return FederalLaw(
        year=int(raw["year"]),
        status=str(raw.get("status", "enacted")),
        sources=tuple(raw.get("sources", ())),
        personal_exemption=float(pol.get("personal_exemption", 0.0)),
        senior_deduction_enabled=bool(pol.get("senior_deduction", True)),
        aca_enhanced_subsidies=bool(pol.get("aca_enhanced_subsidies", False)),
        ordinary={k: Schedule.from_pairs(raw["ordinary_brackets"][k]) for k in FILING_STATUSES},
        standard_deduction=_by_status(raw["standard_deduction"]),
        additional_std_unmarried=float(raw["additional_standard_deduction"]["unmarried"]),
        additional_std_married=float(raw["additional_standard_deduction"]["married"]),
        senior_amount=float(sd["amount_per_person"]),
        senior_phaseout_rate=float(sd["phaseout_rate"]),
        senior_phaseout_per_person=bool(sd.get("phaseout_per_person", True)),
        senior_first_year=int(sd["first_year"]),
        senior_last_year=int(sd["last_year"]),
        senior_mfs_eligible=bool(sd.get("mfs_eligible", False)),
        senior_phaseout_start=_by_status(sd["phaseout_start"]),
        capital_gains={k: Schedule.from_pairs(cg[k]) for k in FILING_STATUSES},
        capital_loss_limit=_by_status(cg["capital_loss_limit"]),
        niit_rate=float(raw["niit"]["rate"]),
        niit_threshold=_by_status(raw["niit"]["threshold"]),
        ss_base=_by_status(raw["social_security"]["base"]),
        ss_adjusted=_by_status(raw["social_security"]["adjusted"]),
        irmaa_premium_year=int(irmaa["premium_year"]),
        irmaa_lookback=int(irmaa.get("lookback_years", 2)),
        irmaa_part_b_standard=float(irmaa["part_b_standard"]),
        irmaa_tiers=tuple(
            IrmaaTier(
                single=float(t["single"]),
                mfj=float(t["mfj"]),
                mfs=float(t["mfs"]),
                part_b=float(t["part_b"]),
                part_d=float(t["part_d"]),
            )
            for t in irmaa["tiers"]
        ),
        aca_fpl_first=float(aca["fpl_first_person"]),
        aca_fpl_additional=float(aca["fpl_each_additional"]),
        aca_medicaid_pct=float(aca["medicaid_expansion_pct"]),
        aca_csr_pct=float(aca["csr_limit_pct"]),
        aca_cliff_pct=float(aca["subsidy_cliff_pct"]),
    )


@dataclass(frozen=True)
class StateLaw:
    code: str
    name: str
    year: int | None
    taxes_social_security: bool
    capital_gains_as_ordinary: bool
    exempt_retirement_income: bool
    brackets: dict[str, Schedule]
    standard_deduction: dict[str, float]
    exemption_per_person: float = 0.0
    surcharges: tuple[tuple[float, float], ...] = ()  # (rate, threshold)
    has_income_tax: bool = True

    @property
    def label(self) -> str:
        return f"State Tax ({self.code})"


def _parse_state(raw: dict) -> StateLaw:
    surcharges = tuple(
        (float(v["rate"]), float(v["threshold"])) for v in raw.get("surcharges", {}).values()
    )
    return StateLaw(
        code=str(raw["code"]).upper(),
        name=str(raw.get("name", raw["code"])),
        year=raw.get("year"),
        taxes_social_security=bool(raw.get("taxes_social_security", False)),
        capital_gains_as_ordinary=bool(raw.get("capital_gains_as_ordinary", True)),
        exempt_retirement_income=bool(raw.get("exempt_retirement_income", False)),
        brackets={k: Schedule.from_pairs(raw["brackets"][k]) for k in FILING_STATUSES},
        standard_deduction=_by_status(raw.get("standard_deduction", {k: 0 for k in FILING_STATUSES})),
        exemption_per_person=float(raw.get("exemption", {}).get("per_person", 0.0)),
        surcharges=surcharges,
    )


def _no_tax_state(code: str) -> StateLaw:
    zero = Schedule.from_pairs([[0, 0.0]])
    return StateLaw(
        code=code,
        name=code,
        year=None,
        taxes_social_security=False,
        capital_gains_as_ordinary=True,
        exempt_retirement_income=False,
        brackets={k: zero for k in FILING_STATUSES},
        standard_deduction={k: 0.0 for k in FILING_STATUSES},
        has_income_tax=False,
    )


@dataclass
class TaxLawLibrary:
    """Finds and caches tax law by year (federal) and code (state)."""

    user_dirs: list[Path] = field(default_factory=list)
    inflation: float | None = None  # None = use projection.toml default
    _federal_raw: dict[int, dict] = field(default_factory=dict, init=False, repr=False)
    _cache: dict[tuple[int, float], FederalLaw] = field(default_factory=dict, init=False, repr=False)
    _projection: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.user_dirs = [Path(p) for p in self.user_dirs]
        self._load_federal_files()

    # -- file discovery -------------------------------------------------
    def _roots(self) -> list[Path]:
        shipped = Path(str(resources.files("rtpl") / "data"))
        return [*self.user_dirs, shipped]

    def _first_existing(self, rel: str) -> Path | None:
        for root in self._roots():
            p = root / rel
            if p.is_file():
                return p
        return None

    def _load_federal_files(self) -> None:
        # Lowest priority first so user files override shipped ones.
        for root in reversed(self._roots()):
            fed = root / "federal"
            if not fed.is_dir():
                continue
            for p in fed.glob("*.toml"):
                with p.open("rb") as fh:
                    raw = tomllib.load(fh)
                if p.stem == "projection":
                    self._projection = raw
                elif p.stem.isdigit():
                    self._federal_raw[int(p.stem)] = raw
        if not self._federal_raw:
            raise FileNotFoundError("No federal tax law files found")

    # -- federal --------------------------------------------------------
    @property
    def enacted_years(self) -> list[int]:
        return sorted(self._federal_raw)

    @property
    def default_inflation(self) -> float:
        if self.inflation is not None:
            return self.inflation
        return float(self._projection.get("default_inflation", 0.025))

    def federal(self, year: int, inflation: float | None = None) -> FederalLaw:
        """Law for ``year``; ``inflation`` only matters for projected years."""
        rate = self.default_inflation if inflation is None else float(inflation)
        key = (year, 0.0 if year in self._federal_raw else rate)
        if key not in self._cache:
            self._cache[key] = self._build_federal(year, rate)
        return self._cache[key]

    def _build_federal(self, year: int, rate: float) -> FederalLaw:
        if year in self._federal_raw:
            return _parse_federal(self._federal_raw[year])
        years = self.enacted_years
        if year < years[0]:
            raise KeyError(f"No tax law data for {year} (earliest is {years[0]})")
        base_year = max(y for y in years if y < year)
        raw = copy.deepcopy(self._federal_raw[base_year])
        n = year - base_year
        factor = (1 + rate) ** n
        idx = self._projection.get("indexed", {})
        sunsets = self._projection.get("sunsets", {})

        def scale_pairs(table: dict, step: float) -> None:
            for k in FILING_STATUSES:
                table[k] = [
                    [(_round_down(b * factor, step) if b else 0), r] for b, r in table[k]
                ]

        if "ordinary_brackets" in idx:
            scale_pairs(raw["ordinary_brackets"], idx["ordinary_brackets"])
        if "capital_gains" in idx:
            scale_pairs(raw["capital_gains"], idx["capital_gains"])
        if "standard_deduction" in idx:
            step = idx["standard_deduction"]
            for k in FILING_STATUSES:
                raw["standard_deduction"][k] = _round_down(raw["standard_deduction"][k] * factor, step)
        if "additional_standard_deduction" in idx:
            step = idx["additional_standard_deduction"]
            for k in ("unmarried", "married"):
                raw["additional_standard_deduction"][k] = _round_down(
                    raw["additional_standard_deduction"][k] * factor, step
                )
        if "irmaa_thresholds" in idx:
            step = idx["irmaa_thresholds"]
            # The top tier was frozen in law and starts indexing in a given year.
            top_from = int(self._projection.get("irmaa_top_tier_indexed_from", 0))
            top_n = max(0, year - max(base_year, top_from - 1)) if top_from else n
            tiers = raw["irmaa"]["tiers"]
            for i, t in enumerate(tiers):
                f = (1 + rate) ** top_n if i == len(tiers) - 1 else factor
                for k in ("single", "mfj", "mfs"):
                    t[k] = _round_down(t[k] * f, step)
            # Premiums themselves grow too; use the same factor as an estimate.
            raw["irmaa"]["part_b_standard"] *= factor
            for t in tiers:
                t["part_b"] *= factor
                t["part_d"] *= factor
        raw["irmaa"]["premium_year"] = year
        if "aca_fpl" in idx:
            step = idx["aca_fpl"]
            raw["aca"]["fpl_first_person"] = _round_down(raw["aca"]["fpl_first_person"] * factor, step)
            raw["aca"]["fpl_each_additional"] = _round_down(
                raw["aca"]["fpl_each_additional"] * factor, step
            )
        for flag, last in sunsets.items():
            if year > int(last):
                raw.setdefault("policy", {})[flag] = False
        raw["year"] = year
        raw["status"] = "projected"
        law = _parse_federal(raw)
        return _replace(law, projected_from=base_year, inflation_used=rate)

    # -- state ----------------------------------------------------------
    def available_states(self) -> list[str]:
        codes: set[str] = set()
        for root in self._roots():
            sdir = root / "states"
            if sdir.is_dir():
                codes.update(p.stem.upper() for p in sdir.glob("*.toml") if not p.stem.startswith("_"))
        return sorted(codes | set(self._no_tax_codes()))

    def _no_tax_codes(self) -> list[str]:
        p = self._first_existing("states/_no_income_tax.toml")
        if not p:
            return []
        with p.open("rb") as fh:
            return [c.upper() for c in tomllib.load(fh).get("codes", [])]

    def state(self, code: str | None) -> StateLaw | None:
        if not code:
            return None
        code = code.upper()
        p = self._first_existing(f"states/{code}.toml")
        if p:
            with p.open("rb") as fh:
                return _parse_state(tomllib.load(fh))
        if code in self._no_tax_codes():
            return _no_tax_state(code)
        raise KeyError(f"No state tax data for {code}")


def _replace(law: FederalLaw, **changes) -> FederalLaw:
    from dataclasses import replace

    return replace(law, **changes)
