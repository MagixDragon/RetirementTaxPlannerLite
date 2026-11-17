"""Plan items: the forward plan's accounts, income, Social Security,
strategies (rules) and assumptions, as a list with a bar for the years each
item covers, and a form for the selected item.

Every change is written straight into the plan and announced with
``changed``, so the graph can recalculate while the user types. A tick box
on accounts, income streams and strategies switches the item off without
deleting it, for quick what-ifs.
"""

from __future__ import annotations

import copy
from typing import Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rtpl.app.inputs import ComboBox, DoubleSpinBox, SpinBox
from rtpl.app.profile_form import compact_form, money_box
from rtpl.model.plan import (
    ACCOUNT_KINDS,
    INCOME_KINDS,
    RULE_KINDS,
    ROTH_KINDS,
    TRADITIONAL_KINDS,
    Account,
    IncomeStream,
    PersonPlan,
    Plan,
    Rule,
    When,
)

ROLE = Qt.ItemDataRole.UserRole  # (kind, object) of a row
BAR_ROLE = Qt.ItemDataRole.UserRole + 1  # (first, last, color, milestone 0 or 1) for the bar column; no bools,
# which PySide 6.12 on Python 3.11 loses references to when they pass through a QVariant

GROUPS = (
    ("accounts", "Accounts"),
    ("incomes", "Income"),
    ("people", "Social Security"),
    ("rules", "Strategies"),
    ("assumptions", "Assumptions"),
)

GROUP_COLORS = {
    "accounts": "#1c5cab",
    "incomes": "#eb6834",
    "people": "#2a78d6",
    "rules": "#e34948",
    "assumptions": "#6b6a66",
}
INCOME_COLORS = {
    "wages": "#eb6834",
    "pension": "#1baf7a",
    "foreign_pension": "#1baf7a",
    "annuity": "#1baf7a",
    "ltcg": "#6da7ec",
    "qualified_dividends": "#1c5cab",
    "interest": "#eda100",
    "other_ordinary": "#eda100",
    "tax_exempt": "#aeaca5",
}
JOINT_KINDS = ("taxable", "cash")
SURVIVOR_KINDS = ("pension", "foreign_pension", "annuity")

# Fill targets offered for "fill" rules, as (label, target).
FILL_TARGETS = [
    ("Top of the 10% bracket", "bracket:0.10"),
    ("Top of the 12% bracket", "bracket:0.12"),
    ("Top of the 22% bracket", "bracket:0.22"),
    ("Top of the 24% bracket", "bracket:0.24"),
    ("Top of the 32% bracket", "bracket:0.32"),
    ("Top of the 35% bracket", "bracket:0.35"),
    ("Top of the 0% capital gains band", "cg0"),
    ("Just under IRMAA tier 1", "irmaa:1"),
    ("Just under IRMAA tier 2", "irmaa:2"),
    ("Just under IRMAA tier 3", "irmaa:3"),
    ("Just under IRMAA tier 4", "irmaa:4"),
    ("Just under IRMAA tier 5", "irmaa:5"),
    ("ACA: 400% of the poverty line", "aca:4.0"),
    ("A MAGI amount", "magi"),
]
MODE_LABELS = {
    "fixed": "A fixed amount each year",
    "fill": "Fill up to a target",
    "spread": "Spread the balance evenly (1/N)",
    "all": "The whole balance",
}


def short_money(v: float) -> str:
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.2f}M"
    if abs(v) >= 1e4:
        return f"${v / 1e3:,.0f}K"
    return f"${v:,.0f}"


def target_label(target: str) -> str:
    if target.startswith("magi:"):
        try:
            return f"MAGI {short_money(float(target.split(':', 1)[1]))}"
        except ValueError:
            return target
    label = FILL_TARGETS[target_index(target)][0]
    if FILL_TARGETS[target_index(target)][1] != target and not target.startswith("bracket:"):
        return target
    return label[0].lower() + label[1:]


def target_index(target: str) -> int:
    """Position of a fill target in FILL_TARGETS (bracket:0.1 matches bracket:0.10)."""
    for i, (_, t) in enumerate(FILL_TARGETS):
        if t == target:
            return i
        if t.startswith("bracket:") and target.startswith("bracket:") and abs(float(t[8:]) - _float(target[8:])) < 1e-9:
            return i
    if target.startswith("magi:"):
        return len(FILL_TARGETS) - 1
    return 2


def _float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        return -1.0


def percent_box(lo: float = 0.0, hi: float = 100.0, step: float = 0.1) -> DoubleSpinBox:
    box = DoubleSpinBox()
    box.setSuffix("%")
    box.setDecimals(2)
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setMaximumWidth(110)
    return box


