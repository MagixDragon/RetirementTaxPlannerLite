"""The forward-looking plan: year-by-year income, tax and balances.

Each year:

1. Accounts owned by someone who died last year pass to the survivor.
2. Required distributions: RMDs from traditional accounts (Uniform
   Lifetime, or Joint and Last Survivor for a much younger spouse), and
   inherited IRA RMDs (Single Life) plus emptying in the 10-year deadline
   year.
3. Income streams (wages, pensions, one-off gains), Social Security (own,
   spousal and survivor benefits) and taxable-account yields.
4. Rules: Roth conversions and withdrawals, fixed amounts first, then
   "fill to a target" rules, each solved against the full tax return.
5. Tax with ``compute_tax()`` and that year's (possibly projected) law.
6. Cash flow: income minus spending, tax and IRMAA surcharges. A surplus
   goes into the taxable account; a deficit is taken from cash, then the
   taxable account (realizing gains, which feeds back into the tax).
7. Balances grow by their return.

Threshold lines for the chart (bracket tops, IRMAA tiers, ACA) are given in
the same units as the bars: the total income at which that year's return
would reach the threshold, found by adding or removing ordinary income and
recomputing the return.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from rtpl.model.plan import Account, Plan, Rule
from rtpl.model.profile import Household, Person, Profile
from rtpl.taxlaw.loader import FederalLaw, StateLaw, TaxLawLibrary, status_key

from . import social_security as ssa
from .rmd import RmdTables, default_tables, inherited_first_period, rmd_start_age
from .tax import IncomeSource, TaxResult, compute_tax

# Income categories, in stack order from the bottom. Ordinary income comes
# first, then gains (which stack on top of ordinary income for tax), then
# money that is not taxed at all.
CATEGORIES = (
    "ss",
    "wages",
    "pension",
    "other_ordinary",
    "rmd",
    "inherited",
    "ira_withdrawal",
    "roth_conversion",
    "cg_qd",
    "tax_exempt",
    "roth_withdrawal",
    "principal",
)
TAX_FREE = ("tax_exempt", "roth_withdrawal", "principal")

CATEGORY_LABELS = {
    "ss": "Social Security",
    "wages": "Wages",
    "pension": "Pensions and annuities",
    "other_ordinary": "Interest and other ordinary income",
    "rmd": "RMDs (traditional accounts)",
    "inherited": "Inherited IRA withdrawals",
    "ira_withdrawal": "Other IRA / 401(k) withdrawals",
    "roth_conversion": "Roth conversions",
    "cg_qd": "Capital gains and qualified dividends",
    "tax_exempt": "Tax-exempt interest",
    "roth_withdrawal": "Roth and HSA withdrawals (tax-free)",
    "principal": "Brokerage and cash principal (tax-free)",
}


@dataclass(frozen=True)
class Source:
    category: str
    key: str  # unique per source, for the "show each source" view
    label: str
    amount: float


@dataclass
class YearResult:
    year: int
    ages: dict[int, int]  # person index -> age at year end, living people only
    filing_status: str
    sources: list[Source]
    tax: TaxResult
    law: FederalLaw
    irmaa_surcharge: float
    irmaa_note: str
    balances_start: dict[str, float]
    balances_end: dict[str, float]
    spending: float
    shortfall: float
    lines: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def category_totals(self) -> dict[str, float]:
        out = {c: 0.0 for c in CATEGORIES}
        for s in self.sources:
            out[s.category] += s.amount
        return out

    @property
    def taxable_bar(self) -> float:
        """Height of the bar below the tax-free segments."""
        return sum(s.amount for s in self.sources if s.category not in TAX_FREE)

    @property
    def total_income(self) -> float:
        return sum(s.amount for s in self.sources)


@dataclass
class PlanResult:
    plan: Plan
    years: list[YearResult]
    account_labels: dict[str, str]
    account_kinds: dict[str, str]
    line_labels: dict[str, str]
    notes: list[str] = field(default_factory=list)

    def deflator(self, year: int) -> float:
        return (1 + self.plan.inflation) ** (year - self.plan.start_year)


# -- helpers -------------------------------------------------------------------


def _active_rules(rules: list[Rule], profile: Profile, year: int) -> list[Rule]:
    """For each (kind, account), the rule with the narrowest span wins; ties go to the later rule."""
    best: dict[tuple[str, str], tuple[int, int, Rule]] = {}
    for idx, r in enumerate(rules):
        if not r.enabled:
            continue
        first, last = r.years(profile)
        if not first <= year <= last:
            continue
        key = (r.kind, r.account)
        span = last - first
        cur = best.get(key)
        if cur is None or span < cur[0] or (span == cur[0] and idx > cur[1]):
            best[key] = (span, idx, r)
    chosen = [v for v in best.values()]
    chosen.sort(key=lambda t: t[1])
    return [t[2] for t in chosen]


@dataclass
class _Year:
    """Everything that feeds one year's return. Amounts keyed by person index."""

    ss: dict[int, float] = field(default_factory=dict)
    wages: dict[int, float] = field(default_factory=dict)
    pension: dict[int, float] = field(default_factory=dict)  # domestic pensions and annuities (taxable part)
    ira: dict[int, float] = field(default_factory=dict)  # all traditional/inherited distributions + conversions
    interest: float = 0.0
    nonqualified_div: float = 0.0
    qualified_div: float = 0.0
    ltcg: float = 0.0
    other_ordinary: float = 0.0  # includes the taxable part of foreign pensions
    tax_exempt: float = 0.0

    def add(self, attr: str, owner: int | None, amount: float) -> None:
        d = getattr(self, attr)
        if isinstance(d, dict):
            d[owner if owner is not None else 0] = d.get(owner if owner is not None else 0, 0.0) + amount
        else:
            setattr(self, attr, d + amount)


