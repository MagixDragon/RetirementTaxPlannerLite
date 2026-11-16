"""Social Security benefit rules: own, spousal and survivor benefits.

Amounts are yearly and in plan-start-year dollars. The plan applies COLAs.

Simplifications (listed in docs/design.md):

* Birth month is not known, so a benefit claimed at age A starts in
  January of the year the person reaches A. A claim at A years and M
  months pays (12 - M) months in that year.
* The earnings test before full retirement age is not applied.
* A survivor who is under their own full retirement age gets the
  deceased's benefit without the early-survivor reduction.
"""

from __future__ import annotations


def full_retirement_age(birth_year: int) -> float:
    """Full retirement age in years (for retirement and spousal benefits)."""
    if birth_year <= 1937:
        return 65.0
    if birth_year <= 1942:
        return 65.0 + (birth_year - 1937) * 2 / 12
    if birth_year <= 1954:
        return 66.0
    if birth_year <= 1959:
        return 66.0 + (birth_year - 1954) * 2 / 12
    return 67.0


def own_benefit_factor(birth_year: int, claim_age: float) -> float:
    """Share of the full-retirement-age benefit paid when claiming at ``claim_age``."""
    fra = full_retirement_age(birth_year)
    claim_age = min(max(claim_age, 62.0), 70.0)
    months = round((claim_age - fra) * 12)
    if months < 0:
        early = -months
        return 1.0 - (min(early, 36) * 5 / 900 + max(0, early - 36) * 5 / 1200)
    return 1.0 + months * (2 / 3) / 100  # 8% a year delayed credits (born 1943+)


def spousal_factor(birth_year: int, age_at_start: float) -> float:
    """Share of the spousal amount (half the other's FRA benefit) when it starts at this age."""
    fra = full_retirement_age(birth_year)
    months = round((fra - max(age_at_start, 62.0)) * 12)
    if months <= 0:
        return 1.0  # no delayed credits on spousal benefits
    return 1.0 - (min(months, 36) * 25 / 3600 + max(0, months - 36) * 5 / 1200)


def survivor_amount(deceased_fra_benefit: float, deceased_actual_benefit: float | None) -> float:
    """What the survivor can step up to (before their own benefit is compared).

    If the deceased had claimed, it is their actual benefit, but not less
    than 82.5% of their full benefit (the widow(er)'s limit). If they had
    not claimed, it is their full benefit.
    """
    if deceased_actual_benefit is None:
        return deceased_fra_benefit
    return max(deceased_actual_benefit, 0.825 * deceased_fra_benefit)


def claim_year_fraction(year: int, birth_year: int, claim_age: float) -> float:
    """Share of ``year`` in which a benefit claimed at ``claim_age`` is paid."""
    whole = int(claim_age)
    months = round((claim_age - whole) * 12)
    start = birth_year + whole
    if year < start:
        return 0.0
    if year == start:
        return (12 - months) / 12
    return 1.0
