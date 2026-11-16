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
    assert {2025, 2026} <= set(lib.enacted_years)
    assert lib.federal(2026).status == "enacted"


def test_projection_indexes_brackets_but_not_statutory_thresholds():
    lib = TaxLawLibrary(inflation=0.03)
    law26, law27 = lib.federal(2026), lib.federal(2027)
    assert law27.status == "projected" and law27.projected_from == 2026
    assert law27.ordinary["single"].brackets[1][0] == (12_400 * 1.03) // 50 * 50
    assert law27.standard_deduction["mfj"] == (32_200 * 1.03) // 50 * 50
    assert law27.niit_threshold == law26.niit_threshold
    assert law27.ss_base == law26.ss_base
    assert law27.senior_amount == 6_000


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
