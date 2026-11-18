"""Forward-looking plan: RMD tables, Social Security, rules and the year loop."""

from pathlib import Path

import pytest

from rtpl.engine import social_security as ssa
from rtpl.engine.forward import PlanEngine, default_line_visibility, run_plan, year_table
from rtpl.engine.rmd import default_tables, rmd_start_age
from rtpl.model.plan import Account, Assumptions, IncomeStream, Plan, PersonPlan, Rule, SocialSecurity, When
from rtpl.model.profile import Household, Person, Profile

EXAMPLE = Path("examples/plans/couple_conversions_inherited.json")


def couple(year=2026, births=(1956, 1958), **household):
    return Profile(
        year=year, filing_status="mfj",
        people=[Person(name="A", birth_year=births[0]), Person(name="B", birth_year=births[1])],
        household=Household(**household),
    )


def lives_to(prof, age):
    for p in prof.people:
        p.life_expectancy = age


def single(year=2026, birth=1951):
    return Profile(year=year, filing_status="single", people=[Person(name="A", birth_year=birth)])


# -- tables -----------------------------------------------------------------------


def test_rmd_tables():
    t = default_tables()
    assert t.uniform_period(73) == 26.5
    assert t.uniform_period(130) == 2.0  # 120 and older
    assert t.single_period(60) == 27.1
    # The derived joint table reproduces the Uniform Lifetime table (10-year gap) to 0.1 year.
    for age in range(72, 100):
        assert abs(t.joint_period(age, age - 10) - t.uniform_period(age)) <= 0.11
    # A much younger spouse gives a longer period than the uniform table.
    assert t.owner_period(75, 55, True) > t.uniform_period(75) + 5
    assert t.owner_period(75, 55, False) == t.uniform_period(75)
    assert t.owner_period(75, 70, True) == t.uniform_period(75)


def test_rmd_start_age():
    assert rmd_start_age(1950) == 72
    assert rmd_start_age(1951) == 73
    assert rmd_start_age(1959) == 73
    assert rmd_start_age(1960) == 75


def test_social_security_factors():
    assert ssa.full_retirement_age(1960) == 67
    assert ssa.full_retirement_age(1957) == pytest.approx(66.5)
    assert ssa.own_benefit_factor(1960, 62) == pytest.approx(0.70)
    assert ssa.own_benefit_factor(1960, 70) == pytest.approx(1.24)
    assert ssa.own_benefit_factor(1960, 67) == pytest.approx(1.0)
    assert ssa.spousal_factor(1960, 62) == pytest.approx(0.65)
    assert ssa.spousal_factor(1960, 68) == 1.0
    assert ssa.survivor_amount(30000, 21000) == pytest.approx(24750)  # 82.5% floor
    assert ssa.survivor_amount(30000, None) == 30000


# -- engine -------------------------------------------------------------------------


def test_rmd_uniform_first_year(lib):
    plan = Plan(profile=single(), accounts=[Account(id="ira", kind="traditional_ira", balance=1_000_000)])
    res = run_plan(plan, lib, with_lines=False)
    y0 = res.years[0]
    assert y0.ages[0] == 75
    rmd = y0.category_totals()["rmd"]
    assert rmd == pytest.approx(1_000_000 / 24.6)
    # The balance after the RMD grows at the default return.
    assert y0.balances_end["ira"] == pytest.approx((1_000_000 - rmd) * 1.05)


def test_rmd_joint_table_for_younger_spouse(lib):
    prof = couple(births=(1951, 1970))
    plan = Plan(profile=prof, accounts=[Account(id="ira", kind="traditional_ira", owner=0, balance=1_000_000)])
    rmd = run_plan(plan, lib, with_lines=False).years[0].category_totals()["rmd"]
    assert rmd == pytest.approx(1_000_000 / default_tables().joint_period(75, 56))
    assert rmd < 1_000_000 / 24.6