class WhenEdit(QWidget):
    """A timing: one person's age, a calendar year, or (if allowed) none."""

    changed = Signal()

    def __init__(self, none_label: str | None = None) -> None:
        super().__init__()
        self.none_label = none_label
        self.plan: Plan | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.mode = ComboBox()
        self.spin = SpinBox()
        self.spin.setMaximumWidth(80)
        self.result = QLabel()
        self.result.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.mode)
        lay.addWidget(self.spin)
        lay.addWidget(self.result, 1)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.spin.valueChanged.connect(self._value_changed)
        self._loading = False

    def set_plan(self, plan: Plan) -> None:
        self.plan = plan
        self._loading = True
        self.mode.clear()
        if self.none_label:
            self.mode.addItem(self.none_label, "none")
        for i, p in enumerate(plan.profile.people):
            self.mode.addItem(f"{p.name}'s age", i)
        self.mode.addItem("Year", "year")
        self._loading = False

    def setAccessibleName(self, name: str) -> None:  # noqa: N802 (Qt name)
        super().setAccessibleName(name)
        self.mode.setAccessibleName(f"{name}: measured by")
        self.spin.setAccessibleName(f"{name}: age or year")

    def set_value(self, when: When | None) -> None:
        self._loading = True
        if when is None:
            self.mode.setCurrentIndex(max(0, self.mode.findData("none")))
        elif when.year is not None:
            self.mode.setCurrentIndex(self.mode.findData("year"))
        else:
            idx = self.mode.findData(min(when.person, len(self.plan.profile.people) - 1))
            self.mode.setCurrentIndex(max(0, idx))
        self._apply_range()
        if when is not None:
            self.spin.setValue(int(when.year if when.year is not None else when.age))
        self._loading = False
        self._show_result()

    def value(self) -> When | None:
        data = self.mode.currentData()
        if data == "none":
            return None
        if data == "year":
            return When(year=self.spin.value())
        return When(age=self.spin.value(), person=int(data))

    def _apply_range(self) -> None:
        data = self.mode.currentData()
        self.spin.setVisible(data != "none")
        if data == "year":
            self.spin.setRange(1900, 2200)
        else:
            self.spin.setRange(0, 120)

    def _mode_changed(self, *_args) -> None:
        if self._loading or self.plan is None:
            return
        # Keep the same calendar year when switching between age and year.
        old_year = self._year_of(self._previous) if getattr(self, "_previous", None) else self.plan.start_year
        self._loading = True
        self._apply_range()
        data = self.mode.currentData()
        if data == "year":
            self.spin.setValue(old_year)
        elif data != "none":
            self.spin.setValue(old_year - self.plan.profile.people[int(data)].birth_year)
        self._loading = False
        self._show_result()
        self.changed.emit()

    def _value_changed(self, *_args) -> None:
        if self._loading:
            return
        self._show_result()
        self.changed.emit()

    def _year_of(self, when: When | None) -> int:
        assert self.plan is not None
        return self.plan.start_year if when is None else when.resolve(self.plan.profile)

    def _show_result(self) -> None:
        when = self.value()
        self._previous = when
        if self.plan is None or when is None:
            self.result.setText("")
            return
        year = when.resolve(self.plan.profile)
        if when.year is not None:
            ages = [f"{p.name} {year - p.birth_year}" for p in self.plan.profile.people]
            self.result.setText(", ".join(ages))
        else:
            self.result.setText(f"= {year}")


# -- forms -------------------------------------------------------------------------


