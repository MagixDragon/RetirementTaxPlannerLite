"""Input widgets that keep the value the user typed.

Stock Qt spin boxes and combo boxes have three habits that make values
seem not to "stick" in a scrolling form:

* The mouse wheel changes any spin box or combo box under the pointer,
  even one that is not being edited. Scrolling the Inputs panel with the
  pointer over a money field silently steps it by $1,000 per notch, so
  $6,160 becomes $1,160 after five notches.
* Clicking into a field puts the cursor where you clicked and keeps the
  old text, so typing a new number gets mixed into the old one
  ("$1,160" + "6160" = "$1,160,616").
* Clearing a field and moving on quietly puts the old value back.

The widgets here fix all three: the wheel always scrolls the panel and
never changes a value (use the arrow keys or the arrow buttons for
that), the first click into a field selects its whole text so typing
replaces it, and a cleared number field means zero
(or, for a year, keeps its old value).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QFocusEvent, QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QDoubleSpinBox, QSpinBox


class _SpinBoxFixes:
    """Shared behaviour for the spin box classes below (a mixin)."""

    def _setup(self) -> None:
        # Click or Tab to focus; the wheel no longer focuses the field.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # A value typed out of range is clamped to the nearest allowed one,
        # and an emptied field becomes zero (see fixup), instead of both
        # silently going back to the old value.
        self.setCorrectionMode(QAbstractSpinBox.CorrectionMode.CorrectToNearestValue)
        self._select_on_release = False
        self.lineEdit().installEventFilter(self)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()  # let the scroll area behind the field scroll

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        if event.reason() == Qt.FocusReason.MouseFocusReason:
            # The press that focused us is still placing the cursor; select
            # everything once it is done so typing replaces the old number.
            self._select_on_release = True
            QTimer.singleShot(0, self.selectAll)

    def eventFilter(self, obj, event) -> bool:
        # A quick drag during the focusing click can undo the selection made
        # above, so select again on that click's release.
        if (
            obj is self.lineEdit()
            and event.type() == QEvent.Type.MouseButtonRelease
            and self._select_on_release
        ):
            self._select_on_release = False
            if not self.lineEdit().hasSelectedText():
                QTimer.singleShot(0, self.selectAll)
        return False

    def _blank_value(self):
        """The value an emptied field stands for.

        Zero when zero is allowed (money, percentages); otherwise, as for a
        year, the value the field had before it was cleared.
        """
        if self.minimum() <= 0 <= self.maximum():
            return 0
        return self.value()

    def fixup(self, text: str) -> str:
        bare = text.strip()
        for part in (self.prefix(), self.suffix()):
            if part:
                bare = bare.replace(part.strip(), "")
        bare = bare.replace(self.locale().groupSeparator(), "").strip()
        if bare in ("", "-", "+"):
            return self.textFromValue(self._blank_value())
        return super().fixup(text)


class SpinBox(_SpinBoxFixes, QSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup()


class DoubleSpinBox(_SpinBoxFixes, QDoubleSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup()


class ComboBox(QComboBox):
    """A combo box that the wheel scrolls past instead of changing."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()
