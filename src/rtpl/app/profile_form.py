"""Profile editor: the baseline financial starting point.

Built from standard Qt form widgets with real labels (``setBuddy``) and
accessible names, so screen readers and keyboard users get the same form
as everyone else.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rtpl.model.profile import FILING_STATUS_LABELS, Household, Person, Profile

PERSON_MONEY = [
    ("social_security", "Social Security"),
    ("wages", "Wages"),
    ("pension", "Pension"),
    ("ira_distributions", "IRA / 401(k) withdrawals"),
]

HOUSEHOLD_MONEY = [
    ("taxable_interest", "Taxable interest"),
    ("tax_exempt_interest", "Tax-exempt interest"),
    ("qualified_dividends", "Qualified dividends"),
    ("nonqualified_dividends", "Non-qualified dividends"),
    ("long_term_capital_gains", "Net long-term capital gains"),
    ("short_term_capital_gains", "Short-term capital gains"),
    ("other_ordinary_income", "Other ordinary income"),
    ("other_investment_income", "Rental / passive income"),
    ("adjustments", "Above-the-line adjustments"),
    ("itemized_deductions", "Itemized deductions"),
]

MONEY_TIPS = {
    "social_security": "Yearly Social Security benefit, before Medicare premiums.",
    "itemized_deductions": "Leave at $0 to use the standard deduction.",
}

# Widest a number field needs to be: "$10,000,000" plus the arrows.
FIELD_WIDTH = 124

MEDICARE_CHOICES = [("Automatic (age 65+)", None), ("Yes", True), ("No", False)]


def money_box(allow_negative: bool = False) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setPrefix("$")
    box.setDecimals(0)
    box.setRange(-10_000_000 if allow_negative else 0, 10_000_000)
    box.setSingleStep(1000)
    box.setGroupSeparatorShown(True)
    box.setAccelerated(True)
    box.setMaximumWidth(FIELD_WIDTH)
    return box


def compact_form(form: QFormLayout) -> QFormLayout:
    """Labels left, fields only as wide as they need, long labels wrap above."""
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setHorizontalSpacing(8)
    return form


def _add_row(form: QFormLayout, text: str, widget: QWidget, tip: str | None = None) -> None:
    label = QLabel(text)
    label.setBuddy(widget)
    widget.setAccessibleName(text.replace("&", ""))
    if tip:
        widget.setToolTip(tip)
        widget.setAccessibleDescription(tip)
    form.addRow(label, widget)


class PersonBox(QGroupBox):
    changed = Signal()

    def __init__(self, title: str) -> None:
        super().__init__(title)
        form = compact_form(QFormLayout(self))
        self.name = QLineEdit()
        self.name.setMinimumWidth(FIELD_WIDTH)
        self.birth_year = QSpinBox()
        self.birth_year.setRange(1900, 2100)
        self.birth_year.setMaximumWidth(80)
        self.age = QLabel()
        self.age.setAccessibleName("Calculated age")
        self._tax_year = 2026
        self.medicare = QComboBox()
        for text, _ in MEDICARE_CHOICES:
            self.medicare.addItem(text)
        self.blind = QCheckBox("Legally blind")
        _add_row(form, "Name", self.name)
        birth_label = QLabel("Birth year")
        birth_label.setBuddy(self.birth_year)
        self.birth_year.setAccessibleName("Birth year")
        form.addRow(birth_label, self.birth_year)
        form.addRow("", self.age)
        _add_row(
            form,
            "Medicare Part B",
            self.medicare,
            "Choose Yes for Medicare before 65 (for example on SSDI), so IRMAA applies.",
        )
        form.addRow("", self.blind)
        self.money: dict[str, QDoubleSpinBox] = {}
        for key, text in PERSON_MONEY:
            box = money_box()
            self.money[key] = box
            _add_row(form, text, box, MONEY_TIPS.get(key))
            box.valueChanged.connect(self.changed)
        self.name.textChanged.connect(self.changed)
        self.birth_year.valueChanged.connect(self.changed)
        self.birth_year.valueChanged.connect(self._update_age)
        self.medicare.currentIndexChanged.connect(self.changed)
        self.blind.toggled.connect(self.changed)

    def set_tax_year(self, year: int) -> None:
        self._tax_year = year
        self._update_age()

    def _update_age(self, *_args) -> None:
        age = self._tax_year - self.birth_year.value()
        self.age.setText(f"Age {age} at end of {self._tax_year}")

    def set_person(self, p: Person) -> None:
        self.name.setText(p.name)
        self.birth_year.setValue(p.birth_year)
        self.medicare.setCurrentIndex([v for _, v in MEDICARE_CHOICES].index(p.medicare_part_b))
        self.blind.setChecked(p.blind)
        for key, box in self.money.items():
            box.setValue(getattr(p, key))

    def person(self) -> Person:
        return Person(
            name=self.name.text() or self.title(),
            birth_year=self.birth_year.value(),
            medicare_part_b=MEDICARE_CHOICES[self.medicare.currentIndex()][1],
            blind=self.blind.isChecked(),
            **{k: b.value() for k, b in self.money.items()},
        )


class ProfileForm(QWidget):
    """Edits a :class:`Profile`; emits ``changed`` on every edit."""

    changed = Signal()

    def __init__(self, states: list[str]) -> None:
        super().__init__()
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        basics = QGroupBox("Baseline")
        form = compact_form(QFormLayout(basics))
        self.plan_name = QLineEdit()
        self.plan_name.setMinimumWidth(FIELD_WIDTH)
        self.year = QSpinBox()
        self.year.setRange(2025, 2100)
        self.year.setMaximumWidth(80)
        self.filing = QComboBox()
        for key, text in FILING_STATUS_LABELS.items():
            self.filing.addItem(text, key)
        self.state = QComboBox()
        self.state.addItem("(none)", None)
        for code in states:
            self.state.addItem(code, code)
        _add_row(form, "Plan name", self.plan_name)
        _add_row(form, "Tax year", self.year)
        _add_row(form, "Filing status", self.filing)
        _add_row(form, "State of residence", self.state, "Optional. Adds a state tax layer.")
        self.inflation = QDoubleSpinBox()
        self.inflation.setSuffix("%")
        self.inflation.setDecimals(1)
        self.inflation.setRange(0.0, 15.0)
        self.inflation.setSingleStep(0.1)
        self.inflation.setMaximumWidth(80)
        _add_row(
            form,
            "Inflation rate",
            self.inflation,
            "Yearly inflation used to project tax law for years with no data yet, "
            "such as the IRMAA brackets two years ahead.",
        )
        outer.addWidget(basics)

        self.person_boxes = [PersonBox("Person 1"), PersonBox("Person 2 (spouse)")]
        for box in self.person_boxes:
            outer.addWidget(box)
            box.changed.connect(self._emit)

        hh = QGroupBox("Household income and settings")
        hform = compact_form(QFormLayout(hh))
        self.household_money: dict[str, QDoubleSpinBox] = {}
        for key, text in HOUSEHOLD_MONEY:
            box = money_box(allow_negative=key == "long_term_capital_gains")
            self.household_money[key] = box
            _add_row(hform, text, box, MONEY_TIPS.get(key))
            box.valueChanged.connect(self._emit)
        self.brokerage = QCheckBox("Taxable brokerage account")
        self.brokerage.setToolTip("The household has a taxable brokerage account.")
        self.aca = QCheckBox("ACA marketplace coverage")
        self.aca.setToolTip("The household buys health coverage on an ACA marketplace.")
        self.household_size = QSpinBox()
        self.household_size.setRange(0, 20)
        self.household_size.setSpecialValueText("Automatic")
        self.household_size.setMaximumWidth(FIELD_WIDTH)
        hform.addRow("", self.brokerage)
        hform.addRow("", self.aca)
        _add_row(hform, "Household size (for FPL)", self.household_size)
        outer.addWidget(hh)
        outer.addStretch(1)

        self.plan_name.textChanged.connect(self._emit)
        self.year.valueChanged.connect(self._year_changed)
        self.filing.currentIndexChanged.connect(self._filing_changed)
        self.state.currentIndexChanged.connect(self._emit)
        self.inflation.valueChanged.connect(self._emit)
        self.brokerage.toggled.connect(self._emit)
        self.aca.toggled.connect(self._emit)
        self.household_size.valueChanged.connect(self._emit)

    def _emit(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def _year_changed(self, year: int) -> None:
        for box in self.person_boxes:
            box.set_tax_year(year)
        self._emit()

    def _filing_changed(self, *_args) -> None:
        married = self.filing.currentData() in ("mfj", "mfs")
        self.person_boxes[1].setVisible(married)
        self._emit()

    def set_profile(self, profile: Profile) -> None:
        self._loading = True
        try:
            self.plan_name.setText(profile.name)
            self.year.setValue(profile.year)
            self.filing.setCurrentIndex(max(0, self.filing.findData(profile.filing_status)))
            self.state.setCurrentIndex(max(0, self.state.findData(profile.state)))
            self.inflation.setValue(profile.inflation * 100)
            people = list(profile.people) + [Person(name="Person 2")] * (2 - len(profile.people))
            for box, person in zip(self.person_boxes, people):
                box.set_person(person)
                box.set_tax_year(profile.year)
            for key, box in self.household_money.items():
                box.setValue(getattr(profile.household, key))
            self.brokerage.setChecked(profile.household.has_taxable_brokerage)
            self.aca.setChecked(profile.household.aca_marketplace)
            self.household_size.setValue(profile.household.household_size or 0)
            self.person_boxes[1].setVisible(profile.filing_status in ("mfj", "mfs"))
        finally:
            self._loading = False
        self.changed.emit()

    def profile(self) -> Profile:
        fs = self.filing.currentData()
        n = 2 if fs in ("mfj", "mfs") else 1
        household = Household(
            **{k: b.value() for k, b in self.household_money.items()},
            has_taxable_brokerage=self.brokerage.isChecked(),
            aca_marketplace=self.aca.isChecked(),
            household_size=self.household_size.value() or None,
        )
        return Profile(
            name=self.plan_name.text() or "My plan",
            year=self.year.value(),
            filing_status=fs,
            state=self.state.currentData(),
            people=[b.person() for b in self.person_boxes[:n]],
            household=household,
            inflation=round(self.inflation.value() / 100, 4),
        )
