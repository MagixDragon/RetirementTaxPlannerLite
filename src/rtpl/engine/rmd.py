"""Required minimum distributions: IRS tables, start ages, inherited IRAs.

Three IRS tables are used, as the rules call for them:

* **Uniform Lifetime** for an owner's own traditional accounts.
* **Joint and Last Survivor** instead, when the owner's spouse is the sole
  beneficiary and is more than 10 years younger.
* **Single Life** for inherited accounts (non-recalculating: the first
  year's factor goes down by one each year after).

The Joint and Last Survivor table is derived from the Single Life table's
mortality (see ``data/rmd/tables.toml``); a user file can supply the
published table instead.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path


def rmd_start_age(birth_year: int) -> int:
    """First RMD age for an owner (SECURE 2.0)."""
    if birth_year <= 1950:
        return 72  # people born before 1951 are already taking RMDs
    if birth_year <= 1959:
        return 73
    return 75


@dataclass(frozen=True)
class RmdTables:
    uniform: dict[int, float]
    single: dict[int, float]
    joint: dict[tuple[int, int], float] = field(default_factory=dict)  # published values, if supplied

    @classmethod
    def load(cls, user_dirs: list[Path] | None = None) -> "RmdTables":
        raw = None
        for d in user_dirs or []:
            p = Path(d) / "rmd" / "tables.toml"
            if p.is_file():
                raw = tomllib.loads(p.read_text(encoding="utf-8"))
                break
        if raw is None:
            shipped = resources.files("rtpl") / "data" / "rmd" / "tables.toml"
            raw = tomllib.loads(shipped.read_text(encoding="utf-8"))
        uniform = {int(a): float(v) for a, v in raw["uniform_lifetime"]}
        single = {int(a): float(v) for a, v in raw["single_life"]}
        joint = {(int(a), int(b)): float(v) for a, b, v in raw.get("joint_life", [])}
        return cls(uniform, single, joint)

    # -- lookups -------------------------------------------------------
    @staticmethod
    def _lookup(table: dict[int, float], age: int) -> float:
        ages = sorted(table)
        if age < ages[0]:
            raise KeyError(f"Age {age} is below the table's first age {ages[0]}")
        return table[min(age, ages[-1])]

    def uniform_period(self, age: int) -> float:
        return self._lookup(self.uniform, age)

    def single_period(self, age: int) -> float:
        return self._lookup(self.single, max(0, age))

    def joint_period(self, owner_age: int, spouse_age: int) -> float:
        if (owner_age, spouse_age) in self.joint:
            return self.joint[(owner_age, spouse_age)]
        return round(_joint_from_single(tuple(sorted(self.single.items())), owner_age, spouse_age), 1)

    def owner_period(self, owner_age: int, spouse_age: int | None, spouse_sole_beneficiary: bool) -> float:
        """Distribution period for an owner's own account."""
        if spouse_sole_beneficiary and spouse_age is not None and owner_age - spouse_age > 10:
            return self.joint_period(owner_age, spouse_age)
        return self.uniform_period(owner_age)


@lru_cache(maxsize=4096)
def _joint_from_single(single_items: tuple[tuple[int, float], ...], x: int, y: int) -> float:
    """Joint and last survivor expectancy from single life expectancies.

    The single table gives e(x) = 0.5 + sum of survival probabilities, so
    one-year survival is p(x) = (e(x) - 0.5) / (e(x+1) + 0.5). The joint
    expectancy is 0.5 + sum over k of (kp_x + kp_y - kp_x * kp_y).
    """
    e = dict(single_items)
    top = max(e)
    p = {a: min(1.0, max(0.0, (e[a] - 0.5) / (e[a + 1] + 0.5))) for a in range(top)}

    def surv(age: int):
        s = 1.0
        while True:
            s *= p.get(age, 0.0)
            age += 1
            yield s

    total = 0.5
    for sx, sy, _ in zip(surv(max(0, x)), surv(max(0, y)), range(150)):
        total += sx + sy - sx * sy
        if sx < 1e-9 and sy < 1e-9:
            break
    return total


@lru_cache(maxsize=1)
def default_tables() -> RmdTables:
    return RmdTables.load()


def inherited_first_period(
    tables: RmdTables,
    beneficiary_birth_year: int,
    inherited_year: int,
    original_owner_birth_year: int | None,
    owner_started_rmds: bool,
) -> float:
    """Single Life factor for the first RMD year (the year after death).

    When the owner had started RMDs, the beneficiary may use the longer of
    their own expectancy and the owner's remaining expectancy.
    """
    first = inherited_year + 1
    period = tables.single_period(first - beneficiary_birth_year)
    if owner_started_rmds and original_owner_birth_year is not None:
        owner_left = tables.single_period(inherited_year - original_owner_birth_year) - 1
        period = max(period, owner_left)
    return period
