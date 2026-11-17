"""Values typed into the Inputs panel stay put. Skipped without Qt."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")
QtGui = pytest.importorskip("PySide6.QtGui")
QtTest = pytest.importorskip("PySide6.QtTest")

Qt = QtCore.Qt
QTest = QtTest.QTest


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def panel(app):
    """The profile form inside a short scrolling side panel, as in the app."""
    from rtpl.app.profile_form import ProfileForm
    from rtpl.app.side_panel import SidePanel

    form = ProfileForm(["CA", "NC"])
    side = SidePanel("Inputs", form, Qt.Edge.LeftEdge)
    side.resize(320, 300)
    side.show()
    QTest.qWaitForWindowExposed(side)
    yield form, side
    side.close()
    side.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


def wheel(widget, notches):
    """Send mouse wheel notches (negative is scrolling down) to the widget."""
    center = QtCore.QPointF(widget.rect().center())
    event = QtGui.QWheelEvent(
        center,
        QtCore.QPointF(widget.mapToGlobal(widget.rect().center())),
        QtCore.QPoint(0, 0),
        QtCore.QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QtWidgets.QApplication.sendEvent(widget, event)
    return event


def test_wheel_passes_through_money_fields(panel):
    form, _side = panel
    box = form.household_money["nonqualified_dividends"]
    box.setValue(6160)
    for _ in range(5):  # used to step $1,000 a notch: $6,160 became $1,160
        event = wheel(box, -1)
        # Not accepted, so Qt hands the wheel on to the panel to scroll it.
        assert not event.isAccepted()
    assert box.value() == 6160
    wheel(box, 2)
    assert box.value() == 6160


def test_wheel_leaves_a_focused_field_alone(panel):
    form, _side = panel
    box = form.household_money["taxable_interest"]
    box.setValue(2500)
    box.setFocus()
    wheel(box, -3)
    assert box.value() == 2500


def test_wheel_does_not_change_choices(panel):
    form, _side = panel
    form.filing.setCurrentIndex(0)
    state = form.state.currentIndex()
    wheel(form.filing, -1)
    wheel(form.state, -1)
    assert form.filing.currentIndex() == 0
    assert form.state.currentIndex() == state


def test_click_then_type_replaces_the_number(panel, app):
    form, _side = panel
    box = form.household_money["nonqualified_dividends"]
    box.setValue(1160)
    form.plan_name.setFocus()
    edit = box.lineEdit()
    QTest.mouseClick(
        edit, Qt.MouseButton.LeftButton, pos=QtCore.QPoint(edit.width() - 4, 5)
    )
    QTest.qWait(5)
    QTest.keyClicks(edit, "6160")
    assert box.value() == 6160  # not $1,160,616
    # A second click places the cursor, so one digit can be fixed.
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton, pos=QtCore.QPoint(10, 5))
    QTest.qWait(5)
    assert not edit.hasSelectedText()


def test_value_updates_while_typing(panel):
    form, _side = panel
    seen = []
    form.changed.connect(
        lambda: seen.append(form.profile().household.qualified_dividends)
    )
    box = form.household_money["qualified_dividends"]
    box.setFocus()
    box.lineEdit().selectAll()
    QTest.keyClicks(box.lineEdit(), "4200")
    assert seen[-1] == 4200  # no Enter or Tab needed


def _clear_and_leave(box, form):
    box.setFocus()
    box.lineEdit().selectAll()
    QTest.keyClick(box.lineEdit(), Qt.Key.Key_Delete)
    form.plan_name.setFocus()


def test_cleared_money_field_means_zero(panel):
    form, _side = panel
    for box in (
        form.household_money["nonqualified_dividends"],
        form.household_money["long_term_capital_gains"],
    ):
        box.setValue(1160)
        _clear_and_leave(box, form)
        assert box.value() == 0  # used to go back to $1,160


def test_cleared_year_keeps_its_value(panel):
    form, _side = panel
    form.year.setValue(2027)
    _clear_and_leave(form.year, form)
    assert form.year.value() == 2027


def test_too_large_value_is_clamped_not_dropped(panel):
    form, _side = panel
    form.inflation.setValue(2.5)
    form.inflation.setFocus()
    form.inflation.lineEdit().selectAll()
    QTest.keyClicks(form.inflation.lineEdit(), "9")
    form.plan_name.setFocus()
    assert form.inflation.value() == 9.0