def test_inherited_ira_ten_year_rule_and_spread(lib):
    prof = single(birth=1970)
    lives_to(prof, 70)
    acct = Account(id="inh", kind="inherited_ira", owner=0, balance=500_000, inherited_year=2024,
                   original_owner_birth_year=1940, owner_started_rmds=True)
    plan = Plan(profile=prof, accounts=[acct], assumptions=Assumptions(return_rate=0.0))
    res = run_plan(plan, lib, with_lines=False)
    by_year = {y.year: y for y in res.years}
    # RMD years 2025+: Single Life at the beneficiary's age in 2025 (55 -> 31.6), less one per year.
    assert by_year[2026].category_totals()["inherited"] == pytest.approx(500_000 / 30.6)
    assert by_year[2034].balances_end["inh"] == pytest.approx(0.0)
    assert any("deadline year" in n for n in by_year[2034].notes)

    # 1/N over the final 4 years: equal withdrawals when returns are zero.
    plan.rules = [Rule(kind="withdrawal", account="inh", start=When(year=2031), end=When(year=2034), mode="spread")]
    res = run_plan(plan, lib, with_lines=False)
    amounts = [y.category_totals()["inherited"] for y in res.years if 2031 <= y.year <= 2034]
    assert max(amounts) - min(amounts) < 1.0


def test_one_year_rule_overrides_range(lib):
    prof = single(birth=1964)
    lives_to(prof, 68)
    plan = Plan(
        profile=prof,
        accounts=[Account(id="ira", kind="traditional_ira", balance=800_000)],
        rules=[
            Rule(kind="roth_conversion", account="ira", start=When(age=62), end=When(age=66), mode="fixed", amount=50_000),
            Rule(kind="roth_conversion", account="ira", start=When(year=2028), mode="fixed", amount=0),
        ],
        assumptions=Assumptions(),
    )
    conv = {y.year: y.category_totals()["roth_conversion"] for y in run_plan(plan, lib, with_lines=False).years}
    assert conv[2026] == 50_000 and conv[2027] == 50_000
    assert conv[2028] == 0
    assert conv[2029] == 50_000 and conv[2030] == 50_000
    assert conv[2031] == 0  # after the range


def test_fill_to_bracket_top_and_line_matches(lib):
    prof = couple(births=(1962, 1963))
    lives_to(prof, 67)
    plan = Plan(
        profile=prof,
        accounts=[Account(id="ira", kind="traditional_ira", owner=0, balance=2_000_000)],
        incomes=[IncomeStream(kind="pension", amount=30_000, owner=0)],
        rules=[Rule(kind="roth_conversion", account="ira", start=When(year=2026), end=When(year=2028),
                    mode="fill", target="bracket:0.22")],
        assumptions=Assumptions(),
    )
    res = run_plan(plan, lib)
    for y in res.years[:3]:
        top = [b for b, r in y.law.ordinary["mfj"].brackets if r == 0.24][0]
        ordinary_taxable = y.tax.taxable_income - y.tax.preferential_income
        assert ordinary_taxable == pytest.approx(top, abs=2.0)
        # The 22% line sits at the top of the bar.
        assert y.lines["bracket:0.22"] == pytest.approx(y.taxable_bar, abs=10.0)
    assert res.years[3].category_totals()["roth_conversion"] == 0
    # Conversions land in an automatically created Roth IRA.
    assert any(k.startswith("_roth") for k in res.years[0].balances_end)


def test_social_security_spousal_and_survivor(lib):
    prof = couple(births=(1960, 1960))
    lives_to(prof, 82)
    prof.people[0].life_expectancy = 80
    plan = Plan(
        profile=prof,
        people=[
            PersonPlan(social_security=SocialSecurity(fra_benefit=40_000, claim_age=67)),
            PersonPlan(social_security=SocialSecurity(fra_benefit=0, claim_age=67)),
        ],
        incomes=[IncomeStream(kind="pension", amount=20_000, owner=0, survivor_pct=0.5)],
        assumptions=Assumptions(ss_cola=0.0),
    )
    by_year = {y.year: y for y in run_plan(plan, lib, with_lines=False).years}
    y67 = by_year[2027]
    assert y67.category_totals()["ss"] == pytest.approx(40_000 + 20_000)  # own + 50% spousal
    assert by_year[2040].filing_status == "mfj"  # year of death: still joint
    after = by_year[2041]
    assert after.filing_status == "single"
    assert after.category_totals()["ss"] == pytest.approx(40_000)  # survivor steps up
    assert after.category_totals()["pension"] == pytest.approx(10_000)