class ItemForm(QWidget):
    """Base for the per-item forms: load an object, write edits back into it."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.form = compact_form(QFormLayout(self))
        self.form.setContentsMargins(0, 0, 0, 0)
        self.plan: Plan | None = None
        self.obj = None
        self._loading = False

    def row(self, text: str, widget: QWidget, tip: str | None = None) -> QWidget:
        label = QLabel(text)
        label.setBuddy(widget)
        widget.setAccessibleName(text.replace("&", ""))
        if tip:
            widget.setToolTip(tip)
        self.form.addRow(label, widget)
        return widget

    def show_row(self, widget: QWidget, visible: bool) -> None:
        self.form.setRowVisible(widget, visible)

    def watch(self, *widgets: QWidget) -> None:
        for w in widgets:
            if isinstance(w, (DoubleSpinBox, SpinBox)):
                w.valueChanged.connect(self._edited)
            elif isinstance(w, ComboBox):
                w.currentIndexChanged.connect(self._edited)
            elif isinstance(w, QLineEdit):
                w.textEdited.connect(self._edited)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._edited)
            elif isinstance(w, WhenEdit):
                w.changed.connect(self._edited)

    def load(self, plan: Plan, obj) -> None:
        self.plan, self.obj = plan, obj
        self._loading = True
        try:
            self.fill()
        finally:
            self._loading = False
        self.update_rows()

    def _edited(self, *_args) -> None:
        if self._loading or self.obj is None:
            return
        self.store()
        self.update_rows()
        self.changed.emit()

    # Subclasses fill these in.
    def fill(self) -> None: ...
    def store(self) -> None: ...
    def update_rows(self) -> None: ...


def owner_combo(plan: Plan, joint: bool) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = [(p.name, i) for i, p in enumerate(plan.profile.people)]
    if joint:
        out.append(("Joint", None))
    return out


def set_items(combo: ComboBox, items: list[tuple[str, object]], current) -> None:
    combo.blockSignals(True)
    combo.clear()
    for text, data in items:
        combo.addItem(text, data)
    idx = combo.findData(current)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.blockSignals(False)


class AccountForm(ItemForm):
    def __init__(self) -> None:
        super().__init__()
        self.name = self.row("Name", QLineEdit())
        self.kind = self.row("Type", ComboBox())
        for k, label in ACCOUNT_KINDS.items():
            self.kind.addItem(label, k)
        self.owner = self.row("Owner", ComboBox())
        self.balance = self.row("Balance today", money_box())
        self.own_return = self.row("Return", QCheckBox("Own return (otherwise the plan's default)"))
        self.return_rate = self.row("Yearly return", percent_box(-20, 30))
        self.basis = self.row("Cost basis", money_box(), "What you paid; gains above it are taxed when sold")
        self.basis.setMinimum(-1)
        self.basis.setSpecialValueText("Same as balance")
        self.interest = self.row("Interest yield", percent_box(0, 20))
        self.qdiv = self.row("Qualified dividend yield", percent_box(0, 20))
        self.nqdiv = self.row("Non-qualified dividend yield", percent_box(0, 20))
        self.capdist = self.row("Capital gain distributions", percent_box(0, 20))
        self.spouse_bene = self.row("Beneficiary", QCheckBox("Spouse is the sole beneficiary"))
        self.inherited_year = self.row("Year inherited", SpinBox())
        self.inherited_year.setRange(1950, 2200)
        self.orig_birth = self.row("Original owner born", SpinBox())
        self.orig_birth.setRange(1880, 2100)
        self.started = self.row("RMDs", QCheckBox("The original owner had started RMDs"))
        self.edb = self.row("Stretch", QCheckBox("Eligible designated beneficiary (no 10-year limit)"))
        self.watch(self.name, self.kind, self.owner, self.balance, self.own_return, self.return_rate, self.basis,
                   self.interest, self.qdiv, self.nqdiv, self.capdist, self.spouse_bene, self.inherited_year,
                   self.orig_birth, self.started, self.edb)

    def fill(self) -> None:
        a: Account = self.obj
        self.name.setText(a.name)
        self.name.setPlaceholderText(ACCOUNT_KINDS.get(a.kind, a.kind))
        self.kind.setCurrentIndex(max(0, self.kind.findData(a.kind)))
        set_items(self.owner, owner_combo(self.plan, a.kind in JOINT_KINDS), a.owner)
        self.balance.setValue(a.balance)
        self.own_return.setChecked(a.return_rate is not None)
        self.return_rate.setValue(100 * (a.return_rate if a.return_rate is not None else self.plan.assumptions.return_rate))
        self.basis.setValue(-1 if a.cost_basis is None else a.cost_basis)
        self.interest.setValue(100 * a.interest_yield)
        self.qdiv.setValue(100 * a.qualified_dividend_yield)
        self.nqdiv.setValue(100 * a.nonqualified_dividend_yield)
        self.capdist.setValue(100 * a.cap_gain_distribution_yield)
        self.spouse_bene.setChecked(a.spouse_sole_beneficiary)
        self.inherited_year.setValue(a.inherited_year or self.plan.start_year - 1)
        self.orig_birth.setValue(a.original_owner_birth_year or 1945)
        self.started.setChecked(a.owner_started_rmds)
        self.edb.setChecked(a.eligible_designated_beneficiary)

    def store(self) -> None:
        a: Account = self.obj
        a.name = self.name.text().strip()
        kind = self.kind.currentData()
        if kind != a.kind:
            a.kind = kind
            # Joint is only offered for brokerage and cash accounts.
            self._loading = True
            set_items(self.owner, owner_combo(self.plan, kind in JOINT_KINDS), a.owner)
            self.name.setPlaceholderText(ACCOUNT_KINDS.get(kind, kind))
            self._loading = False
        a.owner = self.owner.currentData()
        a.balance = self.balance.value()
        a.return_rate = self.return_rate.value() / 100 if self.own_return.isChecked() else None
        a.cost_basis = None if self.basis.value() < 0 else self.basis.value()
        a.interest_yield = self.interest.value() / 100
        a.qualified_dividend_yield = self.qdiv.value() / 100
        a.nonqualified_dividend_yield = self.nqdiv.value() / 100
        a.cap_gain_distribution_yield = self.capdist.value() / 100
        a.spouse_sole_beneficiary = self.spouse_bene.isChecked()
        if a.kind == "inherited_ira":
            a.inherited_year = self.inherited_year.value()
            a.original_owner_birth_year = self.orig_birth.value()
        a.owner_started_rmds = self.started.isChecked()
        a.eligible_designated_beneficiary = self.edb.isChecked()

    def update_rows(self) -> None:
        kind = self.kind.currentData()
        self.return_rate.setEnabled(self.own_return.isChecked())
        taxable = kind == "taxable"
        for w in (self.basis, self.interest, self.qdiv, self.nqdiv, self.capdist):
            self.show_row(w, taxable)
        self.show_row(self.interest, kind in ("taxable", "cash"))
        self.show_row(self.spouse_bene, kind in TRADITIONAL_KINDS)
        for w in (self.inherited_year, self.orig_birth, self.started, self.edb):
            self.show_row(w, kind == "inherited_ira")


class IncomeForm(ItemForm):
    def __init__(self) -> None:
        super().__init__()
        self.name = self.row("Name", QLineEdit())
        self.kind = self.row("Type", ComboBox())
        for k, label in INCOME_KINDS.items():
            self.kind.addItem(label, k)
        self.owner = self.row("Owner", ComboBox())
        self.amount = self.row("Yearly amount", money_box(), "In the first year it is paid")
        self.start = self.row("Starts", WhenEdit("Plan start"))
        self.end = self.row("Ends (last year paid)", WhenEdit("For life"))
        self.growth = self.row("Yearly increase", percent_box(-10, 20), "COLA or raises after the first year")
        self.survivor = self.row("To the survivor", percent_box(0, 100, 5), "Share that continues after the owner dies")
        self.taxable = self.row("Federally taxable", percent_box(0, 100, 5))
        self.watch(self.name, self.kind, self.owner, self.amount, self.start, self.end, self.growth,
                   self.survivor, self.taxable)

    def fill(self) -> None:
        s: IncomeStream = self.obj
        self.name.setText(s.name)
        self.name.setPlaceholderText(INCOME_KINDS.get(s.kind, s.kind))
        self.kind.setCurrentIndex(max(0, self.kind.findData(s.kind)))
        set_items(self.owner, owner_combo(self.plan, True), s.owner)
        self.amount.setValue(s.amount)
        for w, when in ((self.start, s.start), (self.end, s.end)):
            w.set_plan(self.plan)
            w.set_value(when)
        self.growth.setValue(100 * s.growth)
        self.survivor.setValue(100 * s.survivor_pct)
        self.taxable.setValue(100 * s.federal_taxable_pct)

    def store(self) -> None:
        s: IncomeStream = self.obj
        s.name = self.name.text().strip()
        s.kind = self.kind.currentData()
        self.name.setPlaceholderText(INCOME_KINDS.get(s.kind, s.kind))
        s.owner = self.owner.currentData()
        s.amount = self.amount.value()
        s.start = self.start.value()
        s.end = self.end.value()
        s.growth = self.growth.value() / 100
        s.survivor_pct = self.survivor.value() / 100
        s.federal_taxable_pct = self.taxable.value() / 100

    def update_rows(self) -> None:
        self.show_row(self.survivor, self.kind.currentData() in SURVIVOR_KINDS)


class PersonForm(ItemForm):
    def __init__(self) -> None:
        super().__init__()
        self.fra = self.row("Benefit at full retirement age", money_box(),
                            "Yearly benefit at full retirement age, in today's dollars (from your SSA statement)")
        self.claim = self.row("Claiming age", DoubleSpinBox())
        self.claim.setRange(62, 70)
        self.claim.setDecimals(2)
        self.claim.setSingleStep(1 / 12)
        self.claim.setMaximumWidth(110)
        self.claim.setToolTip("Years; months as a fraction, e.g. 67.5 is 67 and 6 months")
        self.claimed = self.row("Already claimed", QCheckBox("Benefit is already being paid"))
        self.current = self.row("Yearly benefit now", money_box())
        self.ssdi = self.row("Disability", QCheckBox("Receiving SSDI"))
        self.ssdi_end = self.row("SSDI ends at age", DoubleSpinBox())
        self.ssdi_end.setRange(17, 67)
        self.ssdi_end.setDecimals(1)
        self.ssdi_end.setMinimum(17)
        self.ssdi_end.setSpecialValueText("At full retirement age")
        self.death = self.row("Life expectancy", SpinBox(), "Age at death in the plan; the survivor files single after")
        self.death.setRange(49, 120)
        self.death.setSpecialValueText("End of plan")
        self.watch(self.fra, self.claim, self.claimed, self.current, self.ssdi, self.ssdi_end, self.death)

    def fill(self) -> None:
        pp: PersonPlan = self.obj
        ss = pp.social_security
        self.fra.setValue(ss.fra_benefit)
        self.claim.setValue(ss.claim_age)
        self.claimed.setChecked(ss.current_benefit is not None)
        self.current.setValue(ss.current_benefit or 0)
        self.ssdi.setChecked(ss.ssdi)
        self.ssdi_end.setValue(ss.ssdi_end_age if ss.ssdi_end_age is not None else 17)
        self.death.setValue(pp.death_age if pp.death_age is not None else 49)

    def store(self) -> None:
        pp: PersonPlan = self.obj
        ss = pp.social_security
        ss.fra_benefit = self.fra.value()
        ss.claim_age = self.claim.value()
        ss.current_benefit = self.current.value() if self.claimed.isChecked() else None
        ss.ssdi = self.ssdi.isChecked()
        ss.ssdi_end_age = None if self.ssdi_end.value() <= 17 else self.ssdi_end.value()
        pp.death_age = None if self.death.value() <= 49 else self.death.value()

    def update_rows(self) -> None:
        claimed = self.claimed.isChecked()
        self.show_row(self.current, claimed)
        self.show_row(self.ssdi_end, self.ssdi.isChecked())


class RuleForm(ItemForm):
    def __init__(self) -> None:
        super().__init__()
        self.kind = self.row("Strategy", ComboBox())
        for k, label in RULE_KINDS.items():
            self.kind.addItem(label, k)
        self.account = self.row("From account", ComboBox())
        self.to_account = self.row("Into", ComboBox())
        self.start = self.row("Starts", WhenEdit())
        self.end = self.row("Ends (last year)", WhenEdit("Same year (one year only)"))
        self.mode = self.row("Amount", ComboBox())
        for k, label in MODE_LABELS.items():
            self.mode.addItem(label, k)
        self.amount = self.row("Each year", money_box())
        self.target = self.row("Fill up to", ComboBox())
        for label, t in FILL_TARGETS:
            self.target.addItem(label, t)
        self.magi = self.row("MAGI amount", money_box(), "In that year's dollars")
        self.note = self.row("Note", QLineEdit())
        self.watch(self.kind, self.account, self.to_account, self.start, self.end, self.mode, self.amount,
                   self.target, self.magi, self.note)

    def _accounts(self, kind: str) -> list[tuple[str, str]]:
        assert self.plan is not None
        ok = (lambda a: a.kind in TRADITIONAL_KINDS or a.kind == "inherited_ira") if kind == "roth_conversion" \
            else (lambda a: True)
        return [(a.label(self.plan.profile), a.id) for a in self.plan.accounts if ok(a)]

    def _fill_accounts(self) -> None:
        r: Rule = self.obj
        set_items(self.account, self._accounts(r.kind), r.account)
        roths = [("Owner's Roth IRA", None)] + [
            (a.label(self.plan.profile), a.id) for a in self.plan.accounts if a.kind in ROTH_KINDS]
        set_items(self.to_account, roths, r.to_account)

    def fill(self) -> None:
        r: Rule = self.obj
        self.kind.setCurrentIndex(max(0, self.kind.findData(r.kind)))
        self._fill_accounts()
        for w, when in ((self.start, r.start), (self.end, r.end)):
            w.set_plan(self.plan)
            w.set_value(when)
        self.mode.setCurrentIndex(max(0, self.mode.findData(r.mode)))
        self.amount.setValue(r.amount)
        if r.target.startswith("magi:"):
            self.target.setCurrentIndex(self.target.findData("magi"))
            self.magi.setValue(_float(r.target[5:]))
        else:
            self.target.setCurrentIndex(target_index(r.target))
        self.note.setText(r.note)

    def store(self) -> None:
        r: Rule = self.obj
        kind = self.kind.currentData()
        if kind != r.kind:
            r.kind = kind
            self._loading = True
            self._fill_accounts()
            self._loading = False
        if self.account.currentData() is not None:
            r.account = self.account.currentData()
        r.to_account = self.to_account.currentData() if r.kind == "roth_conversion" else None
        start = self.start.value()
        if start is not None:
            r.start = start
        r.end = self.end.value()
        r.mode = self.mode.currentData()
        r.amount = self.amount.value()
        t = self.target.currentData()
        r.target = f"magi:{self.magi.value():.0f}" if t == "magi" else t
        r.note = self.note.text()

    def update_rows(self) -> None:
        mode = self.mode.currentData()
        self.show_row(self.to_account, self.kind.currentData() == "roth_conversion")
        self.show_row(self.amount, mode == "fixed")
        self.show_row(self.target, mode == "fill")
        self.show_row(self.magi, mode == "fill" and self.target.currentData() == "magi")


class AssumptionsForm(ItemForm):
    def __init__(self) -> None:
        super().__init__()
        self.inflation = self.row("Inflation", percent_box(-1.01, 15))
        self.inflation.setSpecialValueText("Same as the baseline")
        self.cola = self.row("Social Security COLA", percent_box(-1.01, 15))
        self.cola.setSpecialValueText("Same as inflation")
        self.ret = self.row("Default return", percent_box(-20, 30), "For accounts without a return of their own")
        self.cash = self.row("Cash return", percent_box(-5, 20))
        self.spending = self.row("Yearly spending", money_box(), "In today's dollars; grows with inflation")
        self.end_age = self.row("Plan runs to age", SpinBox())
        self.end_age.setRange(60, 120)
        self.magi1 = self.row("MAGI two years before", money_box(), "For IRMAA in the plan's first two years")
        self.magi2 = self.row("MAGI last year", money_box())
        self.watch(self.inflation, self.cola, self.ret, self.cash, self.spending, self.end_age, self.magi1,
                   self.magi2)

    def fill(self) -> None:
        a = self.obj
        y = self.plan.start_year
        self.inflation.setValue(-1.01 if a.inflation is None else 100 * a.inflation)
        self.cola.setValue(-1.01 if a.ss_cola is None else 100 * a.ss_cola)
        self.ret.setValue(100 * a.return_rate)
        self.cash.setValue(100 * a.cash_return)
        self.spending.setValue(a.spending)
        self.end_age.setValue(a.end_age)
        self.magi1.setValue(a.prior_magi.get(str(y - 2), 0))
        self.magi2.setValue(a.prior_magi.get(str(y - 1), 0))
        self.form.labelForField(self.magi1).setText(f"MAGI in {y - 2}")
        self.form.labelForField(self.magi2).setText(f"MAGI in {y - 1}")

    def store(self) -> None:
        a = self.obj
        y = self.plan.start_year
        a.inflation = None if self.inflation.value() <= -1.0 else self.inflation.value() / 100
        a.ss_cola = None if self.cola.value() <= -1.0 else self.cola.value() / 100
        a.return_rate = self.ret.value() / 100
        a.cash_return = self.cash.value() / 100
        a.spending = self.spending.value()
        a.end_age = self.end_age.value()
        for year, box in ((y - 2, self.magi1), (y - 1, self.magi2)):
            if box.value():
                a.prior_magi[str(year)] = box.value()
            else:
                a.prior_magi.pop(str(year), None)


# -- the list ------------------------------------------------------------------------


class BarDelegate(QStyledItemDelegate):
    """Paints the years an item covers as a bar on the plan's year axis."""

    def __init__(self, editor: "PlanEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(150, super().sizeHint(option, index).height())

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(p, option, index)
        bar = index.data(BAR_ROLE)
        plan = self.editor.plan
        if plan is None:
            return
        r = option.rect.adjusted(4, 0, -6, 0)
        y0, y1 = plan.start_year, plan.end_year
        span = max(1, y1 - y0 + 1)

        def x(year: float) -> float:
            return r.left() + (year - y0) / span * r.width()

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        guide = QColor(option.palette.mid().color())
        guide.setAlpha(60)
        p.setPen(QPen(guide, 1))
        for yr in range(y0 - y0 % 10 + 10, y1 + 1, 10):
            p.drawLine(int(x(yr)), r.top(), int(x(yr)), r.bottom())
        if bar is not None:
            first, last, color, milestone = bar
            c = QColor(color)
            cy = r.center().y()
            if milestone:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                cx = int(x(first + 0.5))
                p.drawPolygon(QPolygon([QPoint(cx, cy - 5), QPoint(cx + 5, cy), QPoint(cx, cy + 5),
                                        QPoint(cx - 5, cy)]))
            else:
                first = max(first, y0)
                last = min(last, y1)
                if last >= first:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(c)
                    left, right = x(first), x(last + 1)
                    p.drawRoundedRect(QRect(int(left), cy - 4, max(4, int(right - left)), 9), 3, 3)
        p.restore()


class PlanEditor(QWidget):
    """The Plan items tab: the list, its buttons and the selected item's form."""

    changed = Signal()  # the plan was edited (recalculate)
    structure_changed = Signal()  # items were added or removed

    def __init__(self) -> None:
        super().__init__()
        self.plan: Plan | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        row = QHBoxLayout()
        self.add_btn = QToolButton()
        self.add_btn.setText("&Add item")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.add_menu = QMenu(self)
        self.add_actions: dict[str, object] = {}
        for key, text in (("account", "Account"), ("income", "Income stream"),
                          ("one_time", "One-time income (sale, gift, bonus)"), (None, None),
                          ("roth_conversion", "Roth conversion"), ("withdrawal", "Withdrawal")):
            if key is None:
                self.add_menu.addSeparator()
                continue
            self.add_actions[key] = self.add_menu.addAction(text, lambda k=key: self.add_item(k))
        self.add_btn.setMenu(self.add_menu)
        self.dup_btn = QPushButton("D&uplicate")
        self.del_btn = QPushButton("&Delete")
        self.dup_btn.clicked.connect(lambda: self.duplicate_item())
        self.del_btn.clicked.connect(lambda: self.delete_item())
        row.addWidget(self.add_btn)
        row.addWidget(self.dup_btn)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Item", "Years"])
        self.tree.setAccessibleName("Plan items")
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setItemDelegateForColumn(1, BarDelegate(self))
        head = self.tree.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        head.resizeSection(1, 140)
        self.tree.setMinimumHeight(260)
        self.tree.setMinimumWidth(400)
        self.tree.currentItemChanged.connect(self._selected)
        self.tree.itemChanged.connect(self._item_changed)
        lay.addWidget(self.tree, 1)

        self.form_title = QLabel()
        lay.addWidget(self.form_title)
        self.forms: dict[str, ItemForm] = {
            "accounts": AccountForm(),
            "incomes": IncomeForm(),
            "people": PersonForm(),
            "rules": RuleForm(),
            "assumptions": AssumptionsForm(),
        }
        self.stack = QStackedWidget()
        self.hint = QLabel("Select an item to change it. Untick an account, income or strategy to see the plan "
                           "without it.")
        self.hint.setWordWrap(True)
        self.stack.addWidget(self.hint)
        for form in self.forms.values():
            self.stack.addWidget(form)
            form.changed.connect(self._form_changed)
        lay.addWidget(self.stack, 0)
        self._update_buttons()

    # -- building the list -------------------------------------------------
    def set_plan(self, plan: Plan | None) -> None:
        self.plan = plan
        self.rebuild()

    def rebuild(self, select=None) -> None:
        """Fill the list from the plan, keeping (or moving to) a selection."""
        current = select if select is not None else self.current_object()
        self.tree.blockSignals(True)
        self.tree.clear()
        self.groups: dict[str, QTreeWidgetItem] = {}
        found = None
        if self.plan is not None:
            for key, title in GROUPS:
                g = QTreeWidgetItem(self.tree, [title])
                g.setFlags(Qt.ItemFlag.ItemIsEnabled)
                f = g.font(0)
                f.setBold(True)
                g.setFont(0, f)
                g.setFirstColumnSpanned(False)
                self.groups[key] = g
                for obj in self._objects(key):
                    item = QTreeWidgetItem(g)
                    item.setData(0, ROLE, (key, obj))
                    self._describe(item)
                    if obj is current:
                        found = item
                g.setExpanded(True)
        self.tree.blockSignals(False)
        if found is not None:
            self.tree.setCurrentItem(found)
        else:
            self._selected(None, None)
        self._update_buttons()

    def _objects(self, key: str) -> list:
        assert self.plan is not None
        if key == "assumptions":
            return [self.plan.assumptions]
        return list(getattr(self.plan, key))

    def refresh(self) -> None:
        """Re-describe every row (names or the baseline may have changed)."""
        self.tree.blockSignals(True)
        for g in getattr(self, "groups", {}).values():
            for i in range(g.childCount()):
                self._describe(g.child(i))
        self.tree.blockSignals(False)
        self.tree.viewport().update()

    def _describe(self, item: QTreeWidgetItem) -> None:
        key, obj = item.data(0, ROLE)
        plan = self.plan
        assert plan is not None
        profile = plan.profile
        y0, y1 = plan.start_year, plan.end_year
        color = GROUP_COLORS[key]
        bar = None
        tip = ""
        if key == "accounts":
            text = f"{obj.label(profile)}  {short_money(obj.balance)}"
            last = obj.deadline_year or y1
            if obj.kind == "inherited_ira":
                color = "#008300"
                tip = f"Must be empty by {obj.deadline_year}" if obj.deadline_year else ""
            bar = (y0, last, color, 0)
        elif key == "incomes":
            text = f"{obj.label(profile)}  {short_money(obj.amount)}"
            first = obj.start.resolve(profile) if obj.start else y0
            last = obj.end.resolve(profile) if obj.end else y1
            bar = (first, last, INCOME_COLORS.get(obj.kind, color), 0)
            tip = f"{first}–{last}" if first != last else str(first)
        elif key == "people":
            i = plan.people.index(obj)
            p = profile.people[i]
            ss = obj.social_security
            if ss.current_benefit is not None:
                claim_text = f"Social Security {short_money(ss.current_benefit)} now"
                first = y0
            else:
                claim_text = f"claims at {ss.claim_age:g}"
                first = p.birth_year + int(ss.claim_age)
            death = obj.death_age
            text = f"{p.name}: {claim_text}" + (f", to age {death}" if death else "")
            last = plan.death_year(i) or y1
            bar = (first, last, color, 0)
            tip = f"Social Security {first}–{last}"
        elif key == "rules":
            acct = plan.account(obj.account)
            name = acct.label(profile) if acct else obj.account
            how = {"fixed": short_money(obj.amount), "fill": f"fill to {target_label(obj.target)}",
                   "spread": "spread evenly (1/N)", "all": "whole balance"}.get(obj.mode, obj.mode)
            verb = "Convert" if obj.kind == "roth_conversion" else "Withdraw from"
            text = f"{verb} {name}: {how}"
            first, last = obj.years(profile)
            bar = (first, last, color if obj.kind == "roth_conversion" else "#6250d6", 0)
            tip = obj.describe(profile, {a.id: a for a in plan.accounts}) + (f"\n{obj.note}" if obj.note else "")
        else:
            a = obj
            infl = plan.inflation
            text = (f"Inflation {100 * infl:.1f}%, return {100 * a.return_rate:.1f}%, "
                    f"spending {short_money(a.spending)}")
            tip = text
        item.setText(0, text)
        item.setToolTip(0, tip or text)
        item.setData(1, BAR_ROLE, bar)
        if bar is not None:
            item.setToolTip(1, tip or f"{bar[0]}–{bar[1]}")
            item.setData(1, Qt.ItemDataRole.AccessibleTextRole, tip or f"{bar[0]} to {bar[1]}")
        if hasattr(obj, "enabled"):
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if obj.enabled else Qt.CheckState.Unchecked)
            if not obj.enabled:
                item.setForeground(0, self.palette().placeholderText())
                item.setData(1, BAR_ROLE, (bar[0], bar[1], "#c9c7c0", 0) if bar else None)
            else:
                item.setData(0, Qt.ItemDataRole.ForegroundRole, None)

    # -- selection and editing ----------------------------------------------
    def current_object(self):
        item = self.tree.currentItem()
        data = item.data(0, ROLE) if item is not None else None
        return data[1] if data else None

    def current_key(self) -> str | None:
        item = self.tree.currentItem()
        data = item.data(0, ROLE) if item is not None else None
        return data[0] if data else None

    def _selected(self, current, _previous) -> None:
        data = current.data(0, ROLE) if current is not None else None
        if not data or self.plan is None:
            self.stack.setCurrentWidget(self.hint)
            self.form_title.setText("")
        else:
            key, obj = data
            form = self.forms[key]
            form.load(self.plan, obj)
            self.stack.setCurrentWidget(form)
            title = dict(GROUPS)[key]
            if key == "people":
                title = self.plan.profile.people[self.plan.people.index(obj)].name
            elif key == "rules":
                title = RULE_KINDS.get(obj.kind, obj.kind)
            elif key == "accounts":
                title = "Account"
            elif key == "incomes":
                title = "Income"
            self.form_title.setText(f"<b>{title}</b>")
        self._update_buttons()

    def _form_changed(self) -> None:
        item = self.tree.currentItem()
        if item is not None and item.data(0, ROLE):
            self.tree.blockSignals(True)
            self._describe(item)
            self.tree.blockSignals(False)
        if self.current_key() == "accounts":
            self.refresh()  # rules show account names
        self.changed.emit()

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        data = item.data(0, ROLE)
        if column != 0 or not data:
            return
        obj = data[1]
        if hasattr(obj, "enabled"):
            enabled = item.checkState(0) == Qt.CheckState.Checked
            if enabled != obj.enabled:
                obj.enabled = enabled
                self.tree.blockSignals(True)
                self._describe(item)
                self.tree.blockSignals(False)
                self.changed.emit()

    def _update_buttons(self) -> None:
        key = self.current_key()
        has_plan = self.plan is not None
        self.add_btn.setEnabled(has_plan)
        removable = key in ("accounts", "incomes", "rules")
        self.dup_btn.setEnabled(removable)
        self.del_btn.setEnabled(removable)

    # -- adding and removing ------------------------------------------------
    def add_item(self, what: str) -> None:
        plan = self.plan
        if plan is None:
            return
        y0 = plan.start_year
        if what == "account":
            obj = Account(id=plan.new_account_id("traditional_ira", 0), kind="traditional_ira", owner=0)
            plan.accounts.append(obj)
        elif what in ("income", "one_time"):
            if what == "income":
                obj = IncomeStream(kind="pension", amount=0.0, owner=0)
            else:
                obj = IncomeStream(kind="ltcg", amount=0.0, owner=None, name="One-time income",
                                   start=When(year=y0 + 1), end=When(year=y0 + 1))
            plan.incomes.append(obj)
        else:
            sources = [a for a in plan.accounts
                       if what != "roth_conversion" or a.kind in TRADITIONAL_KINDS or a.kind == "inherited_ira"]
            if not sources:
                need = "a traditional IRA or 401(k)" if what == "roth_conversion" else "an account"
                QMessageBox.information(self, "Add strategy", f"Add {need} first.")
                return
            if what == "roth_conversion":
                obj = Rule(kind="roth_conversion", account=sources[0].id, start=When(year=y0),
                           end=When(year=y0 + 4), mode="fill", target="bracket:0.12")
            else:
                obj = Rule(kind="withdrawal", account=sources[0].id, start=When(year=y0), mode="fixed")
            plan.rules.append(obj)
        self.rebuild(select=obj)
        self.structure_changed.emit()
        self.changed.emit()

    def duplicate_item(self) -> None:
        key, obj = self.current_key(), self.current_object()
        if self.plan is None or key not in ("accounts", "incomes", "rules"):
            return
        new = copy.deepcopy(obj)
        items = getattr(self.plan, key)
        if key == "accounts":
            new.id = self.plan.new_account_id(new.kind, new.owner)
            if new.name:
                new.name += " (copy)"
        items.insert(items.index(obj) + 1, new)
        self.rebuild(select=new)
        self.structure_changed.emit()
        self.changed.emit()

    def delete_item(self, *, confirm: Callable[[str], bool] | None = None) -> None:
        key, obj = self.current_key(), self.current_object()
        if self.plan is None or key not in ("accounts", "incomes", "rules"):
            return
        items = getattr(self.plan, key)
        idx = items.index(obj)
        if key == "accounts":
            users = [r for r in self.plan.rules if r.account == obj.id or r.to_account == obj.id]
            if users:
                ask = confirm or self._confirm
                if not ask(f"{len(users)} strateg{'y uses' if len(users) == 1 else 'ies use'} this account "
                           f"and will be deleted with it."):
                    return
                self.plan.rules = [r for r in self.plan.rules if r not in users]
        items.pop(idx)
        neighbours = getattr(self.plan, key)
        self.rebuild(select=neighbours[min(idx, len(neighbours) - 1)] if neighbours else None)
        self.structure_changed.emit()
        self.changed.emit()

    def _confirm(self, text: str) -> bool:
        answer = QMessageBox.question(self, "Delete account", text + " Delete them?")
        return answer == QMessageBox.StandardButton.Yes
