"""Forward plan page: income-by-source graph, balances graph and a year table.

The plan's accounts, incomes and rules come from a plan file for now; the
baseline (people, filing status, state) is the profile on the left, so
edits there flow straight into the plan.
"""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rtpl.charts.forward_chart import line_short_label, render_balances, render_income
from rtpl.engine.forward import PlanResult, default_line_visibility, run_plan, year_table
from rtpl.model.plan import Plan
from rtpl.model.profile import Profile
from rtpl.taxlaw.loader import TaxLawLibrary
from rtpl.theme import Palette


class PlanView(QWidget):
    status = Signal(str)

    def __init__(self, library: TaxLawLibrary) -> None:
        super().__init__()
        self.library = library
        self.plan: Plan | None = None
        self.result: PlanResult | None = None
        self.palette: Palette | None = None
        self.overrides: dict[str, bool] = {}
        self.line_checks: dict[str, QCheckBox] = {}

        lay = QVBoxLayout(self)
        self.summary = QLabel("No plan loaded. Use File > Open Plan, or File > New Plan from Profile.")
        self.summary.setWordWrap(True)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.summary)

        # The plan's graph options live in the Graph options side panel (the
        # main window shows them while this page is active).
        self.options_widget = QWidget()
        olay = QVBoxLayout(self.options_widget)
        olay.setContentsMargins(6, 6, 6, 6)
        view = QGroupBox("View")
        vlay = QVBoxLayout(view)
        self.detail = QCheckBox("Show each &source separately")
        self.detail.setToolTip("Split the bars by person, account and income stream instead of by type")
        self.todays = QCheckBox("In &today's dollars")
        self.todays.setToolTip("Divide each year's amounts by inflation since the plan's first year")
        self.tax_free = QCheckBox("Show ta&x-free withdrawals")
        self.tax_free.setChecked(True)
        self.tax_free.setToolTip("Roth, HSA and brokerage principal: money spent but not taxed")
        for cb in (self.detail, self.todays, self.tax_free):
            cb.toggled.connect(self.redraw)
            vlay.addWidget(cb)
        olay.addWidget(view)
        self.line_groups: dict[str, QVBoxLayout] = {}
        for key, title in (("bracket", "Bracket tops"), ("irmaa", "IRMAA tiers (premiums 2 years later)"),
                           ("other", "Other lines")):
            group = QGroupBox(title)
            self.line_groups[key] = QVBoxLayout(group)
            olay.addWidget(group)
        reset = QPushButton("Reset &lines to suggested")
        reset.setToolTip("Go back to the lines suggested by the plan")
        reset.clicked.connect(self._reset)
        olay.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
        olay.addStretch(1)

        self.tabs = QTabWidget()
        self.income_fig = Figure(figsize=(9, 6), dpi=100)
        self.income_canvas = FigureCanvasQTAgg(self.income_fig)
        self.income_canvas.setAccessibleName("Income by source and tax thresholds chart")
        self.income_canvas.setAccessibleDescription("The Year table tab lists the same information as text.")
        self.tabs.addTab(self.income_canvas, "&Income and brackets")
        self.bal_fig = Figure(figsize=(9, 6), dpi=100)
        self.bal_canvas = FigureCanvasQTAgg(self.bal_fig)
        self.bal_canvas.setAccessibleName("Account balances chart")
        self.tabs.addTab(self.bal_canvas, "&Balances")
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName("Year by year plan table")
        self.tabs.addTab(self.table, "&Year table")
        lay.addWidget(self.tabs, 1)

    # -- data ------------------------------------------------------------
    def set_plan(self, plan: Plan | None) -> None:
        self.plan = plan
        self.overrides.clear()
        if plan is None:
            self.result = None
            self.summary.setText("No plan loaded. Use File > Open Plan, or File > New Plan from Profile.")
            self.table.setRowCount(0)
            self._paint_empty()

    def _paint_empty(self) -> None:
        """Blank charts in the theme's background, so dark mode has no white page."""
        for fig, canvas in ((self.income_fig, self.income_canvas), (self.bal_fig, self.bal_canvas)):
            fig.clear()
            if self.palette is not None:
                fig.set_facecolor(self.palette.surface)
            canvas.draw_idle()

    def recompute(self, profile: Profile | None = None) -> None:
        if self.plan is None:
            return
        if profile is not None:
            self.plan.profile = profile
            self.plan.__post_init__()
        problems = self.plan.validate()
        if problems:
            self.status.emit("Plan: " + "; ".join(problems))
            return
        try:
            self.result = run_plan(self.plan, self.library)
        except (KeyError, ValueError, IndexError) as exc:
            self.status.emit(f"Plan cannot be computed: {exc}")
            return
        self._rebuild_line_checks()
        self.redraw()
        r = self.result
        if r.years:
            first, last = r.years[0], r.years[-1]
            short = [y.year for y in r.years if y.shortfall > 0]
            text = (
                f"<b>{self.plan.profile.name}</b>: {first.year}–{last.year}, "
                f"{len(self.plan.accounts)} accounts, {len(self.plan.incomes)} income streams, "
                f"{len(self.plan.rules)} rules. Lifetime federal + state tax "
                f"${sum(y.tax.total_tax for y in r.years):,.0f}; final balances "
                f"${sum(last.balances_end.values()):,.0f}."
            )
            if short:
                text += f" Spending not covered in {short[0]}–{short[-1]}."
            self.summary.setText(text)

    def _rebuild_line_checks(self) -> None:
        assert self.result is not None
        keys = list(self.result.line_labels)
        if list(self.line_checks) == keys:
            return
        for lay in self.line_groups.values():
            while lay.count():
                w = lay.takeAt(0).widget()
                if w:
                    w.deleteLater()
        self.line_checks.clear()
        for key in keys:
            label = self.result.line_labels[key]
            if key.startswith("bracket:"):
                group, text = "bracket", f"Top of {line_short_label(key)}"
            elif key.startswith("irmaa:"):
                group, text = "irmaa", f"Tier {key.split(':')[1]}"
            else:
                group, text = "other", label
            cb = QCheckBox(text)
            cb.setToolTip(label)
            cb.setAccessibleName(label)
            cb.clicked.connect(lambda checked, k=key: self._toggled(k, checked))
            self.line_checks[key] = cb
            self.line_groups[group].addWidget(cb)

    def visibility(self) -> dict[str, bool]:
        assert self.result is not None
        vis = default_line_visibility(self.result)
        vis.update(self.overrides)
        vis["tax_free"] = self.tax_free.isChecked()
        return vis

    def _toggled(self, key: str, checked: bool) -> None:
        self.overrides[key] = checked
        self.redraw()

    def _reset(self) -> None:
        self.overrides.clear()
        self.redraw()

    # -- drawing ---------------------------------------------------------
    def set_palette(self, palette: Palette) -> None:
        self.palette = palette
        if self.result is None:
            self._paint_empty()
        self.redraw()

    def redraw(self, *_args) -> None:
        if self.result is None or self.palette is None:
            return
        vis = self.visibility()
        for key, cb in self.line_checks.items():
            cb.blockSignals(True)
            cb.setChecked(vis.get(key, False))
            cb.blockSignals(False)
        kw = dict(detail=self.detail.isChecked(), todays_dollars=self.todays.isChecked())
        render_income(self.income_fig, self.result, vis, self.palette, **kw)
        self.income_canvas.draw_idle()
        render_balances(self.bal_fig, self.result, self.palette, **kw)
        self.bal_canvas.draw_idle()
        header, rows = year_table(self.result, todays_dollars=self.todays.isChecked())
        self.table.setColumnCount(len(header))
        self.table.setHorizontalHeaderLabels(header)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate(row):
                item = QTableWidgetItem(text)
                if 2 < c < len(row) - 1:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

    def current_figure(self) -> Figure:
        return self.bal_fig if self.tabs.currentIndex() == 1 else self.income_fig
