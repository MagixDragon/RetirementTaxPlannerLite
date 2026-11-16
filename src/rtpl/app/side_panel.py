"""Side panels that can be pinned beside the graph or pop open over it.

A pinned panel sits in the window's splitter and takes space from the
graph. An unpinned panel is hidden until its toolbar button (or shortcut)
opens it; it then floats over the edge of the graph and closes again on
Escape, on a click on the graph, or when focus leaves it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class SidePanel(QFrame):
    """A titled panel with a pin button and a scrolling body."""

    pin_toggled = Signal(bool)
    close_requested = Signal()

    def __init__(self, title: str, body: QWidget, side: Qt.Edge) -> None:
        super().__init__()
        self.side = side
        self.body = body
        self.setObjectName("SidePanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)  # opaque when floating over the graph
        self.setAccessibleName(title.replace("&", ""))

        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 4, 0)
        self.title = QLabel(f"<b>{title.replace('&', '')}</b>")
        # A check box shows the state itself (checked = pinned), so there is
        # no "does the label say what it is or what it will do?" question.
        self.pin = QCheckBox("Pinned")
        self.pin.setAccessibleName(f"Pin {title.replace('&', '')} beside the graph")
        self.pin.toggled.connect(self._pin_changed)
        self.saved_width = 0  # width the user dragged a pinned panel to
        self.close_btn = QToolButton()
        self.close_btn.setAutoRaise(True)
        self.close_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        self.close_btn.setToolTip("Hide this panel")
        self.close_btn.setAccessibleName(f"Hide {title.replace('&', '')}")
        self.close_btn.clicked.connect(self.close_requested)
        head.addWidget(self.title, 1)
        head.addWidget(self.pin)
        head.addSpacing(4)
        head.addWidget(self.close_btn)

        self.scroll = QScrollArea()
        self.scroll.setWidget(body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(self.scroll, 1)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self._escape, context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._pin_changed(False)
        self.fit_width()

    def fit_width(self) -> None:
        """Never narrower than the body, so fields are not cut off and tabbing
        through them cannot scroll the body sideways."""
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        self.setMinimumWidth(self.body.minimumSizeHint().width() + bar + 2 * self.frameWidth() + 2)

    def preferred_width(self) -> int:
        return max(self.minimumWidth(), self.saved_width)

    def _pin_changed(self, pinned: bool) -> None:
        self.pin.setToolTip(
            "Pinned: the panel stays open beside the graph. Uncheck to have it "
            "pop open over the graph only when you need it."
            if pinned
            else "Not pinned: the panel pops open over the graph and closes when you "
            "click the graph or press Esc. Check to keep it open beside the graph."
        )
        # Floating panels get a highlight border so they never look pinned.
        self.setProperty("floating", not pinned)
        self.setStyleSheet(
            "#SidePanel[floating=\"true\"] { border: 2px solid palette(highlight); "
            "background: palette(window); }"
        )
        self.pin_toggled.emit(pinned)

    def _escape(self) -> None:
        if not self.pin.isChecked():
            self.close_requested.emit()


class PanelHost(QObject):
    """Places a left and a right :class:`SidePanel` around a center widget.

    ``workspace`` is the window's central widget; it holds the splitter and
    is the parent of any unpinned (floating) panel.
    """

    visibility_changed = Signal(object, bool)  # panel, open
    pinned_changed = Signal(object, bool)

    def __init__(self, workspace: QWidget, center: QWidget) -> None:
        super().__init__(workspace)
        self.workspace = workspace
        self.center = center
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(center)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.splitterMoved.connect(self._remember_widths)
        lay = QHBoxLayout(workspace)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.splitter)
        self.panels: list[SidePanel] = []
        self._open: dict[int, bool] = {}
        workspace.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        QApplication.instance().focusChanged.connect(self._focus_changed)

    # -- setup -----------------------------------------------------------
    def add(self, panel: SidePanel, pinned: bool, open_: bool) -> None:
        self.panels.append(panel)
        self._open[id(panel)] = open_
        panel.pin.blockSignals(True)
        panel.pin.setChecked(pinned)
        panel.pin.blockSignals(False)
        panel._pin_changed(pinned)
        panel.pin_toggled.connect(lambda p, pn=panel: self.set_pinned(pn, p))
        panel.close_requested.connect(lambda pn=panel: self.set_open(pn, False))
        self._place(panel)

    def is_pinned(self, panel: SidePanel) -> bool:
        return panel.pin.isChecked()

    def is_open(self, panel: SidePanel) -> bool:
        return self._open[id(panel)]

    # -- state changes -------------------------------------------------
    def set_pinned(self, panel: SidePanel, pinned: bool) -> None:
        if panel.pin.isChecked() != pinned:
            panel.pin.setChecked(pinned)  # re-enters through pin_toggled
            return
        if pinned and not self._open[id(panel)]:
            self._open[id(panel)] = True  # pinning a panel always shows it
            self.visibility_changed.emit(panel, True)
        self._place(panel)
        self.pinned_changed.emit(panel, pinned)

    def set_open(self, panel: SidePanel, open_: bool) -> None:
        if self._open[id(panel)] == open_:
            return
        self._open[id(panel)] = open_
        had_focus = panel.isAncestorOf(QApplication.focusWidget()) if QApplication.focusWidget() else False
        self._place(panel)
        if open_ and not self.is_pinned(panel):
            panel.body.setFocus(Qt.FocusReason.OtherFocusReason)
            panel.body.focusNextChild()
        elif not open_ and had_focus:
            self.center.setFocus(Qt.FocusReason.OtherFocusReason)
        self.visibility_changed.emit(panel, open_)

    def toggle(self, panel: SidePanel) -> None:
        self.set_open(panel, not self.is_open(panel))

    # -- layout ------------------------------------------------------------
    def _place(self, panel: SidePanel) -> None:
        open_ = self._open[id(panel)]
        if self.is_pinned(panel):
            panel.setGraphicsEffect(None)
            if panel.parent() is not self.splitter:
                if panel.side == Qt.Edge.LeftEdge:
                    self.splitter.insertWidget(0, panel)
                else:
                    self.splitter.addWidget(panel)
                idx = self.splitter.indexOf(panel)
                self.splitter.setStretchFactor(idx, 0)
                self.splitter.setStretchFactor(self.splitter.indexOf(self.center), 1)
            panel.setVisible(open_)
            if open_:
                self._give_space(panel)
        else:
            if panel.parent() is not self.workspace:
                panel.setParent(self.workspace)
                shadow = QGraphicsDropShadowEffect(panel)
                shadow.setBlurRadius(18)
                shadow.setOffset(0, 2)
                shadow.setColor(QColor(0, 0, 0, 110))
                panel.setGraphicsEffect(shadow)
            panel.setVisible(open_)
            if open_:
                self._float_geometry(panel)
                panel.raise_()

    def _give_space(self, panel: SidePanel) -> None:
        sizes = self.splitter.sizes()
        idx = self.splitter.indexOf(panel)
        want = panel.preferred_width()
        if sizes[idx] < want:
            c = self.splitter.indexOf(self.center)
            sizes[c] = max(0, sizes[c] - (want - sizes[idx]))
            sizes[idx] = want
            self.splitter.setSizes(sizes)

    def _remember_widths(self, *_args) -> None:
        for p in self.panels:
            if self.is_pinned(p) and p.isVisible():
                p.saved_width = p.width()

    def _float_geometry(self, panel: SidePanel) -> None:
        area = self.workspace.rect()
        w = min(panel.preferred_width(), area.width())
        x = 0 if panel.side == Qt.Edge.LeftEdge else area.width() - w
        panel.setGeometry(x, 0, w, area.height())

    # -- events ------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt name)
        if obj is self.workspace and event.type() == QEvent.Type.Resize:
            for p in self.panels:
                if not self.is_pinned(p) and self.is_open(p):
                    self._float_geometry(p)
        elif (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(obj, QWidget)
            and (obj is self.center or self.center.isAncestorOf(obj))
        ):
            self.close_floating()
        return False

    def close_floating(self, keep: SidePanel | None = None) -> None:
        for p in self.panels:
            if p is not keep and not self.is_pinned(p) and self.is_open(p):
                self.set_open(p, False)

    def _focus_changed(self, _old, new) -> None:
        if new is None:
            return  # e.g. another app took focus; leave the panel open
        if new.window() is not self.workspace.window():
            return  # a dialog or popup list of a combo box inside the panel
        for p in self.panels:
            if not self.is_pinned(p) and self.is_open(p) and not p.isAncestorOf(new) and new is not p:
                if self.center.isAncestorOf(new) or new is self.center:
                    self.set_open(p, False)