def test_spousal_reduced_when_early(lib):
    prof = couple(births=(1964, 1964))
    lives_to(prof, 63)
    plan = Plan(
        profile=prof,
        people=[
            PersonPlan(social_security=SocialSecurity(fra_benefit=40_000, claim_age=62)),
            PersonPlan(social_security=SocialSecurity(fra_benefit=10_000, claim_age=62)),
        ],
        assumptions=Assumptions(ss_cola=0.0),
    )
    y62 = run_plan(plan, lib, with_lines=False).years[0]
    assert y62.ages[0] == 62
    own = 40_000 * 0.7 + 10_000 * 0.7
    spousal = (20_000 - 10_000) * 0.65
    assert y62.category_totals()["ss"] == pytest.approx(own + spousal)


def test_deficit_sells_brokerage_and_realizes_gains(lib):
    prof = single(birth=1960)
    lives_to(prof, 68)
    plan = Plan(
        profile=prof,
        accounts=[Account(id="brk", kind="taxable", owner=None, balance=500_000, cost_basis=250_000)],
        assumptions=Assumptions(spending=60_000, return_rate=0.0),
    )
    y0 = run_plan(plan, lib, with_lines=False).years[0]
    ct = y0.category_totals()
    assert ct["principal"] + ct["realized_gains"] == pytest.approx(60_000 + y0.tax.total_tax, abs=2)
    assert ct["realized_gains"] == pytest.approx(ct["principal"], rel=1e-6)
    assert ct["dividends"] == 0  # no yields set on this account  # half the balance is gain


def test_example_plan_runs_and_round_trips(lib, tmp_path):
    plan = Plan.load(EXAMPLE)
    res = run_plan(plan, lib)
    assert res.years[0].year == 2026 and res.years[-1].year == 2060
    assert all(y.shortfall == 0 for y in res.years)
    vis = default_line_visibility(res)
    assert vis["bracket:0.22"] and vis["cg0"] and not vis["aca:4"]
    header, rows = year_table(res)
    assert len(rows) == len(res.years) and header[0] == "Year"
    out = tmp_path / "plan.json"
    plan.save(out)
    again = Plan.load(out)
    assert again.to_dict() == plan.to_dict()


def test_plan_validation():
    plan = Plan(profile=single(), accounts=[Account(id="x", kind="bogus")],
                rules=[Rule(kind="roth_conversion", account="nope", start=When(year=2026), mode="fill")])
    problems = plan.validate()
    assert any("unknown kind" in p for p in problems)
    assert any("no account" in p for p in problems)
    assert any("target" in p for p in problems)
    with pytest.raises(ValueError):
        PlanEngine(plan, None)


def test_plan_cli(tmp_path):
    from rtpl.plan_cli import main

    out, bal, csv_path = tmp_path / "i.png", tmp_path / "b.svg", tmp_path / "y.csv"
    assert main([str(EXAMPLE), "-o", str(out), "--balances", str(bal), "--csv", str(csv_path),
                 "--detail", "--todays-dollars", "--line", "bracket:0.32"]) == 0
    assert out.stat().st_size > 10_000 and bal.stat().st_size > 10_000
    assert csv_path.read_text(encoding="utf-8").startswith("Year,")


def test_from_profile_carries_income():
    prof = couple()
    prof.people[0].social_security = 30_000
    prof.people[1].pension = 12_000
    plan = Plan.from_profile(prof)
    assert plan.people[0].social_security.current_benefit == 30_000
    assert [s.kind for s in plan.incomes] == ["pension"]


