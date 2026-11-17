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
    ed.add_item("roth_conversion")
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
    form.inflation.setValue(3.0)
    assert plan.assumptions.spending == 90000 and plan.assumptions.inflation == pytest.approx(0.03)
    form.inflation.setValue(form.inflation.minimum())
    assert plan.assumptions.inflation is None
    pick(ed, plan.people[1])
    pf = ed.forms["people"]
    assert pf.claim.value() == 62
    pf.claim.setValue(67)
    pf.death.setValue(90)
    assert plan.people[1].social_security.claim_age == 67 and plan.people[1].death_age == 90
    assert "claims at 67" in find_item(ed, plan.people[1]).text(0)


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
