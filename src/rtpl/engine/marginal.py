"""The marginal tax graph: effective marginal rate on each extra dollar.

For each step of the X axis the engine adds ``step`` more dollars of the
chosen income source, recomputes the whole return, and splits the change in
tax into layers. Because the split uses counterfactual recomputations, the
layers always add up exactly to the true change in total tax:

* ``ordinary``  bracket tax on the added dollars themselves
* ``ss``        extra federal income tax because more Social Security became
                taxable (its ordinary tax, plus any gains it pushes out of
                the 0% band)
* ``cg``        tax on added gains or dividends, and gains pushed out of the
                0% band by the added ordinary income itself
* ``senior``    extra federal income tax because the senior deduction shrank
                (again including gains it pushes out of the 0% band)
* ``niit``      Net Investment Income Tax
* ``state``     the state layer

So the capital gains layer stays at the statutory 0/15/20% on the added
income; the knock-on effects of the Social Security and senior deduction
phase-ins are charged to those layers, where they come from.

IRMAA and ACA are cliffs in premiums rather than taxes, so they are
returned as annotations (where along the X axis each threshold is crossed)
instead of being folded into the rate.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from rtpl.model.profile import Profile
from rtpl.taxlaw.loader import FederalLaw, StateLaw, TaxLawLibrary, status_key

from .tax import IncomeSource, TaxResult, compute_tax

LAYERS = ("ordinary", "ss", "cg", "senior", "niit", "state")  # stack order, bottom up
ANNOTATIONS = ("irmaa", "aca")

LAYER_LABELS = {
    "ordinary": "Tax on Ordinary Income",
    "ss": "Social Security Taxation Phase-In",
    "senior": "Enhanced Senior Deduction Phase-Out",
    "cg": "Capital Gains and Qualified Dividends Tax",
    "niit": "Net Investment Income Tax",
    "state": "State Tax",
}


@dataclass(frozen=True)
class Annotation:
    kind: str  # "irmaa" or "aca"
    x: float  # additional income at which the threshold is crossed
    magi: float  # the MAGI threshold itself
    label: str
    detail: str
    annual_cost: float | None = None  # IRMAA: extra premium per year


@dataclass
class MarginalSeries:
    profile: Profile
    source: IncomeSource
    law: FederalLaw
    state: StateLaw | None
    x: list[float]  # left edge of each step
    step: float
    layers: dict[str, list[float]]  # marginal rate per step, as a fraction
    baseline: TaxResult
    results: list[TaxResult]  # one per grid point, len(x) + 1
    annotations: list[Annotation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> list[float]:
        return [sum(self.layers[k][i] for k in LAYERS) for i in range(len(self.x))]

    def layer_label(self, key: str) -> str:
        if key == "state":
            return self.state.label if self.state else "State Tax"
        return LAYER_LABELS[key]

    def nonzero_layers(self, tol: float = 1e-9) -> set[str]:
        return {k for k in LAYERS if any(abs(v) > tol for v in self.layers[k])}

    def rate_changes(self) -> list[tuple[float, float]]:
        """(x, total rate) at every point where the total rate changes.

        Used for the accessible data table, so screen reader users get the
        same information as the chart.
        """
        out: list[tuple[float, float]] = []
        prev = None
        for xi, r in zip(self.x, self.total):
            r = round(r, 4)
            if prev is None or abs(r - prev) > 1e-4:
                out.append((xi, r))
                prev = r
        return out


def build_marginal_series(
    profile: Profile,
    library: TaxLawLibrary,
    *,
    source: IncomeSource = IncomeSource.ORDINARY,
    x_max: float = 250_000,
    step: float = 100,
    include_state: bool = True,
) -> MarginalSeries:
    if step <= 0 or x_max <= 0:
        raise ValueError("x_max and step must be positive")
    law = library.federal(profile.year)
    state = library.state(profile.state) if include_state and profile.state else None

    n = int(round(x_max / step))
    grid = [i * step for i in range(n + 1)]
    results = [compute_tax(profile, law, state, extra=g, source=source) for g in grid]
    layers: dict[str, list[float]] = {k: [] for k in LAYERS}

    for i in range(n):
        a, b = results[i], results[i + 1]
        xb = grid[i + 1]
        # Tax with taxable Social Security and the senior deduction frozen at a
        frozen_both = compute_tax(
            profile, law, state, extra=xb, source=source,
            taxable_ss_override=a.taxable_social_security,
            senior_deduction_override=a.senior_deduction,
        )
        frozen_senior = compute_tax(
            profile, law, state, extra=xb, source=source,
            senior_deduction_override=a.senior_deduction,
        )
        def income_tax(r: TaxResult) -> float:
            return r.ordinary_tax + r.capital_gains_tax

        layers["ordinary"].append((frozen_both.ordinary_tax - a.ordinary_tax) / step)
        layers["cg"].append((frozen_both.capital_gains_tax - a.capital_gains_tax) / step)
        layers["ss"].append((income_tax(frozen_senior) - income_tax(frozen_both)) / step)
        layers["senior"].append((income_tax(b) - income_tax(frozen_senior)) / step)
        layers["niit"].append((b.niit - a.niit) / step)
        layers["state"].append((b.state_tax - a.state_tax) / step)

    series = MarginalSeries(
        profile=profile,
        source=source,
        law=law,
        state=state,
        x=grid[:-1],
        step=step,
        layers=layers,
        baseline=results[0],
        results=results,
    )
    if law.status == "projected":
        series.notes.append(
            f"{profile.year} tax law is projected from {law.projected_from} "
            f"at {law.inflation_used:.1%} inflation."
        )
    series.annotations.extend(irmaa_annotations(series, library))
    series.annotations.extend(aca_annotations(series))
    return series


# -- annotations -------------------------------------------------------------


def _crossing(grid: list[float], values: list[float], threshold: float, strict: bool = True) -> float | None:
    """First X where a non-decreasing ``values`` goes above ``threshold``."""
    if not values:
        return None
    if (values[0] > threshold) if strict else (values[0] >= threshold):
        return None  # already past it at the baseline
    idx = bisect.bisect_right(values, threshold) if strict else bisect.bisect_left(values, threshold)
    if idx >= len(values):
        return None
    lo, hi = idx - 1, idx
    v0, v1 = values[lo], values[hi]
    if v1 == v0:
        return grid[hi]
    return grid[lo] + (threshold - v0) / (v1 - v0) * (grid[hi] - grid[lo])


def medicare_people_in(profile: Profile, year: int) -> int:
    count = 0
    for p in profile.people:
        if p.medicare_part_b is not None:
            count += p.medicare_part_b
        else:
            count += p.age_in(year) >= 65
    return count


def irmaa_annotations(series: MarginalSeries, library: TaxLawLibrary) -> list[Annotation]:
    profile = series.profile
    premium_year = profile.year + series.law.irmaa_lookback
    try:
        law = library.federal(premium_year)
    except KeyError:
        return []
    grid = [i * series.step for i in range(len(series.results))]
    magi = [r.magi_irmaa for r in series.results]
    enrolled = medicare_people_in(profile, premium_year)
    people = enrolled or 1
    estimate = " (estimated)" if law.status == "projected" else ""
    out = []
    prev_b, prev_d = law.irmaa_part_b_standard, 0.0
    for n, tier in enumerate(law.irmaa_tiers, start=1):
        threshold = tier.threshold(profile.filing_status)
        x = _crossing(grid, magi, threshold, strict=True)
        step_cost = ((tier.part_b - prev_b) + (tier.part_d - prev_d)) * 12 * people
        prev_b, prev_d = tier.part_b, tier.part_d
        if x is None:
            continue
        who = f"{people} person" if people == 1 else f"{people} people"
        out.append(
            Annotation(
                kind="irmaa",
                x=x,
                magi=threshold,
                label=f"IRMAA {n}",
                detail=(
                    f"IRMAA tier {n}: MAGI over ${threshold:,.0f} in {profile.year} raises "
                    f"{premium_year} Medicare premiums by about ${step_cost:,.0f}/yr "
                    f"({who}){estimate}"
                ),
                annual_cost=step_cost,
            )
        )
    return out


def aca_annotations(series: MarginalSeries) -> list[Annotation]:
    profile, law = series.profile, series.law
    size = profile.fpl_household_size()
    fpl = law.fpl(size)
    grid = [i * series.step for i in range(len(series.results))]
    magi = [r.magi_aca for r in series.results]
    marks = [
        (law.aca_medicaid_pct, "Medicaid 138%", "Medicaid eligibility ends (expansion states) at {pct:.0%} FPL, MAGI ${t:,.0f}"),
        (law.aca_csr_pct, "CSR 250%", "Cost-sharing reductions end at {pct:.0%} FPL, MAGI ${t:,.0f}"),
    ]
    if not law.aca_enhanced_subsidies:
        marks.append(
            (law.aca_cliff_pct, "ACA cliff 400%",
             "ACA premium tax credit ends entirely above {pct:.0%} FPL, MAGI ${t:,.0f}")
        )
    out = []
    for pct, label, text in marks:
        t = pct * fpl
        x = _crossing(grid, magi, t, strict=True)
        if x is None:
            continue
        out.append(Annotation(kind="aca", x=x, magi=t, label=label, detail=text.format(pct=pct, t=t)))
    if law.aca_enhanced_subsidies:
        series.notes.append(
            f"Enhanced ACA credits apply in {law.year}: no 400% FPL subsidy cliff."
        )
    return out


# -- smart defaults ------------------------------------------------------------


def default_visibility(series: MarginalSeries) -> dict[str, bool]:
    """Which layers and annotations to show unless the user says otherwise.

    The idea is to hide what cannot matter for this household, never to
    stop someone from turning a layer on.
    """
    p = series.profile
    active = series.nonzero_layers()
    vis = {k: k in active for k in LAYERS}
    vis["ordinary"] = True
    # Capital gains layer: on if the household holds a taxable brokerage
    # account, already has gains or dividends, or we are adding them.
    vis["cg"] = (
        "cg" in active
        or series.source is not IncomeSource.ORDINARY
        or p.household.has_taxable_brokerage
    )
    vis["state"] = series.state is not None and series.state.has_income_tax
    # IRMAA uses income from two years before the premium year, so it
    # matters from age 63, or for anyone already on Medicare (e.g. SSDI).
    lookahead = p.year + series.law.irmaa_lookback
    vis["irmaa"] = medicare_people_in(p, lookahead) > 0
    under_65_no_medicare = any(
        person.age_in(p.year) < 65 and not person.on_medicare(p.year) for person in p.people
    )
    vis["aca"] = p.household.aca_marketplace and under_65_no_medicare
    return vis


def filing_status_key(profile: Profile) -> str:
    return status_key(profile.filing_status)