def test_ssdi_suspend_at_fra_until_70(lib):
    prof = single(birth=1966)
    lives_to(prof, 72)
    plan = Plan(profile=prof, people=[PersonPlan(social_security=SocialSecurity(
        fra_benefit=30_000, claim_age=70, ssdi=True))], assumptions=Assumptions(ss_cola=0.0))
    ss = {y.ages[0]: y.category_totals()["ss"] for y in run_plan(plan, lib, with_lines=False).years}
    assert ss[60] == pytest.approx(30_000) and ss[66] == pytest.approx(30_000)  # SSDI at the full benefit
    assert ss[67] == 0 and ss[69] == 0  # suspended from full retirement age
    assert ss[70] == pytest.approx(30_000 * 1.24)  # restarts with 36 months of delayed credits


def test_ssdi_default_continues_and_early_end(lib):
    prof = single(birth=1966)
    lives_to(prof, 69)
    plan = Plan(profile=prof, people=[PersonPlan(social_security=SocialSecurity(
        fra_benefit=30_000, claim_age=67, ssdi=True))], assumptions=Assumptions(ss_cola=0.0))
    ss = {y.ages[0]: y.category_totals()["ss"] for y in run_plan(plan, lib, with_lines=False).years}
    assert ss[66] == ss[67] == ss[68] == pytest.approx(30_000)
    plan.people[0].social_security = SocialSecurity(fra_benefit=30_000, claim_age=62, ssdi=True, ssdi_end_age=61)
    ss = {y.ages[0]: y.category_totals()["ss"] for y in run_plan(plan, lib, with_lines=False).years}
    assert ss[60] == pytest.approx(30_000) and ss[61] == 0
    assert ss[62] == pytest.approx(21_000)  # ordinary early claim afterwards


def test_threshold_lines_sit_at_the_threshold_when_income_cannot_get_under_it(lib):
    # Dividends alone are over the 0% gains band and the IRMAA tiers. The
    # lines still run every year, at the threshold (inside the bar), each on
    # its own value, not all stacked on the dividends.
    prof = single(birth=1951)
    lives_to(prof, 76)
    plan = Plan(profile=prof, incomes=[IncomeStream(kind="qualified_dividends", amount=400_000, owner=0)],
                assumptions=Assumptions())
    res = run_plan(plan, lib)
    y = res.years[0]
    std = y.tax.agi - y.tax.taxable_income
    cg_top = y.law.capital_gains["single"].thresholds()[0]
    assert y.lines["cg0"] == pytest.approx(cg_top + std, abs=10.0)
    tiers = [y.lines[f"irmaa:{n}"] for n in (1, 2, 3)]
    assert tiers[0] < tiers[1] < tiers[2] < y.total_income
    assert all(v is not None for yr in res.years for v in yr.lines.values())
    # The chart labels every shown line at the right.
    from matplotlib.figure import Figure

    from rtpl.charts.forward_chart import render_income
    from rtpl.theme import Theme

    fig = Figure()
    render_income(fig, res, {"cg0": True, "irmaa:1": True, "irmaa:2": True}, Theme.load("default").palette("light"))
    texts = [t.get_text() for t in fig.axes[0].texts]
    assert "0% CG" in texts and "IRMAA 1" in texts and "IRMAA 2" in texts


def test_nothing_starts_before_the_plan(lib):
    prof = single(birth=1960)
    lives_to(prof, 70)
    plan = Plan(
        profile=prof,
        accounts=[Account(id="ira", kind="traditional_ira", owner=0, balance=500_000)],
        incomes=[IncomeStream(kind="pension", amount=10_000, owner=0, start=When(year=2015), name="Old pension")],
        rules=[Rule(kind="withdrawal", account="ira", start=When(year=2015), end=When(year=2027), mode="spread"),
               Rule(kind="withdrawal", account="ira", start=When(year=2030), end=When(year=2029), mode="fixed",
                    amount=5_000)],
        assumptions=Assumptions(),
    )
    assert plan.rules[0].years(prof) == (2026, 2027)
    res = run_plan(plan, lib, with_lines=False)
    assert res.years[0].category_totals()["ira_withdrawal"] == pytest.approx(500_000 / 2 * 1, rel=0.02)
    assert res.years[0].category_totals()["pension"] == pytest.approx(10_000)
    notes = " ".join(res.notes)
    assert "Old pension: starts in 2015" in notes and "ends before it starts" in notes


