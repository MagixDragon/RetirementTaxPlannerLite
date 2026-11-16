import pytest

from rtpl.engine.tax import IncomeSource, compute_tax, senior_deduction, taxable_social_security
from rtpl.model.profile import Person

from conftest import make


def test_ordinary_brackets_single_2026(lib):
    p = make(people=[Person(birth_year=1986, wages=60_000)])
    r = compute_tax(p, lib.federal(2026))
    assert r.taxable_income == 60_000 - 16_100
    assert r.ordinary_tax == pytest.approx(1_240 + (43_900 - 12_400) * 0.12)


def test_additional_standard_deduction_65_plus(lib):
    law = lib.federal(2026)
    single = make(ages=(70,), other_ordinary_income=50_000)
    assert compute_tax(single, law).deduction == 16_100 + 2_050
    couple = make(fs="mfj", ages=(66, 67), other_ordinary_income=80_000)
    assert compute_tax(couple, law).deduction == 32_200 + 2 * 1_650


@pytest.mark.parametrize(
    "benefits, provisional, base, adjusted, expected",
    [
        (30_000, 20_000, 25_000, 34_000, 0.0),
        (30_000, 30_000, 25_000, 34_000, 2_500.0),  # 50% tier
        (30_000, 35_000, 25_000, 34_000, 850 + 4_500),  # into 85% tier
        (40_000, 50_000, 32_000, 44_000, 5_100 + 6_000),
        (20_000, 500_000, 32_000, 44_000, 17_000),  # capped at 85%
        (10_000, 5_000, 0, 0, 4_250),  # MFS living together
    ],
)
def test_social_security_worksheet(benefits, provisional, base, adjusted, expected):
    assert taxable_social_security(benefits, provisional, base, adjusted) == pytest.approx(expected)


def test_senior_deduction_phaseout(lib):
    law = lib.federal(2026)
    assert senior_deduction(make(ages=(70,)), law, 75_000) == 6_000
    assert senior_deduction(make(ages=(70,)), law, 100_000) == pytest.approx(4_500)
    assert senior_deduction(make(ages=(70,)), law, 175_000) == 0
    couple = make(fs="mfj", ages=(66, 70))
    assert senior_deduction(couple, law, 200_000) == pytest.approx(6_000)  # 3,000 each
    assert senior_deduction(couple, law, 250_000) == 0
    one_senior = make(fs="mfj", ages=(66, 60))
    assert senior_deduction(one_senior, law, 150_000) == 6_000
    assert senior_deduction(make(fs="mfs", ages=(70, 70)), law, 50_000) == 0
    assert senior_deduction(make(ages=(64,)), law, 50_000) == 0


def test_senior_deduction_sunsets_after_2028(lib):
    assert lib.federal(2028).senior_deduction_active
    assert not lib.federal(2029).senior_deduction_active
    assert senior_deduction(make(year=2029, ages=(70,)), lib.federal(2029), 50_000) == 0


def test_capital_gains_stack_on_ordinary(lib):
    law = lib.federal(2026)
    only_gains = make(long_term_capital_gains=60_000)
    r = compute_tax(only_gains, law)
    assert r.ordinary_tax == 0 and r.capital_gains_tax == 0  # all inside 0% band
    big = make(long_term_capital_gains=100_000)
    assert compute_tax(big, law).capital_gains_tax == pytest.approx(0.15 * (83_900 - 49_450))
    mixed = make(people=[Person(birth_year=1986, wages=50_000)], long_term_capital_gains=20_000)
    r = compute_tax(mixed, law)
    assert r.preferential_income == 20_000
    assert r.capital_gains_tax == pytest.approx(0.15 * (53_900 - 49_450))


def test_capital_loss_limited(lib):
    p = make(people=[Person(birth_year=1986, wages=50_000)], long_term_capital_gains=-10_000)
    r = compute_tax(p, lib.federal(2026))
    assert r.agi == 47_000
    assert r.preferential_income == 0


def test_niit(lib):
    law = lib.federal(2026)
    high = make(people=[Person(birth_year=1986, wages=250_000)], qualified_dividends=20_000)
    assert compute_tax(high, law).niit == pytest.approx(0.038 * 20_000)
    edge = make(people=[Person(birth_year=1986, wages=190_000)], qualified_dividends=20_000)
    assert compute_tax(edge, law).niit == pytest.approx(0.038 * 10_000)
    ira_only = make(people=[Person(birth_year=1950, ira_distributions=400_000)])
    assert compute_tax(ira_only, law).niit == 0  # IRA money is not investment income


def test_extra_income_sources(lib):
    law = lib.federal(2026)
    base = make(people=[Person(birth_year=1986, wages=50_000)])
    assert compute_tax(base, law, extra=10_000).agi == 60_000
    r = compute_tax(base, law, extra=10_000, source=IncomeSource.QUALIFIED_DIVIDENDS)
    assert r.qualified_dividends == 10_000 and r.preferential_income == 10_000
    r = compute_tax(base, law, extra=10_000, source=IncomeSource.LTCG)
    assert r.net_ltcg == 10_000


