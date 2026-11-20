from rtpl.model.profile import Household, Person, Profile
from rtpl.report import ReportChart, profile_sections, write_report


def test_profile_sections_list_people_and_nonzero_household_income():
    profile = Profile(filing_status="mfj", state="CA", people=[Person(name="Ann", social_security=30000), Person(name="Bo")],
                      household=Household(qualified_dividends=5000))
    sections = profile_sections(profile, state_name="California")
    assert [s.heading for s in sections] == ["Profile", "Ann", "Bo", "Household"]
    assert ("Filing status", "Married filing jointly") in sections[0].rows
    assert ("State", "California") in sections[0].rows
    assert ("Social Security", "$30,000") in sections[1].rows
    assert sections[3].rows == [("Qualified dividends", "$5,000"), ("Deduction", "Standard")]


def test_long_profile_spills_onto_more_pages(tmp_path):
    profile = Profile(people=[Person(name=f"Person {i}") for i in range(8)])
    out = tmp_path / "r.pdf"
    chart = ReportChart("Blank", lambda fig: fig.add_subplot(111))
    assert write_report(out, "Test", profile_sections(profile), [chart]) >= 3