def test_spread_rule_on_inherited_ira_stops_at_the_deadline(lib):
    prof = single(birth=1980)
    lives_to(prof, 62)
    plan = Plan(
        profile=prof,
        accounts=[Account(id="inh", kind="inherited_ira", owner=0, balance=100_000, inherited_year=2024,
                          original_owner_birth_year=1940, owner_started_rmds=False, return_rate=0.0)],
        rules=[Rule(kind="withdrawal", account="inh", start=When(year=2026), end=When(year=2040), mode="spread")],
        assumptions=Assumptions(),
    )
    res = run_plan(plan, lib, with_lines=False)
    taken = {y.year: y.category_totals()["inherited"] for y in res.years}
    # Nine years to the 2034 deadline, not 15 years to the rule's end.
    assert taken[2026] == pytest.approx(100_000 / 9, rel=1e-6)
    assert taken[2034] == pytest.approx(taken[2026], rel=1e-6)
    assert taken.get(2035, 0) == 0


def test_conversion_grows_at_the_roth_accounts_own_return(lib):
    prof = single(birth=1960)
    lives_to(prof, 67)
    plan = Plan(
        profile=prof,
        accounts=[Account(id="ira", kind="traditional_ira", owner=0, balance=500_000, return_rate=0.0),
                  Account(id="roth", kind="roth_ira", owner=0, balance=0.0, return_rate=0.10)],
        rules=[Rule(kind="roth_conversion", account="ira", start=When(year=2026), mode="fixed", amount=50_000,
                    to_account="roth")],
        assumptions=Assumptions(return_rate=0.02),
    )
    res = run_plan(plan, lib, with_lines=False)
    assert res.years[0].balances_end["roth"] == pytest.approx(55_000)


# -- plan file version 2 ---------------------------------------------------------

V1 = Path("tests/data/plan_v1.json")


