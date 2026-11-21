import json
import shutil
from importlib import resources
from pathlib import Path

import pytest

from rtpl.model.profile import Household, Person, Profile
from rtpl.taxlaw.loader import Schedule, TaxLawLibrary


def test_schedule_math():
    s = Schedule.from_pairs([[0, 0.1], [10_000, 0.2]])
    assert s.tax(15_000) == pytest.approx(2_000)
    assert s.tax_between(5_000, 12_000) == pytest.approx(500 + 400)
    assert s.rate_at(10_000) == 0.2


def test_enacted_years_present(lib):
    assert set(range(2020, 2028)) <= set(lib.enacted_years)
    assert lib.federal(2026).status == "enacted"


def test_projection_indexes_brackets_but_not_statutory_thresholds():
    lib = TaxLawLibrary(inflation=0.03)
    law27, law28 = lib.federal(2027), lib.federal(2028)
    assert law28.status == "projected" and law28.projected_from == 2027
    assert law28.ordinary["single"].brackets[1][0] == (12_800 * 1.03) // 50 * 50
    assert law28.standard_deduction["mfj"] == (33_200 * 1.03) // 50 * 50
    assert law28.niit_threshold == law27.niit_threshold
    assert law28.ss_base == law27.ss_base
    assert law28.senior_amount == 6_000


def test_years_before_data_are_rejected(lib):
    with pytest.raises(KeyError):
        lib.federal(2010)


def test_user_folder_overrides_shipped_file(tmp_path):
    shipped = Path(str(resources.files("rtpl") / "data" / "federal" / "2026.toml"))
    (tmp_path / "federal").mkdir()
    text = shipped.read_text().replace("single = 16100", "single = 99999")
    (tmp_path / "federal" / "2026.toml").write_text(text)
    lib = TaxLawLibrary(user_dirs=[tmp_path])
    assert lib.federal(2026).standard_deduction["single"] == 99_999


def test_states(lib):
    assert {"CA", "IL", "TX", "FL"} <= set(lib.available_states())
    assert lib.state(None) is None
    with pytest.raises(KeyError):
        lib.state("ZZ")


def test_profile_round_trip(tmp_path):
    p = Profile(
        year=2026,
        filing_status="mfj",
        state="CA",
        people=[Person(name="A", birth_year=1958, social_security=30_000, medicare_part_b=True), Person(name="B")],
        household=Household(qualified_dividends=1_000, has_taxable_brokerage=True),
    )
    path = tmp_path / "p.json"
    p.save(path)
    assert Profile.load(path) == p
    assert json.loads(path.read_text())["schema_version"] == 1


def test_profile_validation():
    assert Profile(filing_status="mfj", people=[Person()]).validate()
    assert not Profile(filing_status="single", people=[Person()]).validate()


# Spot checks against the IRS revenue procedures and CMS premium releases.
@pytest.mark.parametrize(
    "year, single_12, mfj_top, std_mfj, add_married, cg_mfj_0, part_b, irmaa_single_1",
    [
        (2020, 9_875, 622_050, 24_800, 1_300, 80_000, 144.60, 87_000),
        (2021, 9_950, 628_300, 25_100, 1_350, 80_800, 148.50, 88_000),
        (2022, 10_275, 647_850, 25_900, 1_400, 83_350, 170.10, 91_000),
        (2023, 11_000, 693_750, 27_700, 1_500, 89_250, 164.90, 97_000),
        (2024, 11_600, 731_200, 29_200, 1_550, 94_050, 174.70, 103_000),
        (2027, 12_800, 793_650, 33_200, 1_700, 102_100, 209.40, 112_000),
    ],
)
def test_added_years_match_published_figures(
    lib, year, single_12, mfj_top, std_mfj, add_married, cg_mfj_0, part_b, irmaa_single_1
):
    law = lib.federal(year)
    assert law.status == "enacted" and law.projected_from is None
    assert law.ordinary["single"].brackets[1] == (single_12, 0.12)
    assert law.ordinary["mfj"].brackets[-1] == (mfj_top, 0.37)
    assert law.standard_deduction["mfj"] == std_mfj
    assert law.additional_std_married == add_married
    assert law.capital_gains["mfj"].brackets[1][0] == cg_mfj_0
    assert law.irmaa_part_b_standard == pytest.approx(part_b)
    assert law.irmaa_tiers[0].single == irmaa_single_1
    assert len(law.irmaa_tiers) == 5


def test_every_year_file_is_well_formed(lib):
    for year in lib.enacted_years:
        law = lib.federal(year)
        assert law.year == year and law.irmaa_premium_year == year
        for fs in ("single", "mfj", "mfs", "hoh"):
            rates = [r for _, r in law.ordinary[fs].brackets]
            assert rates == [0.10, 0.12, 0.22, 0.24, 0.32, 0.35, 0.37]
            bounds = [b for b, _ in law.ordinary[fs].brackets]
            assert bounds == sorted(bounds)
        thresholds = [t.single for t in law.irmaa_tiers]
        assert thresholds == sorted(thresholds)


def test_senior_deduction_and_aca_by_year(lib):
    for year in range(2020, 2025):
        assert not lib.federal(year).senior_deduction_active
    for year in (2025, 2026, 2027):
        assert lib.federal(year).senior_deduction_active
    enhanced = {y: lib.federal(y).aca_enhanced_subsidies for y in range(2020, 2028)}
    assert enhanced == {2020: False, 2021: True, 2022: True, 2023: True, 2024: True,
                        2025: True, 2026: False, 2027: False}


def test_2020_tax_for_a_retired_couple(lib):
    """A 2020 joint return worked by hand from the 2020 tables."""
    from rtpl.engine.tax import compute_tax

    p = Profile(
        year=2020,
        filing_status="mfj",
        people=[Person(birth_year=1952, social_security=30_000, ira_distributions=40_000),
                Person(birth_year=1954, pension=20_000)],
    )
    r = compute_tax(p, lib.federal(2020))
    # Provisional income 60,000 + 15,000 = 75,000: 85% tier.
    # Taxable SS = min(0.85*(75,000-44,000) + min(6,000, 15,000), 25,500) = 25,500.
    assert r.taxable_social_security == pytest.approx(25_500)
    # Deduction: 24,800 + 2 x 1,300 (both 65+), no senior deduction in 2020.
    assert r.senior_deduction == 0
    assert r.deduction == pytest.approx(27_400)
    taxable = 60_000 + 25_500 - 27_400
    assert r.taxable_income == pytest.approx(taxable)
    assert r.ordinary_tax == pytest.approx(1_975 + 0.12 * (taxable - 19_750))


def test_qcd_limits(lib):
    limits = {y: lib.federal(y).qcd_limit for y in range(2020, 2028)}
    assert limits == {2020: 100_000, 2021: 100_000, 2022: 100_000, 2023: 100_000,
                      2024: 105_000, 2025: 108_000, 2026: 111_000, 2027: 114_000}
    split = {y: lib.federal(y).qcd_split_interest_limit for y in (2022, 2023, 2025, 2026, 2027)}
    assert split == {2022: 0, 2023: 50_000, 2025: 54_000, 2026: 55_000, 2027: 57_000}
