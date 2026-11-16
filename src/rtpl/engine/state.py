"""State income tax for one household and year.

A simplified, data-driven model that covers what matters most for retirees:
brackets, standard deduction, personal exemptions (as deductions or
credits), Social Security exemptions, pension/IRA exclusions, an age-based
deduction, capital gains exclusions or rate caps, and capital-gains-only
taxes such as Washington's. Local income taxes and most credits are not
modelled; each state file's ``notes`` say what is left out.
"""

from __future__ import annotations


from rtpl.model.profile import Profile
from rtpl.taxlaw.loader import StateLaw, status_key


def _exempt_social_security(profile: Profile, state: StateLaw, agi: float, taxable_ss: float) -> float:
    """Part of federally taxable Social Security the state does not tax."""
    if taxable_ss <= 0:
        return 0.0
    if not state.taxes_social_security:
        return taxable_ss
    rule = state.social_security
    fs = status_key(profile.filing_status)
    exempt = 0.0
    if rule.exempt_from_age is not None:
        total = profile.total("social_security")
        if total > 0:
            share = sum(
                p.social_security for p in profile.people if p.age_in(profile.year) >= rule.exempt_from_age
            ) / total
            exempt = max(exempt, share * taxable_ss)
    if rule.exempt_if_agi_at_most is not None:
        limit = rule.exempt_if_agi_at_most[fs]
        if agi <= limit:
            fraction = 1.0
        elif rule.phaseout_width is not None and rule.phaseout_width[fs] > 0:
            fraction = max(rule.exempt_fraction_above, 1.0 - (agi - limit) / rule.phaseout_width[fs])
        else:
            fraction = rule.exempt_fraction_above
        exempt = max(exempt, fraction * taxable_ss)
    return exempt


def _retirement_exclusion(profile: Profile, state: StateLaw, extra_ira: float) -> float:
    """Pension/IRA income excluded, person by person.

    Extra ordinary income on the marginal graph counts as Person 1's IRA
    withdrawal.
    """
    total = 0.0
    for i, person in enumerate(profile.people):
        remaining = {
            "pension": person.pension,
            "ira": person.ira_distributions + (extra_ira if i == 0 else 0.0),
        }
        age = person.age_in(profile.year)
        for ex in state.retirement_exclusions:
            if age < ex.min_age:
                continue
            eligible = sum(remaining[k] for k in ex.applies_to if k in remaining)
            used = min(eligible, ex.amount)
            total += used
            # Take the exclusion from the eligible buckets in order.
            for k in ex.applies_to:
                if k in remaining and used > 0:
                    take = min(remaining[k], used)
                    remaining[k] -= take
                    used -= take
    return total


def state_income_tax(
    profile: Profile,
    state: StateLaw,
    *,
    agi: float,
    taxable_ss: float,
    net_ltcg: float,
    extra_ira: float = 0.0,
) -> float:
    fs = status_key(profile.filing_status)
    tax = 0.0
    if state.has_income_tax:
        base = agi
        base -= _exempt_social_security(profile, state, agi, taxable_ss)
        base -= _retirement_exclusion(profile, state, extra_ira)
        cg_excluded = state.capital_gains_exclusion * max(0.0, net_ltcg)
        base -= cg_excluded
        if state.age_deduction is not None:
            amount, min_age = state.age_deduction
            base -= amount * sum(1 for a in profile.ages() if a >= min_age)
        base -= state.standard_deduction[fs]
        base -= state.exemption_per_person * len(profile.people)
        base = max(0.0, base)
        sched = state.brackets[fs]
        tax = sched.tax(base)
        if state.capital_gains_max_rate is not None:
            gains = min(base, max(0.0, net_ltcg) - cg_excluded)
            capped = sched.tax(base - gains) + float(state.capital_gains_max_rate) * gains
            tax = min(tax, capped)
        for rate, threshold in state.surcharges:
            tax += rate * max(0.0, base - threshold)
        credits = state.credit_per_person * len(profile.people)
        if state.filer_credit is not None:
            credits += state.filer_credit[fs]
        tax = max(0.0, tax - credits)
    if state.gains_only_tax is not None:
        taxable_gains = max(0.0, net_ltcg - state.gains_only_deduction)
        tax += state.gains_only_tax.tax(taxable_gains)
    return tax


def has_state_tax(state: StateLaw | None) -> bool:
    return state is not None and (state.has_income_tax or state.gains_only_tax is not None)


__all__ = ["state_income_tax", "has_state_tax"]
