"""Main window: profile on the left, marginal graph and data table on the right."""

from __future__ import annotations

from pathlib import Path

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from rtpl import __version__
from rtpl.app.profile_form import ProfileForm, money_box
from rtpl.charts.marginal_chart import render_marginal
from rtpl.engine.marginal import (
    ANNOTATIONS,
    LAYERS,
    MarginalSeries,
    build_marginal_series,
    default_visibility,
)
from rtpl.engine.tax import IncomeSource
from rtpl.model.profile import Person, Profile
from rtpl.taxlaw.loader import TaxLawLibrary
from rtpl.theme import Theme

APP_NAME = "RetirementTaxPlannerLite"
ANNOTATION_LABELS = {"irmaa": "IRMAA brackets (Medicare)", "aca": "ACA subsidy cliffs"}


def config_dir() -> Path:
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
    path.mkdir(parents=True, exist_ok=True)
    return path


def apply_app_theme(app: QApplication, mode: str) -> str:
    """Apply light/dark to the Qt widgets. Returns the effective mode."""
    if mode == "system":
        scheme = QGuiApplication.styleHints().colorScheme()
        mode = "dark" if scheme == Qt.ColorScheme.Dark else "light"
    app.setStyle("Fusion")
    pal = QPalette()
    if mode == "dark":
        c = {
            QPalette.ColorRole.Window: "#242423",
            QPalette.ColorRole.WindowText: "#f0efec",
            QPalette.ColorRole.Base: "#151514",
            QPalette.ColorRole.Light: "#77766f",
            QPalette.ColorRole.Midlight: "#5c5b56",
            QPalette.ColorRole.Mid: "#6a6964",
            QPalette.ColorRole.Dark: "#4a4945",
            QPalette.ColorRole.Shadow: "#000000",
            QPalette.ColorRole.AlternateBase: "#262625",
            QPalette.ColorRole.Text: "#f0efec",
            QPalette.ColorRole.Button: "#2b2b2a",
            QPalette.ColorRole.ButtonText: "#f0efec",
            QPalette.ColorRole.Highlight: "#3987e5",
            QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.ToolTipBase: "#2b2b2a",
            QPalette.ColorRole.ToolTipText: "#f0efec",
            QPalette.ColorRole.PlaceholderText: "#9a998f",
            QPalette.ColorRole.Link: "#86b6ef",
        }
        for role, color in c.items():
            pal.setColor(role, QColor(color))
        pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#7a7970"))
        pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#7a7970"))
    else:
        pal = app.style().standardPalette()
    app.setPalette(pal)
    return mode


