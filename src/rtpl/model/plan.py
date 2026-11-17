"""The forward-looking plan: a baseline profile plus accounts, income
streams, Social Security choices, assumptions and a short rule list.

Design points (see docs/design.md, "Forward-looking plan"):

* Ages are the age a person reaches by December 31 of the year. Timings
  are stored against one person's age (or, if the user prefers, a
  calendar year) and shown in whatever form is convenient.
* Accounts are individual: each spouse can have several of each type.
  The graph groups them by type unless asked to show each one.
* Rules move money between accounts (Roth conversions, withdrawals). For
  each account and kind of rule, the rule with the narrowest time span
  wins in a year, so a one-year rule overrides a range rule.
* Withdrawals happen only where a rule (or an RMD) says so. Money left
  over after spending and taxes goes into the taxable account; a deficit
  is taken from cash, then the taxable account, and anything still short
  is reported as a shortfall.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .profile import Profile

PLAN_SCHEMA_VERSION = 1

ACCOUNT_KINDS = {
    "traditional_ira": "Traditional IRA",
    "401k": "401(k)/403(b)",
    "inherited_ira": "Inherited IRA",
    "roth_ira": "Roth IRA",
    "roth_401k": "Roth 401(k)",
    "hsa": "HSA",
    "taxable": "Taxable brokerage",
    "cash": "Cash / savings",
}
TRADITIONAL_KINDS = ("traditional_ira", "401k")
ROTH_KINDS = ("roth_ira", "roth_401k")

INCOME_KINDS = {
    "wages": "Wages",
    "pension": "Pension",
    "foreign_pension": "Foreign pension",
    "annuity": "Annuity",
    "interest": "Interest",
    "qualified_dividends": "Qualified dividends",
    "ltcg": "Long-term capital gain",
    "other_ordinary": "Other ordinary income",
    "tax_exempt": "Tax-exempt income",
}

RULE_KINDS = {"roth_conversion": "Roth conversion", "withdrawal": "Withdrawal"}

# How a rule decides its amount.
AMOUNT_MODES = {
    "fixed": "Fixed amount each year",
    "fill": "Fill up to a target",
    "spread": "Spread the balance evenly over the remaining years of the rule (1/N)",
    "all": "Take the whole balance",
}

# Targets for "fill" rules: what to fill up to.
#   bracket:<rate>   top of an ordinary bracket, e.g. bracket:0.22
#   cg0              top of the 0% capital gains band
#   irmaa:<n>        just under IRMAA tier n (income two years before the premium year)
#   aca:<pct>        ACA MAGI at this % of the poverty line, e.g. aca:4.0
#   magi:<amount>    a MAGI amount in that year's dollars
FILL_TARGET_HELP = "bracket:0.22, cg0, irmaa:1, aca:4.0 or magi:150000"


@dataclass
class When:
    """A point in time: a person's age, or a calendar year."""

    age: int | None = None
    person: int = 0
    year: int | None = None

    def resolve(self, profile: Profile) -> int:
        if self.year is not None:
            return int(self.year)
        if self.age is None:
            raise ValueError("A timing needs an age or a year")
        idx = min(self.person, len(profile.people) - 1)
        return profile.people[idx].birth_year + int(self.age)

    def describe(self, profile: Profile) -> str:
        if self.year is not None:
            return str(self.year)
        idx = min(self.person, len(profile.people) - 1)
        return f"{profile.people[idx].name} age {self.age}"

    @classmethod
    def from_dict(cls, d: dict | int | None) -> "When | None":
        if d is None:
            return None
        if isinstance(d, int):
            return cls(year=d)
        return cls(**d)