def test_version_1_plan_files_migrate_and_give_the_same_result(lib, tmp_path):
    old = Plan.load(V1)
    new = Plan.load(EXAMPLE)
    assert [p.life_expectancy for p in old.profile.people] == [88, 94]
    assert [s.cola for s in old.incomes] == ["fixed", "none", "fixed", "none"]
    assert old.incomes[0].growth == pytest.approx(0.03)
    a, b = run_plan(old, lib, with_lines=False), run_plan(new, lib, with_lines=False)
    assert [y.year for y in a.years] == [y.year for y in b.years]
    assert sum(y.tax.total_tax for y in a.years) == pytest.approx(sum(y.tax.total_tax for y in b.years))
    # Saving writes version 2, without the old fields.
    old.save(tmp_path / "p.json")
    import json

    data = json.loads((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert "death_age" not in data["people"][0] and "end_age" not in data["assumptions"]
    assert "inflation" not in data["assumptions"]
    assert data["profile"]["people"][0]["life_expectancy"] == 88


def test_migration_moves_inflation_end_age_and_beneficiary():
    data = {
        "profile": {"year": 2026, "filing_status": "mfj", "inflation": 0.025,
                    "people": [{"name": "A", "birth_year": 1960}, {"name": "B", "birth_year": 1962}]},
        "people": [{"death_age": 85}, {"death_age": None}],
        "accounts": [{"id": "inh", "kind": "inherited_ira", "inherited_year": 2022,
                      "eligible_designated_beneficiary": True}],
        "assumptions": {"inflation": 0.03, "end_age": 92},
    }
    plan = Plan.from_dict(data)
    assert [p.life_expectancy for p in plan.profile.people] == [85, 92]
    assert plan.inflation == pytest.approx(0.03) and plan.profile.inflation == pytest.approx(0.03)
    assert plan.accounts[0].beneficiary == "close_in_age"
    assert plan.accounts[0].deadline_year(plan.profile) is None
    assert plan.end_year == 1962 + 92
    assert plan.assumptions.spending_order == ["cash", "taxable"]


def test_death_and_end_of_plan_timings():
    prof = couple(births=(1960, 1964))
    prof.people[0].life_expectancy = 80  # dies 2040
    prof.people[1].life_expectancy = 90  # dies 2054
    assert When(event="death:0").resolve(prof) == 2040
    assert When(event="death:0").resolve(prof, start=True) == 2041  # the first year without them
    assert When(event="first_death").resolve(prof) == 2040
    assert When(event="last_death").resolve(prof) == 2054
    assert When(event="death:1").describe(prof) == "B dies"
    assert When(event="first_death").describe(prof, start=True) == "After the first death"
    plan = Plan(profile=prof, incomes=[
        IncomeStream(kind="annuity", amount=10_000, owner=1, start=When(event="first_death"), cola="none"),
        IncomeStream(kind="pension", amount=5_000, owner=0, end=When(event="first_death"), cola="none"),
    ])
    assert plan.end_year == 2054
    engine = PlanEngine(plan, None)
    assert engine.stream_amount(plan.incomes[0], 2040) == 0
    assert engine.stream_amount(plan.incomes[0], 2041) == 10_000
    assert engine.stream_amount(plan.incomes[1], 2040) == 5_000
    assert engine.stream_amount(plan.incomes[1], 2041) == 0


def test_cola_choices():
    prof = single(birth=1960)
    prof.inflation = 0.02
    plan = Plan(profile=prof, assumptions=Assumptions(ss_cola=0.03))
    engine = PlanEngine(plan, None)
    streams = {k: IncomeStream(kind="pension", amount=10_000, cola=k, growth=0.05)
               for k in ("none", "inflation", "ss_cola", "fixed")}
    got = {k: engine.stream_amount(s, 2028) for k, s in streams.items()}
    assert got["none"] == 10_000
    assert got["inflation"] == pytest.approx(10_000 * 1.02 ** 2)
    assert got["ss_cola"] == pytest.approx(10_000 * 1.03 ** 2)
    assert got["fixed"] == pytest.approx(10_000 * 1.05 ** 2)


def test_fixed_rule_amounts_can_grow(lib):
    prof = single(birth=1964)
    prof.inflation = 0.03
    lives_to(prof, 70)
    rule = Rule(kind="roth_conversion", account="ira", start=When(year=2028), end=When(year=2031),
                mode="fixed", amount=50_000)
    plan = Plan(profile=prof, accounts=[Account(id="ira", kind="traditional_ira", balance=2_000_000)], rules=[rule])
    conv = lambda: {y.year: y.category_totals()["roth_conversion"]  # noqa: E731
                    for y in run_plan(plan, lib, with_lines=False).years}
    assert conv()[2031] == 50_000  # flat
    rule.growth = "inflation"  # today's dollars
    assert conv()[2028] == pytest.approx(50_000 * 1.03 ** 2)
    rule.growth, rule.growth_rate = "fixed", -0.10  # a falling glide path from the first year
    got = conv()
    assert got[2028] == pytest.approx(50_000) and got[2030] == pytest.approx(50_000 * 0.9 ** 2)


def test_survivor_spending(lib):
    prof = couple(births=(1960, 1960))
    prof.people[0].life_expectancy = 70  # dies 2030
    prof.people[1].life_expectancy = 72
    plan = Plan(profile=prof, accounts=[Account(id="cash", kind="cash", owner=None, balance=2_000_000)],
                assumptions=Assumptions(spending=100_000, survivor_spending_pct=0.6))
    by_year = {y.year: y for y in run_plan(plan, lib, with_lines=False).years}
    f = (1 + prof.inflation) ** (2031 - 2026)
    assert by_year[2030].spending == pytest.approx(100_000 * (1 + prof.inflation) ** 4)
    assert by_year[2031].spending == pytest.approx(60_000 * f)


def test_spending_order_can_use_traditional_and_roth_accounts(lib):
    prof = single(birth=1958)
    lives_to(prof, 70)
    accounts = [Account(id="ira", kind="traditional_ira", balance=1_000_000, return_rate=0.0),
                Account(id="roth", kind="roth_ira", balance=100_000, return_rate=0.0)]
    plan = Plan(profile=prof, accounts=accounts, assumptions=Assumptions(spending=60_000))
    y0 = run_plan(plan, lib, with_lines=False).years[0]
    assert y0.shortfall > 0 and "Short" in " ".join(y0.notes)  # cash and brokerage only, and there are none
    plan.assumptions.spending_order = ["traditional", "roth"]
    y0 = run_plan(plan, lib, with_lines=False).years[0]
    ct = y0.category_totals()
    assert y0.shortfall == 0
    # The withdrawal pays the spending and its own tax.
    assert ct["ira_withdrawal"] == pytest.approx(60_000 + y0.tax.total_tax, abs=2)
    assert y0.tax.federal_tax > 0 and ct["roth_withdrawal"] == 0
    plan.assumptions.spending_order = ["roth", "traditional"]
    ct = run_plan(plan, lib, with_lines=False).years[0].category_totals()
    assert ct["roth_withdrawal"] == pytest.approx(60_000, abs=2) and ct["ira_withdrawal"] == 0


def test_inherited_ira_rules_from_who_inherited_it():
    prof = single(birth=1990)
    acct = Account(id="inh", kind="inherited_ira", owner=0, inherited_year=2024, original_owner_birth_year=1950)
    assert acct.inherited_rule(prof).deadline == 2034  # other person: 10 years
    acct.beneficiary = "spouse"
    assert acct.inherited_rule(prof).stretch and acct.deadline_year(prof) is None
    acct.beneficiary = "other"
    acct.inherited_year = 2018
    assert acct.deadline_year(prof) is None  # deaths before 2020 stretch for anyone
    acct.inherited_year = 2024
    acct.beneficiary = "minor_child"
    prof.people[0].birth_year = 2010  # turns 21 in 2031
    assert acct.deadline_year(prof) == 2041
    acct.beneficiary = "estate"
    acct.owner_started_rmds = False
    assert acct.deadline_year(prof) == 2029  # 5-year rule
    acct.owner_started_rmds = True
    rule = acct.inherited_rule(prof)
    assert rule.owner_life and rule.deadline is None


def test_estate_uses_the_owners_remaining_life(lib):
    prof = single(birth=1970)
    lives_to(prof, 60)
    acct = Account(id="inh", kind="inherited_ira", owner=0, balance=100_000, inherited_year=2024,
                   original_owner_birth_year=1944, owner_started_rmds=True, beneficiary="estate", return_rate=0.0)
    y = run_plan(Plan(profile=prof, accounts=[acct]), lib, with_lines=False).years[0]
    period = default_tables().single_period(2024 - 1944) - 1 - (2026 - 2025)
    assert y.category_totals()["inherited"] == pytest.approx(100_000 / period)


def test_successor_of_a_stretching_inherited_ira_gets_ten_years(lib):
    prof = couple(births=(1960, 1962))
    prof.people[0].life_expectancy = 70  # dies 2030
    prof.people[1].life_expectancy = 90
    acct = Account(id="inh", kind="inherited_ira", owner=0, balance=300_000, inherited_year=2018,
                   original_owner_birth_year=1935, return_rate=0.0)
    res = run_plan(Plan(profile=prof, accounts=[acct]), lib, with_lines=False)
    by_year = {y.year: y for y in res.years}
    assert any("empty it by 2040" in n for n in by_year[2031].notes)
    assert by_year[2040].balances_end["inh"] == pytest.approx(0.0)
    assert by_year[2039].balances_end["inh"] > 0
