import pytest

from rtpl.model.profile import Household, Person, Profile
from rtpl.taxlaw.loader import TaxLawLibrary


@pytest.fixture(scope="session")
def lib():
    return TaxLawLibrary()


@pytest.fixture(scope="session")
def lib_no_inflation():
    """Projected years equal the newest enacted year (easy to check by hand)."""
    return TaxLawLibrary(inflation=0.0)


def make(year=2026, fs="single", ages=(40,), state=None, people=None, **household):
    if people is None:
        people = [Person(name=f"P{i}", birth_year=year - a) for i, a in enumerate(ages)]
    return Profile(year=year, filing_status=fs, state=state, people=people, household=Household(**household))