class PlanEngine:
    def __init__(self, plan: Plan, library: TaxLawLibrary, tables: RmdTables | None = None) -> None:
        problems = plan.validate()
        if problems:
            raise ValueError("; ".join(problems))
        self.plan = plan
        self.library = library
        self.tables = tables or default_tables()
        self.profile = plan.profile
        self.state_law: StateLaw | None = library.state(plan.profile.state) if plan.profile.state else None

    # -- people ----------------------------------------------------------
    def alive(self, i: int, year: int) -> bool:
        dy = self.plan.death_year(i)
        return dy is None or year <= dy

    def living(self, year: int) -> list[int]:
        return [i for i in range(len(self.profile.people)) if self.alive(i, year)]

    def filing_status(self, year: int) -> str:
        fs = self.profile.filing_status
        if len(self.profile.people) == 2 and len(self.living(year)) < 2:
            return "single"  # qualifying surviving spouse is not modelled yet (issue #2)
        return fs

    def survivor_of(self, i: int, year: int) -> int | None:
        others = [j for j in self.living(year) if j != i]
        return others[0] if others else None

    # -- Social Security -------------------------------------------------
    def _ss_own(self, i: int, year: int) -> tuple[float, float]:
        """(yearly own benefit if fully paid this year, share of year paid)."""
        p = self.profile.people[i]
        ss = self.plan.people[i].social_security
        cola = (1 + self.plan.ss_cola) ** (year - self.plan.start_year)
        if ss.ssdi:
            amount = self._ssdi_year(p.birth_year, ss, year) * cola
            return amount, (1.0 if amount > 0 else 0.0)
        if ss.current_benefit is not None:
            return ss.current_benefit * cola, 1.0
        if ss.fra_benefit <= 0:
            # No benefit of their own, but filing still starts any spousal benefit.
            return 0.0, ssa.claim_year_fraction(year, p.birth_year, ss.claim_age)
        factor = ssa.own_benefit_factor(p.birth_year, ss.claim_age)
        return ss.fra_benefit * factor * cola, ssa.claim_year_fraction(year, p.birth_year, ss.claim_age)

    @staticmethod
    def _ssdi_year(birth_year: int, ss, year: int) -> float:
        """Benefit paid in ``year`` (today's dollars) for someone on SSDI.

        SSDI pays the full benefit and becomes a retirement benefit at full
        retirement age. With a claiming age past FRA the benefit is
        suspended from FRA and restarts at that age with delayed credits.
        If SSDI ends early (a return to work), nothing is paid until the
        claiming age, which then works as an ordinary claim.
        """
        pia = ss.fra_benefit or (ss.current_benefit or 0.0)
        fra = ssa.full_retirement_age(birth_year)
        end = ss.ssdi_end_age if ss.ssdi_end_age is not None and ss.ssdi_end_age < fra else fra
        # Share of the year on SSDI: whole years before ``end``, then the months before it.
        on_ssdi = min(1.0, max(0.0, end - (year - birth_year)))
        paid = pia * on_ssdi
        if end < fra:
            claim = max(ss.claim_age, end)
            paid += pia * ssa.own_benefit_factor(birth_year, claim) * ssa.claim_year_fraction(year, birth_year, claim)
        elif ss.claim_age > fra:
            factor = ssa.own_benefit_factor(birth_year, ss.claim_age)
            paid += pia * factor * ssa.claim_year_fraction(year, birth_year, ss.claim_age)
        else:
            paid += pia * (1.0 - on_ssdi) * (1.0 if year - birth_year >= int(fra) else 0.0)
        return paid

    def _ss_claim_year(self, i: int) -> int:
        p = self.profile.people[i]
        ss = self.plan.people[i].social_security
        if ss.current_benefit is not None or ss.ssdi:
            return self.plan.start_year - 1
        return p.birth_year + int(ss.claim_age)

    def social_security(self, year: int) -> dict[int, float]:
        out: dict[int, float] = {}
        people = self.profile.people
        cola = (1 + self.plan.ss_cola) ** (year - self.plan.start_year)
        for i in self.living(year):
            own, frac = self._ss_own(i, year)
            amount = own * frac
            j = 1 - i if len(people) == 2 else None
            if j is not None and self.alive(j, year):
                # Spousal top-up once both have filed (deemed filing means
                # filing for one is filing for both).
                other_own, other_frac = self._ss_own(j, year)
                if frac > 0 and other_frac > 0:
                    pia_self = self.plan.people[i].social_security.fra_benefit * cola
                    pia_other = self.plan.people[j].social_security.fra_benefit * cola
                    excess = max(0.0, 0.5 * pia_other - pia_self)
                    if excess > 0:
                        start = max(self._ss_claim_year(i), self._ss_claim_year(j))
                        age_at_start = start - people[i].birth_year
                        amount += excess * ssa.spousal_factor(people[i].birth_year, age_at_start) * min(frac, other_frac)
            elif j is not None and not self.alive(j, year):
                # Survivor: the larger of their own benefit and the deceased's.
                dy = self.plan.death_year(j)
                dj_own, _ = self._ss_own(j, year)
                claimed = self._ss_claim_year(j) <= dy
                pia_dead = self.plan.people[j].social_security.fra_benefit * cola
                surv = ssa.survivor_amount(pia_dead, dj_own if claimed else None)
                amount = max(own if frac > 0 else 0.0, surv)
            if amount > 0:
                out[i] = amount
        return out

    # -- incomes ---------------------------------------------------------
    def stream_amount(self, s, year: int) -> float:
        first = s.start.resolve(self.profile) if s.start else self.plan.start_year
        last = s.end.resolve(self.profile) if s.end else 10**6
        if not first <= year <= last:
            return 0.0
        amount = s.amount * (1 + s.growth) ** (year - max(first, self.plan.start_year))
        if s.owner is not None and s.owner < len(self.profile.people) and not self.alive(s.owner, year):
            if s.kind in ("pension", "foreign_pension", "annuity"):
                return amount * s.survivor_pct
            if s.kind == "wages":
                return 0.0
        return amount

    # -- RMDs ------------------------------------------------------------
    def required_distribution(self, acct: Account, balance: float, year: int, first_periods: dict) -> float:
        if balance <= 0 or acct.owner is None:
            return 0.0
        people = self.profile.people
        owner = people[acct.owner]
        age = owner.age_in(year)
        if acct.is_traditional:
            if age < rmd_start_age(owner.birth_year):
                return 0.0
            spouse = self.survivor_of(acct.owner, year)
            spouse_age = people[spouse].age_in(year) if spouse is not None else None
            period = self.tables.owner_period(age, spouse_age, acct.spouse_sole_beneficiary)
            return balance / period
        if acct.kind == "inherited_ira" and acct.inherited_year is not None:
            deadline = acct.deadline_year
            if deadline is not None and year >= deadline:
                return balance
            if year <= acct.inherited_year:
                return 0.0
            if not (acct.owner_started_rmds or acct.eligible_designated_beneficiary):
                return 0.0
            if acct.id not in first_periods:
                first_periods[acct.id] = inherited_first_period(
                    self.tables, owner.birth_year, acct.inherited_year,
                    acct.original_owner_birth_year, acct.owner_started_rmds,
                )
            period = first_periods[acct.id] - (year - acct.inherited_year - 1)
            return balance if period <= 1 else balance / period
        return 0.0

    # -- the tax return --------------------------------------------------
    def build_profile(self, year: int, y: _Year, filing_status: str) -> Profile:
        base = self.profile
        infl = (1 + self.plan.inflation) ** (year - self.plan.start_year)
        people = []
        for i in self.living(year):
            src = base.people[i]
            people.append(
                Person(
                    name=src.name,
                    birth_year=src.birth_year,
                    medicare_part_b=src.medicare_part_b,
                    blind=src.blind,
                    social_security=y.ss.get(i, 0.0),
                    wages=y.wages.get(i, 0.0),
                    pension=y.pension.get(i, 0.0),
                    ira_distributions=y.ira.get(i, 0.0),
                )
            )
        h = base.household
        household = Household(
            taxable_interest=y.interest,
            tax_exempt_interest=y.tax_exempt,
            qualified_dividends=y.qualified_div,
            nonqualified_dividends=y.nonqualified_div,
            long_term_capital_gains=y.ltcg,
            other_ordinary_income=y.other_ordinary,
            adjustments=h.adjustments * infl,
            itemized_deductions=h.itemized_deductions * infl,
            has_taxable_brokerage=h.has_taxable_brokerage,
            aca_marketplace=h.aca_marketplace,
            household_size=h.household_size if len(people) == len(base.people) else None,
        )
        return Profile(year=year, filing_status=filing_status, state=base.state, people=people,
                       household=household, name=base.name, inflation=self.plan.inflation)

    def law(self, year: int) -> FederalLaw:
        return self.library.federal(year, self.plan.inflation)

    # -- fill targets ----------------------------------------------------
    def _target(self, target: str, year: int, prof: Profile, law: FederalLaw):
        """Return (metric(TaxResult) -> float, limit)."""
        fs = status_key(prof.filing_status)
        kind, _, arg = target.partition(":")
        if kind == "bracket":
            rate = float(arg)
            sched = law.ordinary[fs]
            tops = [(b, r) for b, r in sched.brackets]
            limit = None
            for k, (b, r) in enumerate(tops):
                if abs(r - rate) < 1e-9 and k + 1 < len(tops):
                    limit = tops[k + 1][0]
            if limit is None:
                raise ValueError(f"No bracket at {rate:.0%} with a top in {year}")
            return (lambda r: r.taxable_income - r.preferential_income), limit
        if kind == "cg0":
            return (lambda r: r.taxable_income), law.capital_gains[fs].thresholds()[0]
        if kind == "irmaa":
            n = int(arg or 1)
            plaw = self.law(year + law.irmaa_lookback)
            tier = plaw.irmaa_tiers[n - 1]
            return (lambda r: r.magi_irmaa), tier.threshold(prof.filing_status) - 1
        if kind == "aca":
            pct = float(arg or 4.0)
            return (lambda r: r.magi_aca), pct * law.fpl(prof.fpl_household_size())
        if kind == "magi":
            return (lambda r: r.magi_irmaa), float(arg)
        raise ValueError(f"Unknown fill target {target!r}")

    def solve_extra_ordinary(self, prof: Profile, law: FederalLaw, metric, limit: float,
                             lo: float, hi: float, tol: float = 1.0) -> float:
        """Largest extra ordinary income in [lo, hi] with metric <= limit."""
        f = lambda x: metric(compute_tax(prof, law, self.state_law, extra=x, source=IncomeSource.ORDINARY))  # noqa: E731
        if f(lo) > limit:
            return lo
        if f(hi) <= limit:
            return hi
        for _ in range(60):
            if hi - lo <= tol:
                break
            mid = (lo + hi) / 2
            if f(mid) <= limit:
                lo = mid
            else:
                hi = mid
        return lo

    # -- the run ---------------------------------------------------------
    def run(self, *, with_lines: bool = True) -> PlanResult:
        plan, profile = self.plan, self.profile
        accounts = [copy.deepcopy(a) for a in plan.accounts]
        by_id = {a.id: a for a in accounts}
        bal = {a.id: a.balance for a in accounts}
        basis = {a.id: (a.cost_basis if a.cost_basis is not None else a.balance) for a in accounts
                 if a.kind in ("taxable", "cash")}
        for a in accounts:
            if a.kind == "cash":
                basis[a.id] = bal[a.id]
        first_periods: dict[str, float] = {}
        magi_history: dict[int, tuple[float, str]] = {
            int(k): (float(v), profile.filing_status) for k, v in plan.assumptions.prior_magi.items()
        }
        labels = {a.id: a.label(profile) for a in accounts}
        result = PlanResult(plan=plan, years=[], account_labels=labels,
                            account_kinds={a.id: a.kind for a in accounts}, line_labels={})
        if not any(a.kind in ("taxable", "cash") for a in accounts):
            # Somewhere for surpluses and deficits to go.
            cash = Account(id="_cash", kind="cash", owner=None, name="Cash (surplus)", balance=0.0)
            accounts.append(cash)
            by_id[cash.id] = cash
            bal[cash.id] = 0.0
            basis[cash.id] = 0.0
            labels[cash.id] = cash.name
            result.account_kinds[cash.id] = "cash"
        roth_for: dict[int, str] = {}
        for a in accounts:
            if a.kind == "roth_ira" and a.owner is not None:
                roth_for.setdefault(a.owner, a.id)

        for year in range(plan.start_year, plan.end_year + 1):
            living = self.living(year)
            if not living:
                break
            # 1. accounts of someone who died last year pass to the survivor
            for a in accounts:
                if a.owner is not None and not self.alive(a.owner, year):
                    surv = self.survivor_of(a.owner, year)
                    if surv is not None:
                        a.owner = surv
                        labels[a.id] = a.label(profile) + " (inherited from spouse)" if not a.name else labels[a.id]
                        if a.kind == "roth_ira":
                            roth_for.setdefault(surv, a.id)
            start_bal = dict(bal)
            fs = self.filing_status(year)
            law = self.law(year)
            y = _Year()
            sources: list[Source] = []
            withdrawn: dict[str, float] = {a.id: 0.0 for a in accounts}
            notes: list[str] = []

            def take_from(acct: Account, amount: float, category: str, label: str) -> float:
                amount = max(0.0, min(amount, bal[acct.id] - withdrawn[acct.id]))
                if amount <= 0:
                    return 0.0
                withdrawn[acct.id] += amount
                owner = acct.owner if acct.owner is not None else living[0]
                if acct.is_traditional or acct.kind == "inherited_ira":
                    y.add("ira", owner, amount)
                    sources.append(Source(category, f"{category}:{acct.id}", label, amount))
                elif acct.is_roth or acct.kind == "hsa":
                    sources.append(Source("roth_withdrawal", f"roth_withdrawal:{acct.id}", label, amount))
                return amount

            # 2. required distributions
            for a in accounts:
                req = self.required_distribution(a, bal[a.id], year, first_periods)
                if req > 0:
                    cat = "inherited" if a.kind == "inherited_ira" else "rmd"
                    what = "required withdrawal" if a.kind == "inherited_ira" else "RMD"
                    take_from(a, req, cat, f"{labels[a.id]} {what}")
                    if a.deadline_year == year:
                        notes.append(f"{labels[a.id]}: 10-year deadline, remaining balance withdrawn")

            # 3. incomes
            for s in plan.incomes:
                amt = self.stream_amount(s, year)
                if amt <= 0:
                    continue
                owner = s.owner if s.owner is not None and s.owner < len(profile.people) else None
                if owner is not None and not self.alive(owner, year):
                    owner = self.survivor_of(owner, year)
                label = s.label(profile)
                key = f"income:{plan.incomes.index(s)}"
                taxable = amt * s.federal_taxable_pct
                if s.kind == "wages":
                    y.add("wages", owner, taxable)
                    sources.append(Source("wages", key, label, amt))
                elif s.kind in ("pension", "annuity"):
                    y.add("pension", owner, taxable)
                    sources.append(Source("pension", key, label, amt))
                elif s.kind == "foreign_pension":
                    y.add("other_ordinary", owner, taxable)
                    sources.append(Source("pension", key, label, amt))
                elif s.kind == "interest":
                    y.add("interest", owner, taxable)
                    sources.append(Source("other_ordinary", key, label, amt))
                elif s.kind == "qualified_dividends":
                    y.add("qualified_div", owner, taxable)
                    sources.append(Source("cg_qd", key, label, amt))
                elif s.kind == "ltcg":
                    y.add("ltcg", owner, taxable)
                    sources.append(Source("cg_qd", key, label, amt))
                elif s.kind == "tax_exempt":
                    y.add("tax_exempt", owner, amt)
                    sources.append(Source("tax_exempt", key, label, amt))
                else:
                    y.add("other_ordinary", owner, taxable)
                    sources.append(Source("other_ordinary", key, label, amt))

            for i, amt in self.social_security(year).items():
                y.add("ss", i, amt)
                sources.append(Source("ss", f"ss:{i}", f"{profile.people[i].name} Social Security", amt))

            # taxable-account yields (reinvested)
            reinvested: dict[str, float] = {}
            for a in accounts:
                if a.kind != "taxable" or bal[a.id] <= 0:
                    continue
                b = bal[a.id]
                interest = b * (a.interest_yield + a.nonqualified_dividend_yield)
                qd = b * a.qualified_dividend_yield
                cgd = b * a.cap_gain_distribution_yield
                y.interest += b * a.interest_yield
                y.nonqualified_div += b * a.nonqualified_dividend_yield
                y.qualified_div += qd
                y.ltcg += cgd
                if interest:
                    sources.append(Source("other_ordinary", f"yield:{a.id}", f"{labels[a.id]} interest", interest))
                if qd + cgd:
                    sources.append(Source("cg_qd", f"yield:{a.id}", f"{labels[a.id]} dividends and distributions", qd + cgd))
                reinvested[a.id] = interest + qd + cgd
            for a in accounts:
                if a.kind == "cash" and bal[a.id] > 0:
                    intr = bal[a.id] * plan.assumptions.cash_return
                    y.interest += intr
                    sources.append(Source("other_ordinary", f"yield:{a.id}", f"{labels[a.id]} interest", intr))
                    reinvested[a.id] = intr

            # 4. rules: fixed, spread and all first, then fill rules
            conversions: dict[str, tuple[str, float]] = {}
            active = _active_rules(plan.rules, profile, year)
            fills = []
            for r in active:
                if r.mode == "fill":
                    fills.append(r)
                    continue
                self._apply_rule(r, year, by_id, bal, withdrawn, take_from, y, sources, conversions,
                                 labels, roth_for, None, law, fs, basis, notes)
            for r in fills:
                self._apply_rule(r, year, by_id, bal, withdrawn, take_from, y, sources, conversions,
                                 labels, roth_for, r.target, law, fs, basis, notes)
            for acct_id, a in by_id.items():  # Roth accounts created by conversions
                if a not in accounts:
                    accounts.append(a)
                    withdrawn.setdefault(acct_id, 0.0)
                    result.account_kinds[acct_id] = a.kind

            # 5-6. tax and cash flow, iterating on gains from sales that pay for a deficit
            spending = plan.assumptions.spending * (1 + plan.inflation) ** (year - plan.start_year)
            surcharge, irmaa_note = self._irmaa(year, magi_history, living, fs)
            sale_ltcg = 0.0
            sale_plan: list[tuple[str, float, float]] = []
            for _ in range(6):
                y_try = copy.deepcopy(y)
                y_try.ltcg += sale_ltcg
                prof = self.build_profile(year, y_try, fs)
                tax = compute_tax(prof, law, self.state_law)
                inflow = sum(s.amount for s in sources if s.key.startswith(("income:", "ss:")))
                inflow += sum(s.amount for s in sources if s.category in ("rmd", "inherited", "ira_withdrawal", "roth_withdrawal"))
                inflow += sum(amt for (_, amt, _) in sale_plan_other(sources))
                outflow = spending + tax.total_tax + surcharge
                net = inflow - outflow
                new_plan, new_gain, short = self._cover_deficit(-net, accounts, bal, withdrawn, basis)
                if abs(new_gain - sale_ltcg) < 1.0:
                    sale_plan, sale_ltcg = new_plan, new_gain
                    break
                sale_plan, sale_ltcg = new_plan, new_gain
            y.ltcg += sale_ltcg
            prof = self.build_profile(year, y, fs)
            tax = compute_tax(prof, law, self.state_law)
            shortfall = short if net < 0 else 0.0
            for acct_id, amount, gain in sale_plan:
                withdrawn[acct_id] += amount
                if gain > 0:
                    sources.append(Source("cg_qd", f"sale:{acct_id}", f"{labels[acct_id]} gains on sales", gain))
                if amount - gain > 0:
                    sources.append(Source("principal", f"sale:{acct_id}", f"{labels[acct_id]} principal", amount - gain))
            surplus = max(0.0, net) if net >= 0 else 0.0
            if shortfall > 0:
                notes.append(f"Short ${shortfall:,.0f}: spending and tax exceed income and cash/brokerage")

            # 7. balances
            for a in accounts:
                rate = plan.assumptions.cash_return if a.kind == "cash" else (
                    a.return_rate if a.return_rate is not None else plan.assumptions.return_rate)
                b = bal[a.id] - withdrawn[a.id]
                if a.id in basis and bal[a.id] > 0 and a.kind == "taxable":
                    sold_frac = min(1.0, withdrawn[a.id] / bal[a.id]) if bal[a.id] else 0.0
                    basis[a.id] *= (1 - sold_frac)
                elif a.kind == "cash":
                    basis[a.id] = max(0.0, b)
                bal[a.id] = b * (1 + rate)
                if a.id in reinvested and a.id in basis:
                    basis[a.id] += reinvested[a.id]
            for target_id, amount in _conversion_totals(conversions).items():
                bal[target_id] = bal.get(target_id, 0.0) + amount * (1 + plan.assumptions.return_rate)
            if surplus > 0:
                dest = next((a for a in accounts if a.kind == "taxable"), None) or next(
                    a for a in accounts if a.kind == "cash")
                bal[dest.id] += surplus
                basis[dest.id] = basis.get(dest.id, 0.0) + surplus
            for a in accounts:
                if a.kind == "cash":
                    basis[a.id] = bal[a.id]

            magi_history[year] = (tax.magi_irmaa, fs)
            yr = YearResult(
                year=year,
                ages={i: profile.people[i].age_in(year) for i in living},
                filing_status=fs,
                sources=[s for s in sources if s.amount > 0.005],
                tax=tax,
                law=law,
                irmaa_surcharge=surcharge,
                irmaa_note=irmaa_note,
                balances_start=start_bal,
                balances_end={k: max(0.0, v) for k, v in bal.items()},
                spending=spending,
                shortfall=shortfall,
                notes=notes,
            )
            if with_lines:
                yr.lines = self.threshold_lines(yr, prof, law, result.line_labels)
            result.years.append(yr)

        projected = [yr.year for yr in result.years if yr.law.status == "projected"]
        if projected:
            result.notes.append(
                f"Tax law for {projected[0]} on is projected at {plan.inflation:.1%} inflation."
            )
        return result

    # -- rule application -------------------------------------------------
    def _apply_rule(self, r: Rule, year, by_id, bal, withdrawn, take_from, y, sources, conversions,
                    labels, roth_for, target, law, fs, basis, notes) -> None:
        acct = by_id.get(r.account)
        if acct is None:
            return
        available = bal[acct.id] - withdrawn[acct.id]
        if available <= 0:
            return
        first, last = r.years(self.profile)
        # A withdrawal rule's amount is the year's total from that account, so
        # an RMD already taken counts toward it (RMDs are a floor, not extra).
        # Conversions come on top of any RMD, which cannot be converted.
        already = withdrawn[acct.id] if r.kind == "withdrawal" else 0.0
        if r.mode == "fixed":
            amount = r.amount - already
        elif r.mode == "spread":
            amount = bal[acct.id] / max(1, last - year + 1) - already
        elif r.mode == "all":
            amount = available
        else:  # fill
            if acct.kind in ("taxable", "cash", "roth_ira", "roth_401k", "hsa"):
                notes.append(f"Fill rules apply to traditional and inherited accounts only ({labels[acct.id]})")
                return
            prof = self.build_profile(year, y, fs)
            try:
                metric, limit = self._target(target, year, prof, law)
            except (ValueError, IndexError) as exc:
                notes.append(str(exc))
                return
            amount = self.solve_extra_ordinary(prof, law, metric, limit, 0.0, available)
        amount = min(amount, available)
        if amount <= 0:
            return
        if r.kind == "roth_conversion":
            if not (acct.is_traditional or acct.kind == "inherited_ira") or acct.kind == "inherited_ira":
                notes.append(f"Roth conversions need a traditional account ({labels[acct.id]})")
                return
            owner = acct.owner if acct.owner is not None else 0
            dest = r.to_account or roth_for.get(owner)
            if dest is None:
                dest = f"_roth{owner}"
                roth_for[owner] = dest
                by_id[dest] = Account(id=dest, kind="roth_ira", owner=owner, balance=0.0)
                bal[dest] = 0.0
                labels[dest] = by_id[dest].label(self.profile)
            withdrawn[acct.id] += amount
            y.add("ira", owner, amount)
            sources.append(Source("roth_conversion", f"roth_conversion:{acct.id}",
                                  f"{labels[acct.id]} Roth conversion", amount))
            conversions[f"{acct.id}->{dest}:{len(conversions)}"] = (dest, amount)
            return
        # withdrawal
        if acct.kind in ("taxable", "cash"):
            b = bal[acct.id]
            gain = 0.0
            if acct.kind == "taxable" and b > 0:
                gain = amount * max(0.0, 1 - basis.get(acct.id, b) / b)
            withdrawn[acct.id] += amount
            y.ltcg += gain
            if gain:
                sources.append(Source("cg_qd", f"withdrawal:{acct.id}", f"{labels[acct.id]} gains on sales", gain))
            if amount - gain:
                sources.append(Source("principal", f"withdrawal:{acct.id}", f"{labels[acct.id]} principal", amount - gain))
            return
        cat = "inherited" if acct.kind == "inherited_ira" else "ira_withdrawal"
        take_from(acct, amount, cat, f"{labels[acct.id]} withdrawal")

    def _cover_deficit(self, deficit: float, accounts, bal, withdrawn, basis):
        """Sell from cash, then taxable accounts. Returns (sales, realized gain, shortfall)."""
        sales: list[tuple[str, float, float]] = []
        gain_total = 0.0
        need = max(0.0, deficit)
        for kind in ("cash", "taxable"):
            for a in accounts:
                if need <= 0 or a.kind != kind:
                    continue
                avail = bal[a.id] - withdrawn[a.id]
                take = min(need, max(0.0, avail))
                if take <= 0:
                    continue
                gain = 0.0
                if kind == "taxable" and bal[a.id] > 0:
                    gain = take * max(0.0, 1 - basis.get(a.id, bal[a.id]) / bal[a.id])
                sales.append((a.id, take, gain))
                gain_total += gain
                need -= take
        return sales, gain_total, need

    # -- IRMAA -----------------------------------------------------------
    def _irmaa(self, year: int, history: dict, living: list[int], fs: str) -> tuple[float, str]:
        law = self.law(year)
        enrolled = [i for i in living if self.profile.people[i].on_medicare(year)]
        if not enrolled:
            return 0.0, ""
        look = year - law.irmaa_lookback
        if look in history:
            magi, look_fs = history[look]
            note = f"IRMAA {year}: MAGI ${magi:,.0f} from {look}"
        elif history:
            first = min(history)
            magi, look_fs = history[first]
            note = f"IRMAA {year}: {look} MAGI not entered; using {first}"
        else:
            return 0.0, f"IRMAA {year}: needs {look} MAGI (Assumptions, prior MAGI); not charged"
        cost = 0.0
        for tier in law.irmaa_tiers:
            if magi > tier.threshold(look_fs):
                cost = (tier.part_b - law.irmaa_part_b_standard) + tier.part_d
        return cost * 12 * len(enrolled), note

    # -- threshold lines ---------------------------------------------------
    def threshold_lines(self, yr: YearResult, prof: Profile, law: FederalLaw, labels: dict[str, str]) -> dict[str, float]:
        fs = status_key(prof.filing_status)
        base = yr.taxable_bar
        ordinary_now = sum(
            s.amount for s in yr.sources if s.category not in TAX_FREE and s.category not in ("cg_qd", "ss")
        )
        lo, hi = -ordinary_now - 1.0, 5_000_000.0
        out: dict[str, float] = {}

        gains = yr.category_totals()["cg_qd"]

        def line(key: str, label: str, metric, limit: float, ordinary_only: bool = False) -> None:
            # Ordinary bracket tops are read against the top of the ordinary
            # segments (gains sit above them, as they do on the return);
            # everything else is read against the whole taxable bar.
            delta = self.solve_extra_ordinary(prof, law, metric, limit, lo, hi, tol=5.0)
            out[key] = max(0.0, base - (gains if ordinary_only else 0.0) + delta)
            labels.setdefault(key, label)

        line("deduction", "Deductions (taxable income starts)", lambda r: r.taxable_income, 0.0)
        sched = law.ordinary[fs].brackets
        for k in range(len(sched) - 1):
            rate, top = sched[k][1], sched[k + 1][0]
            line(f"bracket:{rate:g}", f"Top of {rate:.0%} bracket", lambda r: r.taxable_income - r.preferential_income, top,
                 ordinary_only=True)
        cg_top = law.capital_gains[fs].thresholds()[0]
        line("cg0", "Top of 0% capital gains band", lambda r: r.taxable_income, cg_top)
        try:
            plaw = self.law(yr.year + law.irmaa_lookback)
            for n, tier in enumerate(plaw.irmaa_tiers, start=1):
                line(f"irmaa:{n}", f"IRMAA tier {n} (premiums 2 years later)", lambda r: r.magi_irmaa,
                     tier.threshold(prof.filing_status))
        except KeyError:
            pass
        fpl = law.fpl(prof.fpl_household_size())
        line("aca:4", "ACA 400% of poverty line", lambda r: r.magi_aca, law.aca_cliff_pct * fpl)
        return out


