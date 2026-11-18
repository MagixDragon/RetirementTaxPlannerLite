"""Plan editor spike: static screen mockups over the real app.

Nothing here is wired up. It builds the real main window with the example
plan, adds mock widgets for the proposed editor (plan timeline, item
editor, scenario bar, live summary strip, year overrides, scenario
compare, setup wizard) and saves screenshots. Numbers in the summary strip
and compare view come from real plan runs.

    uv run python spikes/plan_editor/mockup.py OUT_DIR
"""

from __future__ import annotations

import copy
import os
import sys
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PySide6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from rtpl.app.main_window import MainWindow, apply_app_theme  # noqa: E402
from rtpl.engine.forward import CATEGORY_LABELS, run_plan  # noqa: E402
from rtpl.model.plan import Plan, Rule, When  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLAN_FILE = ROOT / "examples/plans/couple_conversions_inherited.json"

COLORS = {
    "Milestones": "#5f5d58",
    "Accounts": "#1c5cab",
    "Income": "#eb6834",
    "Social Security": "#2a78d6",
    "Strategies": "#e34948",
}
PIN = QColor("#fbe3a6")  # year override cells
CALC = QColor("#f1f0ec")  # calculated (read-only) cells


# -- plan numbers ---------------------------------------------------------------


@dataclass
class Totals:
    tax: float
    irmaa: float
    final: float
    traditional: float
    irmaa_years: int
    result: object


def totals(plan: Plan, library) -> Totals:
    r = run_plan(plan, library)
    last = r.years[-1]
    trad = sum(v for k, v in last.balances_end.items() if r.account_kinds.get(k) in ("traditional_ira", "401k", "inherited_ira"))
    return Totals(
        tax=sum(y.tax.total_tax for y in r.years),
        irmaa=sum(y.irmaa_surcharge for y in r.years),
        final=sum(last.balances_end.values()),
        traditional=trad,
        irmaa_years=sum(1 for y in r.years if y.irmaa_surcharge > 0),
        result=r,
    )


def money(v: float) -> str:
    if abs(v) >= 1e6:
        return f"${v / 1e6:,.2f}M"
    return f"${v:,.0f}"


def delta(v: float, good_when_negative: bool) -> tuple[str, str]:
    if abs(v) < 1:
        return "no change", "#6b6a66"
    sign = "+" if v > 0 else "−"
    good = (v < 0) == good_when_negative
    return f"{sign}{money(abs(v))}", ("#16794c" if good else "#b42318")


# -- mock widgets ---------------------------------------------------------------