@dataclass
class Account:
    id: str
    kind: str
    owner: int | None = 0  # index into profile.people; None for joint taxable/cash
    name: str = ""
    balance: float = 0.0
    return_rate: float | None = None  # None = the plan's default return
    # Taxable brokerage
    cost_basis: float | None = None  # None = equal to the balance
    interest_yield: float = 0.0  # taxed as ordinary income, reinvested
    qualified_dividend_yield: float = 0.0
    nonqualified_dividend_yield: float = 0.0
    cap_gain_distribution_yield: float = 0.0
    # Traditional accounts
    spouse_sole_beneficiary: bool = True  # allows the Joint and Last Survivor table
    # Inherited IRA
    inherited_year: int | None = None  # year the original owner died
    original_owner_birth_year: int | None = None
    owner_started_rmds: bool = True  # the owner died on or after their RMD start
    eligible_designated_beneficiary: bool = False  # stretch over life expectancy, no 10-year limit

    def label(self, profile: Profile) -> str:
        if self.name:
            return self.name
        kind = ACCOUNT_KINDS.get(self.kind, self.kind)
        if self.owner is None or self.owner >= len(profile.people):
            return kind
        return f"{profile.people[self.owner].name} {kind}"

    @property
    def is_traditional(self) -> bool:
        return self.kind in TRADITIONAL_KINDS

    @property
    def is_roth(self) -> bool:
        return self.kind in ROTH_KINDS

    @property
    def deadline_year(self) -> int | None:
        """Last year of the 10-year rule, if it applies."""
        if self.kind != "inherited_ira" or self.inherited_year is None or self.eligible_designated_beneficiary:
            return None
        return self.inherited_year + 10


@dataclass
class IncomeStream:
    """A recurring (or one-time) income: wages, a pension, a home sale gain."""

    kind: str
    amount: float  # yearly amount in the first year it is paid
    owner: int | None = 0
    name: str = ""
    start: When | None = None  # None = from the plan's first year
    end: When | None = None  # None = for life (or to the end of the plan); inclusive
    growth: float = 0.0  # yearly increase after the first year (COLA, raises)
    survivor_pct: float = 0.0  # share that continues to the survivor (pensions)
    federal_taxable_pct: float = 1.0
    state_taxable_pct: float = 1.0  # not used yet: the state taxes what the federal return does

    def label(self, profile: Profile) -> str:
        if self.name:
            return self.name
        kind = INCOME_KINDS.get(self.kind, self.kind)
        if self.owner is None or self.owner >= len(profile.people):
            return kind
        return f"{profile.people[self.owner].name} {kind.lower()}"


@dataclass
class SocialSecurity:
    fra_benefit: float = 0.0  # yearly benefit at full retirement age, today's dollars
    claim_age: float = 67.0  # years, may include months as a fraction (67.5)
    current_benefit: float | None = None  # already claimed: the yearly amount being paid now
    ssdi: bool = False  # receiving disability; becomes a retirement benefit at FRA
    # With SSDI: a claim_age above full retirement age suspends the benefit
    # at FRA and restarts it at claim_age with delayed credits (8% a year).
    # ssdi_end_age ends SSDI early (a return to work); the benefit then
    # starts again at claim_age as an ordinary claim.
    ssdi_end_age: float | None = None


@dataclass
class PersonPlan:
    social_security: SocialSecurity = field(default_factory=SocialSecurity)
    death_age: int | None = None  # None = lives to the end of the plan


@dataclass
class Assumptions:
    inflation: float | None = None  # None = the profile's inflation
    ss_cola: float | None = None  # None = inflation
    return_rate: float = 0.05  # default for accounts with no return of their own
    cash_return: float = 0.03
    spending: float = 0.0  # yearly spending, today's dollars, grows with inflation
    end_age: int = 95  # plan runs until everyone has died or reached this age
    prior_magi: dict[str, float] = field(default_factory=dict)  # {"2025": 180000} for IRMAA


@dataclass
class Rule:
    kind: str  # roth_conversion or withdrawal
    account: str  # source account id
    start: When
    end: When | None = None  # None = a one-year rule
    mode: str = "fixed"
    amount: float = 0.0  # for "fixed"
    target: str = ""  # for "fill"
    to_account: str | None = None  # Roth account for conversions (default: owner's Roth IRA)
    enabled: bool = True
    note: str = ""

    def years(self, profile: Profile) -> tuple[int, int]:
        first = self.start.resolve(profile)
        last = self.end.resolve(profile) if self.end else first
        return first, last

    def describe(self, profile: Profile, accounts: dict[str, Account]) -> str:
        acct = accounts.get(self.account)
        name = acct.label(profile) if acct else self.account
        when = self.start.describe(profile)
        if self.end:
            when += f" to {self.end.describe(profile)}"
        how = {
            "fixed": f"${self.amount:,.0f}",
            "fill": f"fill to {self.target}",
            "spread": "spread evenly (1/N)",
            "all": "whole balance",
        }.get(self.mode, self.mode)
        return f"{RULE_KINDS.get(self.kind, self.kind)} from {name}, {when}, {how}"