def sale_plan_other(sources: list[Source]):
    """Cash from rule withdrawals out of brokerage and cash accounts (already in sources)."""
    for s in sources:
        if s.key.startswith("withdrawal:"):
            yield (s.key, s.amount, 0.0)


def _conversion_totals(conversions: dict[str, tuple[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for dest, amount in conversions.values():
        out[dest] = out.get(dest, 0.0) + amount
    return out


def run_plan(plan: Plan, library: TaxLawLibrary, **kw) -> PlanResult:
    return PlanEngine(plan, library).run(**kw)


# -- default visibility ----------------------------------------------------------


def default_line_visibility(result: PlanResult) -> dict[str, bool]:
    """Suggested lines: the bracket tops and IRMAA tiers the bars come near,
    the 0% gains band if there are gains, ACA if the household buys
    marketplace coverage. Every line can be turned on or off."""

    def near(key: str, slack: float = 1.15) -> bool:
        return any(yr.lines.get(key, math.inf) <= max(yr.total_income, 1.0) * slack for yr in result.years)

    vis: dict[str, bool] = {}
    keys = list(result.line_labels)
    brackets = [k for k in keys if k.startswith("bracket:")]
    reached = [k for k in brackets if near(k)]
    for k in brackets:
        vis[k] = k in reached and k != "bracket:0.1"
    above = [k for k in brackets if k not in reached]
    if above:
        vis[above[0]] = True  # the next bracket up, so the headroom shows
    medicare = any(
        result.plan.profile.people[i].on_medicare(yr.year + 2) for yr in result.years for i in yr.ages
    )
    for k in keys:
        if k.startswith("irmaa:"):
            vis[k] = medicare and near(k, 1.05)
    vis["aca:4"] = result.plan.profile.household.aca_marketplace
    vis["deduction"] = False
    vis["cg0"] = any(yr.category_totals()["cg_qd"] > 0 for yr in result.years)
    vis["tax_free"] = True
    return vis


# -- text table ------------------------------------------------------------------


def year_table(result: PlanResult, *, todays_dollars: bool = False) -> tuple[list[str], list[list[str]]]:
    """The plan as rows of text: for the data table tab, CSV export and screen readers."""
    people = result.plan.profile.people
    cats = [c for c in CATEGORIES if any(yr.category_totals()[c] for yr in result.years)]
    header = ["Year", "Ages", "Filing"] + [CATEGORY_LABELS[c] for c in cats] + [
        "Total income", "AGI", "Taxable income", "IRMAA MAGI", "Federal tax", "State tax",
        "IRMAA surcharge", "Total balances", "Notes",
    ]
    rows = []
    for yr in result.years:
        f = 1 / result.deflator(yr.year) if todays_dollars else 1.0
        money = lambda v: f"{v * f:,.0f}"  # noqa: E731
        ct = yr.category_totals()
        ages = ", ".join(f"{people[i].name} {a}" for i, a in yr.ages.items())
        rows.append(
            [str(yr.year), ages, yr.filing_status.upper()]
            + [money(ct[c]) for c in cats]
            + [money(yr.total_income), money(yr.tax.agi), money(yr.tax.taxable_income), money(yr.tax.magi_irmaa),
               money(yr.tax.federal_tax), money(yr.tax.state_tax), money(yr.irmaa_surcharge),
               money(sum(yr.balances_end.values())), "; ".join(yr.notes)]
        )
    return header, rows