class SummaryStrip(QFrame):
    """Key results with the change since the last edit (or against a scenario)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(10, 6, 10, 6)
        self.lay.setSpacing(28)

    def set_tiles(self, tiles: list[tuple[str, str, str, str]], caption: str) -> None:
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        for title, value, change, color in tiles:
            box = QVBoxLayout()
            box.setSpacing(0)
            t = QLabel(title)
            t.setStyleSheet("color:#6b6a66;")
            v = QLabel(f"<span style='font-size:15pt;font-weight:600'>{value}</span>"
                       f"&nbsp;&nbsp;<span style='color:{color};font-weight:600'>{change}</span>")
            box.addWidget(t)
            box.addWidget(v)
            w = QWidget()
            w.setLayout(box)
            self.lay.addWidget(w)
        self.lay.addStretch(1)
        cap = QLabel(caption)
        cap.setStyleSheet("color:#6b6a66;")
        cap.setWordWrap(True)
        cap.setMaximumWidth(260)
        self.lay.addWidget(cap)


class ScenarioBar(QWidget):
    def __init__(self, current: str, compare: str | None) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Scenario</b>"))
        combo = QComboBox()
        combo.addItems(["Base plan", "Fill to 24% until 70", "Sam claims SS at 67"])
        combo.setCurrentText(current)
        combo.setMinimumWidth(190)
        lay.addWidget(combo)
        for text in ("Duplicate", "Rename", "Delete"):
            lay.addWidget(QPushButton(text))
        lay.addSpacing(18)
        cb = QCheckBox("Compare with")
        cb.setChecked(compare is not None)
        lay.addWidget(cb)
        other = QComboBox()
        other.addItems(["Base plan", "Fill to 24% until 70", "Sam claims SS at 67"])
        other.setCurrentText(compare or "Base plan")
        other.setEnabled(compare is not None)
        other.setMinimumWidth(190)
        lay.addWidget(other)
        lay.addStretch(1)
        undo = QPushButton("Undo last change")
        lay.addWidget(undo)


@dataclass
class Item:
    group: str
    label: str
    when: str
    first: int | None = None
    last: int | None = None
    milestone: bool = False
    enabled: bool = True
    pins: tuple[int, ...] = ()
    color: str | None = None


class Timeline(QWidget):
    """Plan items grouped by kind, each with a bar across the years it covers."""

    ROW = 24
    HEAD = 38
    GROUP = 26

    def __init__(self, items: list[Item], years: tuple[int, int], ages: dict[int, str],
                 collapsed: set[str] = frozenset()) -> None:
        super().__init__()
        self.collapsed = set(collapsed)
        self.counts = {}
        for i in items:
            self.counts[i.group] = self.counts.get(i.group, 0) + 1
        items = [i for i in items if i.group not in self.collapsed]
        self.items = items
        self.y0, self.y1 = years
        self.ages = ages
        self.selected: str | None = None
        self.hover_add: str | None = None
        groups = list(self.counts)
        self.groups = groups
        self.setMinimumHeight(self.HEAD + len(groups) * self.GROUP + len(items) * self.ROW + 6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(460, self.minimumHeight())

    def _x(self, year: float, left: int, width: int) -> float:
        return left + (year - self.y0) / (self.y1 - self.y0 + 1) * width

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        text = pal.windowText().color()
        muted = QColor("#7c7a75")
        w = self.width()
        bar_w = int(w * 0.44)
        bar_l = w - bar_w - 8
        f = QFont(self.font())
        small = QFont(f)
        small.setPointSizeF(f.pointSizeF() * 0.85)

        # Year axis
        p.setFont(small)
        p.setPen(muted)
        for yr in range(self.y0, self.y1 + 1, 10):
            x = self._x(yr, bar_l, bar_w)
            p.drawText(QRect(int(x) - 30, 2, 60, 16), Qt.AlignmentFlag.AlignCenter, str(yr))
            p.drawText(QRect(int(x) - 30, 17, 60, 16), Qt.AlignmentFlag.AlignCenter, self.ages.get(yr, ""))
        p.setPen(QPen(QColor("#d6d4ce"), 1))
        p.drawLine(0, self.HEAD - 2, w, self.HEAD - 2)
        p.setPen(muted)
        p.drawText(QRect(6, 2, bar_l - 10, 32), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Item")

        y = self.HEAD
        for group in self.groups:
            bf = QFont(f)
            bf.setBold(True)
            p.setFont(bf)
            p.setPen(text)
            closed = group in self.collapsed
            head = ("\u25b8 " if closed else "\u25be ") + group + (f"  ({self.counts[group]})" if closed else "")
            p.drawText(QRect(6, y, w - 40, self.GROUP), Qt.AlignmentFlag.AlignVCenter, head)
            p.setPen(QPen(QColor("#9a9893"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = QRect(w - 28, y + 4, 18, 18)
            p.drawRoundedRect(r, 3, 3)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "+")
            y += self.GROUP
            for it in [i for i in self.items if i.group == group]:
                row = QRect(0, y, w, self.ROW)
                # vertical year guides
                p.setPen(QPen(QColor("#eceae5"), 1))
                for yr in range(self.y0, self.y1 + 1, 5):
                    x = int(self._x(yr, bar_l, bar_w))
                    p.drawLine(x, y, x, y + self.ROW)
                if it.label == self.selected:
                    p.fillRect(row, QColor("#dbe8fb"))
                # enabled check box
                cb = QRect(10, y + 6, 12, 12)
                p.setPen(QPen(QColor("#8d8b86"), 1))
                p.setBrush(QColor("white"))
                p.drawRect(cb)
                if it.enabled:
                    p.setPen(QPen(QColor("#2a2a28"), 2))
                    p.drawLine(cb.left() + 2, cb.center().y(), cb.left() + 5, cb.bottom() - 2)
                    p.drawLine(cb.left() + 5, cb.bottom() - 2, cb.right() - 2, cb.top() + 2)
                p.setFont(f)
                p.setPen(text if it.enabled else muted)
                label = p.fontMetrics().elidedText(it.label, Qt.TextElideMode.ElideRight, bar_l - 40)
                p.drawText(QRect(30, y, bar_l - 36, self.ROW), Qt.AlignmentFlag.AlignVCenter, label)
                color = QColor(it.color or COLORS.get(it.group, "#888"))
                if not it.enabled:
                    color = QColor("#c9c7c0")
                if it.first is not None:
                    last = it.last if it.last is not None else self.y1
                    x0 = self._x(it.first, bar_l, bar_w)
                    x1 = self._x(last + 1, bar_l, bar_w)
                    cy = y + self.ROW // 2
                    if it.milestone:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(color)
                        cx = int(x0 + 2)
                        p.drawPolygon(QPolygon([QPoint(cx, cy - 6), QPoint(cx + 6, cy), QPoint(cx, cy + 6), QPoint(cx - 6, cy)]))
                        p.setFont(small)
                        p.setPen(muted)
                        if cx > bar_l + bar_w * 0.6:
                            p.drawText(QRect(cx - 9 - bar_w, y, bar_w, self.ROW),
                                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, it.when)
                        else:
                            p.drawText(QRect(cx + 9, y, bar_w, self.ROW), Qt.AlignmentFlag.AlignVCenter, it.when)
                    else:
                        p.setPen(Qt.PenStyle.NoPen)
                        c = QColor(color)
                        c.setAlpha(200)
                        p.setBrush(c)
                        p.drawRoundedRect(QRect(int(x0), cy - 5, max(4, int(x1 - x0)), 10), 3, 3)
                        if it.label == self.selected and it.last is not None:
                            # drag handles on the ends
                            p.setBrush(QColor("white"))
                            p.setPen(QPen(color.darker(130), 1.5))
                            for hx in (x0, x1):
                                p.drawRect(QRect(int(hx) - 3, cy - 8, 6, 16))
                        for py in it.pins:
                            px = int(self._x(py + 0.5, bar_l, bar_w))
                            p.setPen(QPen(QColor("#7a5600"), 1))
                            p.setBrush(QColor("#f5b400"))
                            p.drawEllipse(QPoint(px, cy), 4, 4)
                y += self.ROW
        p.end()


class PlanPanel(QWidget):
    """Left panel on the Forward plan page: plan items, editor, baseline."""

    def __init__(self, timeline: Timeline, editor: QWidget | None, overrides: int, baseline: QWidget) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        plan = QWidget()
        lay = QVBoxLayout(plan)
        lay.setContentsMargins(4, 6, 4, 4)
        row = QHBoxLayout()
        self.add_btn = QToolButton()
        self.add_btn.setText("Add item")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.add_btn.setArrowType(Qt.ArrowType.NoArrow)
        row.addWidget(self.add_btn)
        for text in ("Duplicate", "Delete"):
            b = QPushButton(text)
            b.setEnabled(editor is not None)
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(QPushButton("Setup wizard…"))
        lay.addLayout(row)
        lay.addWidget(timeline)
        if overrides:
            note = QLabel(f"<span style='color:#7a5600'>●</span> {overrides} year override{'s' if overrides > 1 else ''} from the Year table "
                          "&nbsp;<a href='#'>Show</a> · <a href='#'>Clear all</a>")
            lay.addWidget(note)
        if editor is not None:
            lay.addWidget(editor)
        else:
            hint = QLabel("Select an item to edit it. Untick an item to try the plan without it. "
                          "Drag the ends of a bar to change when it starts or stops.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#6b6a66;")
            lay.addWidget(hint)
        lay.addStretch(1)
        self.tabs.addTab(plan, "Plan items")
        self.tabs.addTab(baseline, "Baseline")
        self.tabs.addTab(QWidget(), "Assumptions")


def when_row(kind: str, value: str, result: str, changed: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    c = QComboBox()
    c.addItems(["Alex's age", "Sam's age", "Year", "Milestone", "Plan start", "Plan end"])
    c.setCurrentText(kind)
    lay.addWidget(c)
    if kind == "Milestone":
        m = QComboBox()
        m.addItems(["Sam retires", "Alex retires", "Sell rental property", "Alex's life expectancy"])
        m.setCurrentText(value)
        lay.addWidget(m, 1)
    elif kind not in ("Plan start", "Plan end"):
        s = QSpinBox()
        s.setRange(0, 2100)
        s.setValue(int(value))
        if changed:
            s.setStyleSheet("QSpinBox{background:#fff4cc;}")
        lay.addWidget(s)
    note = QLabel(result)
    note.setStyleSheet("color:#6b6a66;")
    lay.addWidget(note)
    lay.addStretch(1)
    return w


def combo(items: list[str], current: str | None = None) -> QComboBox:
    c = QComboBox()
    c.addItems(items)
    if current:
        c.setCurrentText(current)
    return c


def conversion_editor() -> QGroupBox:
    box = QGroupBox("Roth conversion")
    form = QFormLayout(box)
    form.addRow("Name", QLineEdit("Convert Alex IRA to Roth"))
    form.addRow("From", combo(["Alex Traditional IRA", "Sam 401(k)"]))
    form.addRow("Into", combo(["Alex Roth IRA"]))
    form.addRow("Starts", when_row("Alex's age", "63", "= 2027"))
    form.addRow("Ends", when_row("Alex's age", "72", "= 2036  (was 70)", changed=True))
    form.addRow("Amount", combo(["Fixed amount each year", "Fill up to a target", "Spread evenly (1/N)",
                                 "Whole balance"], "Fill up to a target"))
    form.addRow("Fill to", combo(["Top of 12% bracket", "Top of 22% bracket", "Top of 24% bracket",
                                  "Under IRMAA tier 1", "Top of 0% capital gains", "ACA 400% FPL", "A MAGI amount"],
                                 "Top of 22% bracket"))
    pins = QLabel("<span style='color:#7a5600'>●</span> 2031: $0 (year of the property sale) "
                  "&nbsp;<a href='#'>Edit</a> · <a href='#'>Clear</a>")
    form.addRow("Overrides", pins)
    form.addRow("Note", QLineEdit("Use the low-bracket years before Social Security"))
    return box


def milestone_editor() -> QGroupBox:
    box = QGroupBox("Milestone")
    form = QFormLayout(box)
    form.addRow("Name", QLineEdit("Sam retires"))
    form.addRow("When", when_row("Sam's age", "62", "= 2028  (was 60)", changed=True))
    used = QLabel("Sam wages (end)")
    form.addRow("Used by", used)
    tip = QLabel("Items tied to a milestone move with it. Changing Sam's retirement from 60 to 62 moved "
                 "Sam's wages with it; the graph and totals above already show the result.")
    tip.setWordWrap(True)
    tip.setStyleSheet("color:#6b6a66;")
    form.addRow(tip)
    return box


def plan_items(plan: Plan, pins: tuple[int, ...] = (2031,), conv_end: int = 2034, sam_retire: int = 2026,
               selected_disabled: bool = False) -> list[Item]:
    return [
        Item("Milestones", "Sam retires", f"{sam_retire} · Sam {sam_retire - 1966}", sam_retire, sam_retire, True),
        Item("Milestones", "Alex retires", "2026 · Alex 62", 2026, 2026, True),
        Item("Milestones", "Sell rental property", "2031", 2031, 2031, True),
        Item("Milestones", "Alex's life expectancy", "2052 · Alex 88", 2052, 2052, True),
        Item("Accounts", "Alex Traditional IRA  $1.10M", "", 2026, None),
        Item("Accounts", "Sam 401(k)  $350K", "", 2026, None),
        Item("Accounts", "Alex Inherited IRA  $400K", "", 2026, 2034, color="#008300"),
        Item("Accounts", "Alex Roth IRA  $90K", "", 2026, None),
        Item("Accounts", "Joint brokerage  $600K", "", 2026, None),
        Item("Accounts", "Savings  $60K", "", 2026, None),
        Item("Income", "Sam wages  $95K", "", 2026, sam_retire),
        Item("Income", "Alex pension  $18K", "", 2029, 2052, color="#1baf7a"),
        Item("Income", "Alex UK State Pension  $9K", "", 2031, 2052, color="#1baf7a"),
        Item("Income", "Rental property gain  $150K", "", 2031, 2031, color="#6da7ec"),
        Item("Social Security", "Alex: claim at 70", "", 2034, 2052),
        Item("Social Security", "Sam: claim at 62", "", 2028, 2060),
        Item("Strategies", "Convert Alex IRA to Roth", "", 2027, conv_end, pins=pins),
        Item("Strategies", "Spread inherited IRA (1/N)", "", 2027, 2034, color="#008300"),
        Item("Strategies", "Tap Roth after brokerage", "", 2048, 2060, color="#aeaca5",
             enabled=not selected_disabled),
        Item("Strategies", "Spending $110K/yr", "", 2026, 2060, color="#6b6a66"),
    ]


def ages_for(plan: Plan) -> dict[int, str]:
    out = {}
    for yr in range(2026, 2061):
        a, s = yr - 1964, yr - 1966
        out[yr] = f"{a}·{s}" if a <= 88 else f"{s}"
    return out


# -- screens -----------------------------------------------------------------------


class Mock:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.app = QApplication.instance() or QApplication(sys.argv)
        self.win = MainWindow()
        self.win.effective_mode = apply_app_theme(self.app, "light")
        self.win.resize(1500, 950)
        self.base_plan = Plan.load(PLAN_FILE)
        self.lib = self.win.library
        self.win.show()
        self.win.load_plan(copy.deepcopy(self.base_plan), PLAN_FILE)
        self.win.panels.set_open(self.win.options_panel, False)
        self.pv = self.win.plan_view
        self.pv.summary.hide()
        self.scen_holder = QVBoxLayout()
        self.pv.layout().insertLayout(0, self.scen_holder)
        self.strip = SummaryStrip()
        self.pv.layout().insertWidget(1, self.strip)
        # The Baseline tab holds the existing profile form.
        self.baseline = self.win.inputs_panel.scroll.takeWidget()
        self.win.inputs_panel.title.setText("<b>Plan</b>")
        self.win.inputs_action.setText("&Plan")
        self.scen_bar: ScenarioBar | None = None

    def set_panel(self, items, selected=None, editor=None, overrides=1, collapsed=()) -> PlanPanel:
        tl = Timeline(items, (2026, 2060), ages_for(self.base_plan), set(collapsed))
        tl.selected = selected
        old = self.win.inputs_panel.scroll.takeWidget()
        if isinstance(old, PlanPanel):
            # keep the profile form alive
            old.tabs.removeTab(1)
            self.baseline.setParent(None)
        panel = PlanPanel(tl, editor, overrides, self.baseline)
        self.win.inputs_panel.scroll.setWidget(panel)
        self.win.inputs_panel.setMinimumWidth(500)
        self.win.inputs_panel.setMaximumWidth(500)
        self.win.panels.set_pinned(self.win.inputs_panel, True)
        self.win.panels.set_open(self.win.inputs_panel, True)
        return panel

    def set_scenario(self, current="Base plan", compare=None) -> None:
        if self.scen_bar:
            self.scen_bar.setParent(None)
        self.scen_bar = ScenarioBar(current, compare)
        self.scen_holder.addWidget(self.scen_bar)

    def show_plan(self, plan: Plan) -> None:
        self.pv.set_plan(plan)
        self.pv.recompute()

    def strip_change(self, now: Totals, before: Totals, caption: str) -> None:
        tiles = []
        for title, a, b, neg in (
            ("Lifetime federal + state tax", now.tax, before.tax, True),
            ("IRMAA surcharges", now.irmaa, before.irmaa, True),
            ("Final balances", now.final, before.final, False),
            ("Still in traditional accounts", now.traditional, before.traditional, True),
        ):
            ch, col = delta(a - b, neg)
            tiles.append((title, money(a), ch, col))
        self.strip.set_tiles(tiles, caption)

    def shot(self, name: str, widget: QWidget | None = None, overlay: tuple[QWidget, QPoint] | None = None) -> None:
        self.app.processEvents()
        self.app.processEvents()
        pm = (widget or self.win).grab()
        if overlay:
            w, pos = overlay
            w.adjustSize()
            sub = w.grab()
            p = QPainter(pm)
            p.drawPixmap(pos, sub)
            p.setPen(QPen(QColor("#b9b7b1"), 1))
            p.drawRect(QRect(pos, sub.deviceIndependentSize().toSize()).adjusted(0, 0, -1, -1))
            p.end()
        path = self.out / f"{name}.png"
        pm.save(str(path))
        print("wrote", path)

    def run(self) -> None:
        base = totals(self.base_plan, self.lib)

        # 1. Overview: the plan timeline beside the graph, with the Add menu open.
        self.set_scenario()
        self.show_plan(copy.deepcopy(self.base_plan))
        self.strip_change(base, base, "Totals update as you edit. Changes show the effect of your last edit.")
        panel = self.set_panel(plan_items(self.base_plan))
        self.pv.tabs.setCurrentIndex(0)
        menu = QMenu()
        for text in ("Milestone", "Account", "Income stream", "One-time income or expense", "Social Security",
                     None, "Roth conversion", "Withdrawal", "Spending change", None, "From a template…"):
            if text is None:
                menu.addSeparator()
            else:
                menu.addAction(text)
        self.app.processEvents()
        btn = panel.add_btn
        pos = btn.mapTo(self.win, QPoint(0, btn.height()))
        self.shot("1-overview-add-menu", overlay=(menu, pos))

        # 2. Editing a strategy: conversions now run to age 72; totals show the change.
        p2 = copy.deepcopy(self.base_plan)
        p2.rules[1].end = When(age=72, person=0)
        t2 = totals(p2, self.lib)
        self.show_plan(p2)
        self.strip_change(t2, base, "Change since your last edit: conversions end at Alex 72 instead of 70.")
        self.set_panel(plan_items(p2, conv_end=2036), selected="Convert Alex IRA to Roth",
                       editor=conversion_editor(),
                       collapsed=("Accounts", "Income", "Social Security"))
        self.shot("2-edit-strategy-live-totals")

        # 3. Milestones: moving "Sam retires" moves the items tied to it.
        p3 = copy.deepcopy(self.base_plan)
        p3.incomes[0].end = When(age=62, person=1)
        t3 = totals(p3, self.lib)
        self.show_plan(p3)
        self.strip_change(t3, base, "Change since your last edit: Sam retires at 62 instead of 60.")
        self.set_panel(plan_items(p3, sam_retire=2028), selected="Sam retires", editor=milestone_editor(),
                       collapsed=("Accounts", "Social Security", "Strategies"))
        self.shot("3-edit-milestone")

        # 4. Year table overrides: typed cells pin a year; the rule fills the rest.
        p4 = copy.deepcopy(self.base_plan)
        p4.rules.append(Rule(kind="roth_conversion", account="alex_ira", start=When(year=2029), mode="fixed",
                             amount=150000, note="override"))
        t4 = totals(p4, self.lib)
        self.show_plan(p4)
        self.strip_change(t4, base, "Change since your last edit: 2029 conversion typed in as $150,000.")
        self.set_panel(plan_items(p4, pins=(2029, 2031)), overrides=2)
        self.pv.tabs.setCurrentIndex(2)
        self.style_year_table(pinned={(2029, "Roth conversions"), (2031, "Roth conversions")},
                              inputs={"Roth conversions", "Inherited IRA withdrawals"})
        self.shot("4-year-table-overrides")

        # 5. Scenario compare.
        p5 = copy.deepcopy(self.base_plan)
        p5.rules[1].target = "bracket:0.24"
        t5 = totals(p5, self.lib)
        self.set_scenario("Fill to 24% until 70", "Base plan")
        self.show_plan(p5)
        self.strip_change(t5, base, "Compared with Base plan. Turn off Compare to see changes since your last edit.")
        self.set_panel(plan_items(p5), overrides=1)
        self.add_compare_tab(base, t5)
        self.shot("5-scenario-compare")

        # 6-7. Setup wizard.
        self.win.panels.set_open(self.win.inputs_panel, False)
        none = copy.deepcopy(self.base_plan)
        none.rules[1].enabled = False
        none.rules[2].enabled = False
        t0 = totals(none, self.lib)
        for name, page in (("6-wizard-accounts", 2), ("7-wizard-strategy", 6)):
            dlg = wizard(page, t0, t5)
            dlg.resize(860, 520)
            dlg.show()
            self.shot(name, widget=dlg)
            dlg.close()

    def style_year_table(self, pinned: set[tuple[int, str]], inputs: set[str]) -> None:
        t = self.pv.table
        headers = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
        for c, h in enumerate(headers):
            if h in inputs:
                t.horizontalHeaderItem(c).setText(h + "  ✎")
        for r in range(t.rowCount()):
            year = int(t.item(r, 0).text())
            for c, h in enumerate(headers):
                item = t.item(r, c)
                if item is None:
                    continue
                if (year, h) in pinned:
                    item.setBackground(QBrush(PIN))
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                    item.setText("● " + item.text())
                elif h not in inputs and c > 2:
                    item.setBackground(QBrush(CALC))
        legend = QLabel("<b>✎ columns</b> can be typed in: a typed value <span style='background:#fbe3a6'>"
                        "&nbsp;pins that year&nbsp;</span> over the plan's rules. Grey columns are calculated. "
                        "Right-click a pinned cell to clear it, or to turn it into a rule.")
        legend.setWordWrap(True)
        idx = self.pv.layout().indexOf(self.pv.tabs)
        self.pv.layout().insertWidget(idx, legend)
        self._legend = legend
        # Input columns first, right after the year and ages; calculated ones after.
        hdr = t.horizontalHeader()
        for pos, name in enumerate(["Roth conversions", "Inherited IRA withdrawals"]):
            hdr.moveSection(hdr.visualIndex(headers.index(name)), 2 + pos)
        t.setColumnHidden(headers.index("Filing"), True)
        t.verticalHeader().hide()
        t.clearSelection()
        t.setCurrentCell(-1, -1)
        t.scrollToTop()
        t.horizontalScrollBar().setValue(0)

    def add_compare_tab(self, a: Totals, b: Totals) -> None:
        if getattr(self, "_legend", None):
            self._legend.setParent(None)
        fig = Figure(figsize=(9, 6), dpi=100)
        canvas = FigureCanvasQTAgg(fig)
        ra, rb = a.result, b.result
        years = [y.year for y in ra.years]
        ax1, ax2 = fig.subplots(1, 2)
        for ax in (ax1, ax2):
            ax.spines[["top", "right"]].set_visible(False)
            ax.grid(axis="y", color="#e6e4df")
        import itertools

        cum_a = list(itertools.accumulate(y.tax.total_tax + y.irmaa_surcharge for y in ra.years))
        cum_b = list(itertools.accumulate(y.tax.total_tax + y.irmaa_surcharge for y in rb.years))
        ax1.plot(years, [v / 1e3 for v in cum_a], color="#5f5d58", lw=2, label="Base plan")
        ax1.plot(years, [v / 1e3 for v in cum_b], color="#e34948", lw=2, ls="--", label="Fill to 24% until 70")
        ax1.set_title("Cumulative tax + IRMAA ($K)", loc="left", fontsize=11)
        bal_a = [sum(y.balances_end.values()) / 1e6 for y in ra.years]
        bal_b = [sum(y.balances_end.values()) / 1e6 for y in rb.years]
        ax2.plot(years, bal_a, color="#5f5d58", lw=2, label="Base plan")
        ax2.plot(years, bal_b, color="#e34948", lw=2, ls="--", label="Fill to 24% until 70")
        ax2.set_title("Total balances ($M)", loc="left", fontsize=11)
        ax2.legend(frameon=False, loc="upper left")
        fig.tight_layout()

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(canvas, 1)
        table = QTableWidget(5, 4)
        table.setHorizontalHeaderLabels(["", "Base plan", "Fill to 24% until 70", "Difference"])
        rows = [
            ("Lifetime federal + state tax", a.tax, b.tax, True),
            ("IRMAA surcharges", a.irmaa, b.irmaa, True),
            ("Years paying IRMAA", a.irmaa_years, b.irmaa_years, True),
            ("Final balances", a.final, b.final, False),
            ("Still in traditional accounts at the end", a.traditional, b.traditional, True),
        ]
        for r, (label, va, vb, neg) in enumerate(rows):
            is_count = isinstance(va, int)
            fmt = (lambda v: str(v)) if is_count else money
            table.setItem(r, 0, QTableWidgetItem(label))
            for c, v in ((1, va), (2, vb)):
                it = QTableWidgetItem(fmt(v))
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, c, it)
            d = vb - va
            if is_count:
                text, col = (f"{d:+d}" if d else "no change"), ("#16794c" if (d < 0) == neg else "#b42318")
            else:
                text, col = delta(d, neg)
            it = QTableWidgetItem(text)
            it.setForeground(QBrush(QColor(col if d else "#6b6a66")))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            table.setItem(r, 3, it)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.setMaximumHeight(175)
        lay.addWidget(table)
        self.pv.tabs.addTab(w, "Compare")
        self.pv.tabs.setCurrentWidget(w)


def wizard(page: int, base: Totals, alt: Totals) -> QDialog:
    dlg = QDialog()
    dlg.setWindowTitle("Plan setup")
    outer = QHBoxLayout(dlg)
    steps = QListWidget()
    for s in ("1. About you", "2. Accounts", "3. Income", "4. Social Security", "5. Spending and assumptions",
              "6. Strategy", "7. Review"):
        steps.addItem(s)
    steps.setCurrentRow(page - 1)
    steps.setFixedWidth(210)
    outer.addWidget(steps)
    right = QVBoxLayout()
    outer.addLayout(right, 1)
    if page == 2:
        right.addWidget(QLabel("<h2>What accounts do you have?</h2>"))
        tip = QLabel("Today's balances are enough. You can add details such as cost basis or an inherited "
                     "IRA's dates later on the Plan items list.")
        tip.setWordWrap(True)
        right.addWidget(tip)
        table = QTableWidget(6, 4)
        table.setHorizontalHeaderLabels(["Owner", "Type", "Balance", ""])
        data = [("Alex", "Traditional IRA", "1,100,000"), ("Sam", "401(k)/403(b)", "350,000"),
                ("Alex", "Inherited IRA", "400,000"), ("Alex", "Roth IRA", "90,000"),
                ("Joint", "Taxable brokerage", "600,000"), ("Joint", "Cash / savings", "60,000")]
        for r, row in enumerate(data):
            for c, v in enumerate(row):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(r, c, it)
            table.setItem(r, 3, QTableWidgetItem("Remove"))
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        right.addWidget(table, 1)
        right.addWidget(QPushButton("Add an account"), 0, Qt.AlignmentFlag.AlignLeft)
        q = QLabel("Inherited IRA: what year did you inherit it? &nbsp;<b>2024</b> &nbsp;&nbsp;"
                   "<a href='#'>more questions about this account…</a>")
        right.addWidget(q)
    else:
        right.addWidget(QLabel("<h2>Do you want a Roth conversion strategy?</h2>"))
        tip = QLabel("Pick a starting point. It becomes an item on your plan that you can change, or override "
                     "for single years, at any time.")
        tip.setWordWrap(True)
        right.addWidget(tip)
        group = QButtonGroup(dlg)
        opts = [
            ("No conversions for now", None),
            ("Convert each year up to the top of a tax bracket", "fill"),
            ("Convert each year but stay under an IRMAA tier", None),
            ("Convert a fixed amount each year", None),
            ("Empty the account evenly over a number of years (1/N)", None),
        ]
        for text, key in opts:
            rb = QRadioButton(text)
            group.addButton(rb)
            right.addWidget(rb)
            if key == "fill":
                rb.setChecked(True)
                sub = QGridLayout()
                sub.setContentsMargins(28, 0, 0, 8)
                sub.addWidget(QLabel("Account"), 0, 0)
                sub.addWidget(combo(["Alex Traditional IRA", "Sam 401(k)"]), 0, 1)
                sub.addWidget(QLabel("Fill to"), 0, 2)
                sub.addWidget(combo(["Top of 12%", "Top of 22%", "Top of 24%"], "Top of 24%"), 0, 3)
                sub.addWidget(QLabel("Starting"), 1, 0)
                sub.addWidget(combo(["Now", "Alex retires", "Alex age 63"], "Alex age 63"), 1, 1)
                sub.addWidget(QLabel("Until"), 1, 2)
                sub.addWidget(combo(["Alex claims Social Security (70)", "RMDs start (75)", "Alex age 72"]), 1, 3)
                right.addLayout(sub)
        right.addSpacing(10)
        ch, col = delta(alt.tax - base.tax, True)
        cht, colt = delta(alt.traditional - base.traditional, True)
        prev = QLabel(f"<b>Preview</b>, updating as you pick: lifetime tax <b>{money(alt.tax)}</b> "
                      f"(<span style='color:{col}'>{ch}</span> against no conversions); left in traditional "
                      f"accounts at the end <b>{money(alt.traditional)}</b> (<span style='color:{colt}'>{cht}</span>). "
                      "You'll see the full graph after the Review step.")
        prev.setWordWrap(True)
        prev.setStyleSheet("background:#f1f0ec;padding:8px;border-radius:4px;")
        right.addWidget(prev)
        right.addStretch(1)
    btns = QHBoxLayout()
    btns.addWidget(QLabel("<span style='color:#6b6a66'>Skip any step; everything can be changed later.</span>"))
    btns.addStretch(1)
    for t in ("Back", "Next", "Skip to the graph"):
        btns.addWidget(QPushButton(t))
    right.addLayout(btns)
    return dlg


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "spike-out")
    out.mkdir(parents=True, exist_ok=True)
    Mock(out).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
