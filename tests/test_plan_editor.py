"""The Plan items tab: list, forms, add/duplicate/delete, switching items off."""

import os
from pathlib import Path

import pytest

from rtpl.engine.forward import run_plan
from rtpl.model.plan import Plan

PLAN = Path("examples/plans/couple_conversions_inherited.json")


def test_active_plan_leaves_out_switched_off_items():
    plan = Plan.load(PLAN)
    plan.incomes[0].enabled = False
    plan.accounts[2].enabled = False  # the inherited IRA; its 1/N rule goes too
    active = plan.active()
    assert plan.incomes[0] not in active.incomes and len(active.incomes) == len(plan.incomes) - 1
    assert all(a.enabled for a in active.accounts)
    assert all(r.account != plan.accounts[2].id for r in active.rules)
    assert len(plan.accounts) == 6  # the plan itself is unchanged


def test_switching_off_wages_changes_the_result(lib):
    plan = Plan.load(PLAN)
    before = run_plan(plan, lib)
    plan.incomes[0].enabled = False
    after = run_plan(plan, lib)
    assert before.years[0].category_totals()["wages"] > 0
    assert after.years[0].category_totals()["wages"] == 0


def test_new_account_id_is_unique():
    plan = Plan.load(PLAN)
    a = plan.new_account_id("roth_ira", 1)
    assert a == "p2_roth_ira"
    from rtpl.model.plan import Account

    plan.accounts.append(Account(id=a, kind="roth_ira", owner=1))
    assert plan.new_account_id("roth_ira", 1) == "p2_roth_ira_2"
    assert plan.new_account_id("taxable", None) == "joint_taxable"


def test_enabled_round_trips_and_old_files_load(tmp_path):
    plan = Plan.load(PLAN)
    assert all(a.enabled for a in plan.accounts)  # files without the field load as on
    plan.incomes[1].enabled = False
    path = tmp_path / "p.json"
    plan.save(path)
    assert Plan.load(path).incomes[1].enabled is False