class ColorDialog(QDialog):
    """Pick the chart color for each layer, separately for light and dark."""

    def __init__(self, theme: Theme, labels: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chart Colors")
        self.theme = theme
        grid = QGridLayout(self)
        grid.addWidget(QLabel("<b>Layer</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Light mode</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Dark mode</b>"), 0, 2)
        for row, key in enumerate(LAYERS, start=1):
            grid.addWidget(QLabel(labels[key]), row, 0)
            for col, mode in enumerate(("light", "dark"), start=1):
                btn = QPushButton()
                self._paint(btn, theme.raw[mode]["layers"][key])
                btn.setAccessibleName(f"{labels[key]} color, {mode} mode")
                btn.clicked.connect(lambda _=False, b=btn, m=mode, k=key: self._pick(b, m, k))
                grid.addWidget(btn, row, col)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        grid.addWidget(buttons, len(LAYERS) + 1, 0, 1, 3)

    @staticmethod
    def _paint(btn: QPushButton, color: str) -> None:
        btn.setText(color)
        btn.setStyleSheet(f"QPushButton {{ border-left: 18px solid {color}; padding: 4px 8px; }}")

    def _pick(self, btn: QPushButton, mode: str, key: str) -> None:
        current = QColor(self.theme.raw[mode]["layers"][key])
        color = QColorDialog.getColor(current, self, "Choose color")
        if color.isValid():
            self.theme.set_layer_color(mode, key, color.name())
            self._paint(btn, color.name())


class MainWindow(QMainWindow):
    def __init__(self, library: TaxLawLibrary | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.library = library or TaxLawLibrary(user_dirs=[config_dir() / "taxlaw"])
        self.theme_path = config_dir() / "theme.toml"
        self.theme = Theme.load(self.theme_path)
        self.theme_mode = "system"
        self.effective_mode = "light"
        self.current_path: Path | None = None
        self.series: MarginalSeries | None = None
        self.overrides: dict[str, bool] = {}  # user's explicit layer choices

        # Left: profile editor
        self.form = ProfileForm(self.library.available_states())
        scroll = QScrollArea()
        scroll.setWidget(self.form)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(400)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Right: options, chart, table
        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.addWidget(self._build_options())
        self.tabs = QTabWidget()
        self.figure = Figure(figsize=(9, 6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setAccessibleName("Marginal tax rate chart")
        self.canvas.setAccessibleDescription("The Data table tab lists the same information as text.")
        self.tabs.addTab(self.canvas, "&Chart")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Additional income", "Marginal rate", "What changes"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName("Marginal rate data table")
        self.tabs.addTab(self.table, "&Data table")
        rlay.addWidget(self.tabs, 1)

        splitter = QSplitter()
        splitter.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        self._build_menus()

        self._timer = QTimer(self, singleShot=True, interval=200)
        self._timer.timeout.connect(self.recompute)
        self.form.changed.connect(self._schedule)

        self.form.set_profile(self._starter_profile())
        self.statusBar().showMessage("Ready")

    # -- building ------------------------------------------------------
    @staticmethod
    def _starter_profile() -> Profile:
        return Profile(
            name="Example",
            year=2026,
            filing_status="mfj",
            people=[
                Person(name="Person 1", birth_year=1959, social_security=30000),
                Person(name="Person 2", birth_year=1961, social_security=18000),
            ],
        )

    def _build_options(self) -> QWidget:
        box = QGroupBox("Graph options")
        lay = QHBoxLayout(box)
        left = QFormLayout()
        self.source = QComboBox()
        for s in IncomeSource:
            self.source.addItem(s.label[0].upper() + s.label[1:], s.value)
        self.source.setAccessibleName("Source of additional income")
        src_label = QLabel("&Additional income source")
        src_label.setBuddy(self.source)
        left.addRow(src_label, self.source)
        self.x_max = money_box()
        self.x_max.setRange(10_000, 5_000_000)
        self.x_max.setSingleStep(25_000)
        self.x_max.setValue(250_000)
        self.x_max.setAccessibleName("Show additional income up to")
        xlabel = QLabel("Show up &to")
        xlabel.setBuddy(self.x_max)
        left.addRow(xlabel, self.x_max)
        lay.addLayout(left)

        grid = QGridLayout()
        self.checks: dict[str, QCheckBox] = {}
        names = {k: k for k in LAYERS}
        names.update(
            ordinary="Ordinary income tax",
            ss="Social Security phase-in",
            senior="Senior deduction phase-out",
            cg="Capital gains / QD tax",
            niit="NIIT",
            state="State tax",
        )
        names.update(ANNOTATION_LABELS)
        for i, key in enumerate([*LAYERS, *ANNOTATIONS]):
            cb = QCheckBox(names[key])
            cb.clicked.connect(lambda checked, k=key: self._user_toggled(k, checked))
            self.checks[key] = cb
            grid.addWidget(cb, i // 3, i % 3)
        reset = QPushButton("&Reset to suggested")
        reset.setToolTip("Go back to the layers suggested by the profile")
        reset.clicked.connect(self._reset_visibility)
        grid.addWidget(reset, 3, 0)
        lay.addLayout(grid, 1)

        self.source.currentIndexChanged.connect(self._schedule)
        self.x_max.valueChanged.connect(self._schedule)
        return box

    def _build_menus(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, key, slot in (
            ("&New", QKeySequence.StandardKey.New, self.new_profile),
            ("&Open…", QKeySequence.StandardKey.Open, self.open_profile),
            ("&Save", QKeySequence.StandardKey.Save, self.save_profile),
            ("Save &As…", QKeySequence.StandardKey.SaveAs, self.save_profile_as),
        ):
            act = QAction(text, self, shortcut=key, triggered=slot)
            m.addAction(act)
        m.addSeparator()
        m.addAction(QAction("&Export Chart…", self, shortcut="Ctrl+E", triggered=self.export_chart))
        m.addSeparator()
        m.addAction(QAction("E&xit", self, shortcut=QKeySequence.StandardKey.Quit, triggered=self.close))

        v = self.menuBar().addMenu("&View")
        group = QActionGroup(self)
        for text, mode in (("&System theme", "system"), ("&Light mode", "light"), ("&Dark mode", "dark")):
            act = QAction(text, self, checkable=True, checked=mode == "system")
            act.triggered.connect(lambda _=False, md=mode: self.set_theme_mode(md))
            group.addAction(act)
            v.addAction(act)
        v.addSeparator()
        v.addAction(QAction("Chart &Colors…", self, triggered=self.edit_colors))

        h = self.menuBar().addMenu("&Help")
        h.addAction(QAction("&About", self, triggered=self.about))

    # -- behaviour -----------------------------------------------------
    def _schedule(self, *_args) -> None:
        self._timer.start()

    def _user_toggled(self, key: str, checked: bool) -> None:
        self.overrides[key] = checked
        self.redraw()

    def _reset_visibility(self) -> None:
        self.overrides.clear()
        self.recompute()

    def visibility(self) -> dict[str, bool]:
        assert self.series is not None
        vis = default_visibility(self.series)
        vis.update(self.overrides)
        return vis

    def recompute(self) -> None:
        profile = self.form.profile()
        problems = profile.validate()
        if problems:
            self.statusBar().showMessage("; ".join(problems))
            return
        try:
            self.series = build_marginal_series(
                profile,
                self.library,
                source=IncomeSource(self.source.currentData()),
                x_max=self.x_max.value(),
                step=max(100.0, self.x_max.value() / 2500),
            )
        except (KeyError, ValueError) as exc:
            self.statusBar().showMessage(f"Cannot compute: {exc}")
            return
        state_cb = self.checks["state"]
        state_cb.setText(self.series.state.label if self.series.state else "State tax")
        state_cb.setEnabled(self.series.state is not None)
        self.redraw()
        b = self.series.baseline
        self.statusBar().showMessage(
            f"Baseline: AGI ${b.agi:,.0f}, taxable income ${b.taxable_income:,.0f}, "
            f"federal tax ${b.federal_tax:,.0f}, state tax ${b.state_tax:,.0f}"
        )

    def redraw(self) -> None:
        if self.series is None:
            return
        vis = self.visibility()
        for key, cb in self.checks.items():
            cb.blockSignals(True)
            cb.setChecked(vis.get(key, False))
            cb.blockSignals(False)
        render_marginal(self.figure, self.series, vis, self.theme.palette(self.effective_mode))
        self.canvas.draw_idle()
        self._fill_table(vis)

    def _fill_table(self, vis: dict[str, bool]) -> None:
        s = self.series
        assert s is not None
        shown = [k for k in LAYERS if vis.get(k)]
        rows: list[tuple[float, str, str]] = []
        prev = None
        for i, x in enumerate(s.x):
            total = round(sum(s.layers[k][i] for k in shown), 4)
            if prev is None or abs(total - prev) > 1e-4:
                parts = [
                    f"{s.layer_label(k)} {s.layers[k][i]:.1%}" for k in shown if abs(s.layers[k][i]) > 1e-6
                ]
                rows.append((x, f"{total:.1%}", ", ".join(parts)))
                prev = total
        for a in s.annotations:
            if vis.get(a.kind):
                rows.append((a.x, "", a.detail))
        rows.sort(key=lambda r: r[0])
        self.table.setRowCount(len(rows))
        for r, (x, rate, what) in enumerate(rows):
            for c, text in enumerate((f"${x:,.0f}", rate, what)):
                item = QTableWidgetItem(text)
                if c < 2:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

    def set_theme_mode(self, mode: str) -> None:
        self.theme_mode = mode
        self.effective_mode = apply_app_theme(QApplication.instance(), mode)
        self.redraw()

    def edit_colors(self) -> None:
        labels = {k: (self.series.layer_label(k) if self.series else k) for k in LAYERS}
        working = Theme(raw=__import__("copy").deepcopy(self.theme.raw))
        dlg = ColorDialog(working, labels, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.theme = working
            self.theme_path.write_text(self.theme.to_toml(), encoding="utf-8")
            self.redraw()

    # -- files -----------------------------------------------------------
    def new_profile(self) -> None:
        self.current_path = None
        self.overrides.clear()
        self.form.set_profile(self._starter_profile())

    def open_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Profile", "", "Profiles (*.json)")
        if not path:
            return
        try:
            profile = Profile.load(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Open Profile", f"Could not open the profile:\n{exc}")
            return
        self.current_path = Path(path)
        self.overrides.clear()
        self.form.set_profile(profile)
        self.setWindowTitle(f"{self.current_path.name} - {APP_NAME}")

    def save_profile(self) -> None:
        if self.current_path is None:
            self.save_profile_as()
            return
        self.form.profile().save(self.current_path)
        self.statusBar().showMessage(f"Saved {self.current_path}", 4000)

    def save_profile_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save Profile", "profile.json", "Profiles (*.json)")
        if path:
            self.current_path = Path(path)
            self.save_profile()
            self.setWindowTitle(f"{self.current_path.name} - {APP_NAME}")

    def export_chart(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Chart", "marginal-tax.png", "PNG image (*.png);;SVG image (*.svg);;PDF (*.pdf)"
        )
        if path:
            self.figure.savefig(path, dpi=200, facecolor=self.figure.get_facecolor())
            self.statusBar().showMessage(f"Exported {path}", 4000)

    def about(self) -> None:
        years = ", ".join(str(y) for y in self.library.enacted_years)
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br>Free, open-source tax graphs.<br><br>"
            f"Tax law data files: {years} (later years are projected).<br>"
            "This is a planning aid, not tax advice.",
        )