@dataclass
class Plan:
    profile: Profile
    people: list[PersonPlan] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    incomes: list[IncomeStream] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    assumptions: Assumptions = field(default_factory=Assumptions)
    schema_version: int = PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        while len(self.people) < len(self.profile.people):
            self.people.append(PersonPlan())

    @property
    def start_year(self) -> int:
        return self.profile.year

    @property
    def inflation(self) -> float:
        a = self.assumptions.inflation
        return self.profile.inflation if a is None else a

    @property
    def ss_cola(self) -> float:
        c = self.assumptions.ss_cola
        return self.inflation if c is None else c

    def death_year(self, i: int) -> int | None:
        age = self.people[i].death_age
        return None if age is None else self.profile.people[i].birth_year + age

    @property
    def end_year(self) -> int:
        last = self.start_year
        for i, p in enumerate(self.profile.people):
            dy = self.death_year(i)
            last = max(last, dy if dy is not None else p.birth_year + self.assumptions.end_age)
        return last

    def account(self, acct_id: str) -> Account | None:
        return next((a for a in self.accounts if a.id == acct_id), None)

    def validate(self) -> list[str]:
        problems = self.profile.validate()
        ids = [a.id for a in self.accounts]
        if len(ids) != len(set(ids)):
            problems.append("Account ids must be unique")
        for a in self.accounts:
            if a.kind not in ACCOUNT_KINDS:
                problems.append(f"Account {a.id}: unknown kind {a.kind!r}")
            if a.kind == "inherited_ira" and a.inherited_year is None:
                problems.append(f"Account {a.id}: an inherited IRA needs the year it was inherited")
        for s in self.incomes:
            if s.kind not in INCOME_KINDS:
                problems.append(f"Income {s.name or s.kind}: unknown kind {s.kind!r}")
        for r in self.rules:
            if r.kind not in RULE_KINDS:
                problems.append(f"Rule: unknown kind {r.kind!r}")
            if r.mode not in AMOUNT_MODES:
                problems.append(f"Rule: unknown amount mode {r.mode!r}")
            if r.account not in ids:
                problems.append(f"Rule: no account with id {r.account!r}")
            if r.mode == "fill" and not r.target:
                problems.append("Rule: a fill rule needs a target such as " + FILL_TARGET_HELP)
        return problems

    # -- persistence --------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        data = dict(data)
        profile = Profile.from_dict(data.pop("profile"))
        people = []
        for p in data.get("people", []):
            p = dict(p)
            ss = SocialSecurity(**p.pop("social_security", {}))
            people.append(PersonPlan(social_security=ss, **p))
        accounts = [Account(**a) for a in data.get("accounts", [])]
        incomes = []
        for s in data.get("incomes", []):
            s = dict(s)
            s["start"] = When.from_dict(s.get("start"))
            s["end"] = When.from_dict(s.get("end"))
            incomes.append(IncomeStream(**s))
        rules = []
        for r in data.get("rules", []):
            r = dict(r)
            r["start"] = When.from_dict(r["start"])
            r["end"] = When.from_dict(r.get("end"))
            rules.append(Rule(**r))
        assumptions = Assumptions(**data.get("assumptions", {}))
        return cls(profile=profile, people=people, accounts=accounts, incomes=incomes,
                   rules=rules, assumptions=assumptions)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_profile(cls, profile: Profile) -> "Plan":
        """Start a plan from a marginal-graph profile.

        Wages, pensions and Social Security become streams that continue;
        IRA withdrawals and one-off household amounts are left out, since
        the plan works those out from accounts and rules.
        """
        people = []
        incomes = []
        for i, p in enumerate(profile.people):
            ss = SocialSecurity()
            if p.social_security:
                age = p.age_in(profile.year)
                ss = SocialSecurity(fra_benefit=p.social_security, claim_age=min(age, 70),
                                    current_benefit=p.social_security)
            people.append(PersonPlan(social_security=ss))
            if p.wages:
                incomes.append(IncomeStream(kind="wages", amount=p.wages, owner=i, growth=profile.inflation))
            if p.pension:
                incomes.append(IncomeStream(kind="pension", amount=p.pension, owner=i))
        h = profile.household
        for kind, amount in (("interest", h.taxable_interest), ("qualified_dividends", h.qualified_dividends),
                             ("tax_exempt", h.tax_exempt_interest), ("other_ordinary", h.other_ordinary_income)):
            if amount:
                incomes.append(IncomeStream(kind=kind, amount=amount, owner=None))
        return cls(profile=profile, people=people, incomes=incomes,
                   assumptions=Assumptions())