def test_magi_definitions(lib):
    p = make(
        people=[Person(birth_year=1960, social_security=30_000, ira_distributions=20_000)],
        tax_exempt_interest=1_000,
    )
    r = compute_tax(p, lib.federal(2026))
    assert r.magi_irmaa == pytest.approx(r.agi + 1_000)
    assert r.magi_aca == pytest.approx(r.agi + 1_000 + 30_000 - r.taxable_social_security)


def test_state_california_excludes_social_security(lib):
    p = make(
        state="CA",
        people=[Person(birth_year=1955, social_security=40_000, ira_distributions=60_000)],
    )
    law, ca = lib.federal(2026), lib.state("CA")
    r = compute_tax(p, law, ca)
    expected_base = 60_000 - 5_540
    assert r.state_tax == pytest.approx(ca.brackets["single"].tax(expected_base) - 153)
    assert ca.label == "State Tax (CA)"


def test_state_illinois_exempts_retirement_income(lib):
    p = make(state="IL", people=[Person(birth_year=1955, ira_distributions=50_000)], taxable_interest=10_000)
    r = compute_tax(p, lib.federal(2026), lib.state("IL"))
    assert r.state_tax == pytest.approx((10_000 - 2_925) * 0.0495)


def test_no_income_tax_state(lib):
    tx = lib.state("TX")
    assert not tx.has_income_tax
    p = make(state="TX", people=[Person(birth_year=1980, wages=100_000)])
    assert compute_tax(p, lib.federal(2026), tx).state_tax == 0


def test_north_carolina_flat_tax_exempts_social_security(lib):
    p = make(state="NC", people=[Person(birth_year=1958, social_security=30_000, ira_distributions=60_000)])
    r = compute_tax(p, lib.federal(2026), lib.state("NC"))
    assert r.state_tax == pytest.approx((60_000 - 12_750) * 0.0399)


def test_washington_taxes_only_large_long_term_gains(lib):
    wa = lib.state("WA")
    law = lib.federal(2026)
    p = make(state="WA", people=[Person(birth_year=1958, ira_distributions=500_000)], qualified_dividends=50_000)
    assert compute_tax(p, law, wa).state_tax == 0  # wages, IRA and dividends untaxed
    gains = make(state="WA", long_term_capital_gains=378_000)
    assert compute_tax(gains, law, wa).state_tax == pytest.approx(0.07 * 100_000)
    big = make(state="WA", long_term_capital_gains=1_478_000)
    assert compute_tax(big, law, wa).state_tax == pytest.approx(0.07 * 1_000_000 + 0.099 * 200_000)


def test_retirement_exclusion_per_person_and_age(lib):
    ny = lib.state("NY")
    law = lib.federal(2026)
    young = make(state="NY", people=[Person(birth_year=1970, ira_distributions=30_000)])
    old = make(state="NY", people=[Person(birth_year=1960, ira_distributions=30_000)])
    assert compute_tax(old, law, ny).state_tax < compute_tax(young, law, ny).state_tax
    assert compute_tax(old, law, ny).state_tax == pytest.approx(ny.brackets["single"].tax(30_000 - 20_000 - 8_000))


def test_state_social_security_agi_cliff(lib):
    nm = lib.state("NM")
    law = lib.federal(2026)
    low = make(state="NM", people=[Person(birth_year=1958, social_security=40_000, ira_distributions=50_000)])
    r = compute_tax(low, law, nm)
    assert r.state_tax == pytest.approx(nm.brackets["single"].tax(max(0, r.agi - r.taxable_social_security - 16_100)))
    high = make(state="NM", people=[Person(birth_year=1958, social_security=40_000, ira_distributions=120_000)])
    r = compute_tax(high, law, nm)
    assert r.state_tax == pytest.approx(nm.brackets["single"].tax(r.agi - 16_100))


def test_capital_gains_exclusion_and_cap(lib):
    law = lib.federal(2026)
    mo = make(state="MO", long_term_capital_gains=100_000)
    assert compute_tax(mo, law, lib.state("MO")).state_tax == 0
    hi = lib.state("HI")
    p = make(state="HI", long_term_capital_gains=500_000)
    r = compute_tax(p, law, hi)
    base = 500_000 - 4_400 - 1_144
    assert r.state_tax == pytest.approx(min(hi.brackets["single"].tax(base), 0.0725 * base))


def test_every_state_loads_and_computes(lib):
    law = lib.federal(2026)
    codes = lib.available_states()
    assert len(codes) == 51  # 50 states + DC
    for code in codes:
        st = lib.state(code)
        p = make(state=code, people=[Person(birth_year=1958, social_security=30_000, ira_distributions=80_000)],
                 qualified_dividends=10_000, long_term_capital_gains=10_000)
        assert compute_tax(p, law, st).state_tax >= 0