# -- the app ------------------------------------------------------------------

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def window(tmp_path, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    QtCore = pytest.importorskip("PySide6.QtCore")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from rtpl.app.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow()
    w.load_plan(Plan.load(PLAN), PLAN)
    yield w
    w.setWindowModified(False)
    w.close()
    w.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def find_item(editor, obj):
    from rtpl.app.plan_editor import ROLE

    for g in editor.groups.values():
        for i in range(g.childCount()):
            if g.child(i).data(0, ROLE)[1] is obj:
                return g.child(i)
    raise AssertionError("not listed")


def pick(editor, obj):
    item = find_item(editor, obj)
    editor.tree.setCurrentItem(item)
    return item


def test_plan_items_tab_lists_the_plan(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    assert window.input_tabs.isTabVisible(0) and window.input_tabs.currentWidget() is ed
    assert "Plan" in window.inputs_panel.title.text()
    counts = {k: g.childCount() for k, g in ed.groups.items()}
    assert counts == {"accounts": 6, "incomes": 4, "people": 2, "rules": 4, "assumptions": 1}
    assert find_item(ed, plan.rules[1]).text(0) == "Convert Alex Traditional IRA: fill to top of the 22% bracket"
    # The marginal graph page shows only the profile.
    window.pages.setCurrentIndex(0)
    assert not window.input_tabs.isTabVisible(0) and window.input_tabs.currentWidget() is window.form
    assert "Inputs" in window.inputs_panel.title.text()


def test_editing_a_balance_recalculates(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    tax_before = sum(y.tax.total_tax for y in window.plan_view.result.years)
    pick(ed, plan.accounts[0])
    form = ed.forms["accounts"]
    assert ed.stack.currentWidget() is form and form.balance.value() == 1_100_000
    form.balance.setValue(2_000_000)
    assert plan.accounts[0].balance == 2_000_000
    assert "$2.00M" in find_item(ed, plan.accounts[0]).text(0)
    assert window.isWindowModified()
    window._plan_timer.timeout.emit()  # the debounce timer firing
    tax_after = sum(y.tax.total_tax for y in window.plan_view.result.years)
    assert tax_after > tax_before
    assert "Last change: tax" in window.plan_view.summary.text()


def test_conversion_form_edits_the_rule(window):
    ed = window.plan_editor
    rule = window.plan_view.plan.rules[1]
    pick(ed, rule)
    form = ed.forms["rules"]
    assert form.mode.currentData() == "fill" and form.target.currentData() == "bracket:0.22"
    assert form.start.mode.currentText() == "Alex's age" and form.start.spin.value() == 63
    form.end.spin.setValue(72)
    assert rule.end.age == 72 and rule.end.person == 0
    form.target.setCurrentIndex(form.target.findData("bracket:0.24"))
    assert rule.target == "bracket:0.24"
    form.target.setCurrentIndex(form.target.findData("magi"))
    form.magi.setValue(150000)
    assert rule.target == "magi:150000"
    # Switching the end to a year keeps the same calendar year.
    form.end.mode.setCurrentIndex(form.end.mode.findData("year"))
    assert rule.end.year == 1964 + 72


def test_untick_an_income_switches_it_off(window):
    from PySide6.QtCore import Qt

    ed = window.plan_editor
    wages = window.plan_view.plan.incomes[0]
    item = find_item(ed, wages)
    item.setCheckState(0, Qt.CheckState.Unchecked)
    assert wages.enabled is False
    window._plan_timer.timeout.emit()
    assert window.plan_view.result.years[0].category_totals()["wages"] == 0


def test_add_duplicate_and_delete(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    ed.add_actions["roth_conversion"].trigger()  # the Add item menu
    new = plan.rules[-1]
    assert new.kind == "roth_conversion" and ed.current_object() is new
    ed.duplicate_item()
    assert len(plan.rules) == 6 and plan.rules[-1] is not new
    ed.delete_item()
    ed.delete_item()
    assert len(plan.rules) == 4
    ed.add_item("account")
    acct = plan.accounts[-1]
    assert acct.id == "p1_traditional_ira" and ed.current_object() is acct
    ed.duplicate_item()
    assert plan.accounts[-1].id == "p1_traditional_ira_2"
    window._plan_timer.timeout.emit()
    assert window.plan_view.result is not None


def test_deleting_an_account_asks_about_its_strategies(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    ira = plan.accounts[0]
    pick(ed, ira)
    ed.delete_item(confirm=lambda _text: False)
    assert ira in plan.accounts
    ed.delete_item(confirm=lambda _text: True)
    assert ira not in plan.accounts and all(r.account != ira.id for r in plan.rules)


def test_save_plan_writes_edits(window, tmp_path):
    plan = window.plan_view.plan
    plan.assumptions.spending = 95000
    window.plan_path = tmp_path / "edited.plan.json"
    assert window.save_profile()  # Save saves the open plan
    assert Plan.load(window.plan_path).assumptions.spending == 95000
    assert not window.isWindowModified()
    assert window.windowTitle().startswith("edited.plan.json")


def test_assumptions_and_person_forms(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    pick(ed, plan.assumptions)
    form = ed.forms["assumptions"]
    form.spending.setValue(90000)
    assert form.inflation.is_default() and not form.inflation.field.isEnabled()
    assert form.inflation.field.value() == pytest.approx(100 * plan.profile.inflation)
    form.inflation.check.setChecked(False)
    form.inflation.field.setValue(3.0)
    assert plan.assumptions.spending == 90000 and plan.assumptions.inflation == pytest.approx(0.03)
    assert form.cola.is_default() and form.cola.field.value() == pytest.approx(3.0)  # shows inflation
    form.inflation.check.setChecked(True)
    assert plan.assumptions.inflation is None
    assert form.form.labelForField(form.magi1).text() == f"IRMAA MAGI for {plan.start_year - 2}"
    form.magi1.setValue(0)
    assert form.magi1.text() == "Not entered" and str(plan.start_year - 2) not in plan.assumptions.prior_magi
    pick(ed, plan.people[1])
    pf = ed.forms["people"]
    assert pf.claim.value() == 62
    pf.claim.years.setValue(67)
    pf.claim.months.setValue(5)
    assert plan.people[1].social_security.claim_age == pytest.approx(67 + 5 / 12)
    assert "claims at 67 and 5 months" in find_item(ed, plan.people[1]).text(0)
    pf.claim.years.setValue(70)
    assert pf.claim.months.maximum() == 0 and plan.people[1].social_security.claim_age == 70
    assert not pf.death.is_default() and pf.death.field.value() == plan.people[1].death_age
    pf.death.field.setValue(90)
    assert plan.people[1].death_age == 90
    pf.death.check.setChecked(True)
    assert plan.people[1].death_age is None and not pf.death.field.isEnabled()
    assert pf.death.field.value() == plan.assumptions.end_age  # shows what "end of plan" means
    pf.ssdi.setChecked(True)
    assert pf.ssdi_end.is_default() and plan.people[1].social_security.ssdi_end_age is None
    pf.ssdi_end.check.setChecked(False)
    pf.ssdi_end.field.setValue(60.5)
    assert plan.people[1].social_security.ssdi_end_age == pytest.approx(60.5)


def test_account_form_cash_basis_and_inherited_deadline(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    form = ed.forms["accounts"]
    taxable = next(a for a in plan.accounts if a.kind == "taxable")
    pick(ed, taxable)
    assert form.interest.decimals() == 4
    form.interest.setValue(0.0175)
    assert form.interest.text() == "0.0175%"
    form.interest.setValue(1.5)
    assert form.interest.text() == "1.50%"
    form.interest.setValue(0.0175)
    assert taxable.interest_yield == pytest.approx(0.000175)
    form.basis.check.setChecked(False)
    form.basis.field.setValue(123_000)
    assert taxable.cost_basis == 123_000
    form.basis.check.setChecked(True)
    assert taxable.cost_basis is None and form.basis.field.value() == taxable.balance
    # A cash account hides the return and yield it would ignore.
    form.kind.setCurrentIndex(form.kind.findData("cash"))
    assert not form.form.isRowVisible(form.return_rate) and not form.form.isRowVisible(form.interest)
    assert form.form.isRowVisible(form.cash_note) and "Cash return" in form.cash_note.text()
    inherited = next(a for a in plan.accounts if a.kind == "inherited_ira")
    pick(ed, inherited)
    assert f"empty by {inherited.deadline_year}" in form.deadline.text()
    # A rule on it can't end after the deadline.
    rule = next(r for r in plan.rules if r.account == inherited.id)
    pick(ed, rule)
    rf = ed.forms["rules"]
    assert rf.form.isRowVisible(rf.deadline)
    rf.end.mode.setCurrentIndex(rf.end.mode.findData("year"))
    rf.end.spin.setValue(inherited.deadline_year + 5)
    assert rf.end.spin.maximum() == inherited.deadline_year
    assert rule.end.resolve(plan.profile) == inherited.deadline_year


def test_new_plan_from_profile_can_be_built_in_the_app(window):
    window.new_plan()
    ed = window.plan_editor
    plan = window.plan_view.plan
    assert ed.plan is plan and ed.groups["accounts"].childCount() == 0
    ed.add_item("account")
    ed.forms["accounts"].balance.setValue(500000)
    ed.add_item("roth_conversion")
    window._plan_timer.timeout.emit()
    assert window.plan_view.result.years[0].category_totals()["roth_conversion"] > 0


def test_plan_name_round_trips_and_titles(tmp_path):
    plan = Plan.load(PLAN)
    assert plan.name == "" and plan.title == plan.profile.name
    plan.name = "Roth conversions until SS"
    assert plan.title == "Couple with conversions and an inherited IRA - Roth conversions until SS"
    plan.save(tmp_path / "p.json")
    assert Plan.load(tmp_path / "p.json").name == "Roth conversions until SS"


def test_plan_name_field_and_suggested_file_name(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    window.setWindowModified(False)
    ed.name.setText("David retires early")
    ed.name.textEdited.emit("David retires early")  # as if typed
    assert plan.name == "David retires early" and window.isWindowModified()
    window.plan_path = None
    window.form.plan_name.setText("Smith: household")
    window.form.changed.emit()
    path = Path(window._suggested_plan_path(".plan.json"))
    assert path.name == "Smith_ household - David retires early.plan.json"
    window._plan_timer.timeout.emit()
    assert "David retires early" in window.plan_view.summary.text()


def test_baseline_tab_gets_its_full_height(window):
    """Switching to the Baseline tab on the plan page must not squash the
    profile form: the panel measures only the page on show, right away."""
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    window.resize(1200, 700)
    window.show()
    # Saved panel settings can leak between tests on Windows and macOS
    # (QSettings ignores XDG_CONFIG_HOME there), so open the panel explicitly.
    window.panels.set_pinned(window.inputs_panel, True)
    window.panels.set_open(window.inputs_panel, True)
    QtWidgets.QApplication.processEvents()
    for page in (window.form, window.plan_editor, window.form):
        window.input_tabs.setCurrentWidget(page)
        assert page.isVisible() and page.height() >= page.minimumSizeHint().height()
        others = [p for p in (window.form, window.plan_editor) if p is not page]
        assert all(not p.isVisible() for p in others)


def test_timings_cannot_start_before_the_plan_or_end_before_the_start(window):
    ed = window.plan_editor
    plan = window.plan_view.plan
    rule = plan.rules[0]
    pick(ed, rule)
    rf = ed.forms["rules"]
    rf.start.mode.setCurrentIndex(rf.start.mode.findData("year"))
    assert rf.start.spin.minimum() == plan.start_year
    rf.start.spin.setValue(plan.start_year - 5)
    assert rule.start.resolve(plan.profile) == plan.start_year
    rf.end.mode.setCurrentIndex(rf.end.mode.findData("year"))
    rf.start.spin.setValue(plan.start_year + 3)
    assert rf.end.spin.minimum() == plan.start_year + 3
    rf.end.spin.setValue(plan.start_year)
    assert rule.end.resolve(plan.profile) == plan.start_year + 3
    income = plan.incomes[1]
    pick(ed, income)
    inf = ed.forms["incomes"]
    inf.start.mode.setCurrentIndex(inf.start.mode.findData("year"))
    assert inf.start.spin.minimum() == plan.start_year

