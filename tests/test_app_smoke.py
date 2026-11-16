"""Opens the main window offscreen and exercises it. Skipped without Qt."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from rtpl.app.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow()
    yield w
    w.setWindowModified(False)  # never block on the save prompt in tests
    w.close()
    # Destroy the window (and its child dialogs) now, while QApplication is
    # alive; Qt objects left for interpreter shutdown can crash the process.
    w.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


def test_main_window_draws(window, tmp_path):
    from rtpl.model.profile import Profile

    path = Path("examples/profiles/retired_couple_ca.json")
    window._load_profile(Profile.load(path), path)
    window.recompute()
    assert window.series is not None and window.series.state.code == "CA"
    assert window.checks["state"].text() == "State Tax (CA)"
    assert window.table.rowCount() > 0
    window.checks["state"].click()  # user turns the state layer off
    assert window.visibility()["state"] is False
    window.set_theme_mode("dark")
    out = tmp_path / "chart.svg"
    window.figure.savefig(out)
    assert out.stat().st_size > 0


def test_title_tracks_file_and_changes(window):
    from rtpl.model.profile import Profile

    assert window.windowTitle().startswith("Untitled[*]")
    assert not window.isWindowModified()
    path = Path("examples/profiles/retired_couple_ca.json")
    window._load_profile(Profile.load(path), path)
    assert window.windowTitle().startswith("retired_couple_ca.json[*]")
    window.form.plan_name.setText("Edited")
    assert window.isWindowModified()
    window.setWindowModified(False)
    window.new_profile()
    assert window.windowTitle().startswith("Untitled[*]")


def test_age_shown_next_to_birth_year(window):
    box = window.form.person_boxes[0]
    window.form.year.setValue(2026)
    box.birth_year.setValue(1958)
    assert box.age.text() == "Age 68 at end of 2026"
    window.form.year.setValue(2030)
    assert box.age.text() == "Age 72 at end of 2030"


def test_safe_file_stem():
    from rtpl.app.main_window import safe_file_stem

    assert safe_file_stem('Plan: A/B "v2"?') == "Plan_ A_B _v2__"
    assert safe_file_stem("  trailing dots... ") == "trailing dots"
    assert safe_file_stem("CON") == "_CON"
    assert safe_file_stem("nul.backup") == "_nul.backup"
    assert safe_file_stem("") == "profile"
    assert safe_file_stem("a\\b|c<d>e*f") == "a_b_c_d_e_f"


def test_color_dialog_link_and_reset(window):
    from rtpl.app.main_window import ColorDialog
    from rtpl.theme import Theme

    theme = Theme.load(None)
    labels = {k: k for k in theme.raw["light"]["layers"]}
    dlg = ColorDialog(theme, labels, window)  # parented, so it is cleaned up with the window
    theme.set_layer_color("light", "ordinary", "#123456")
    dlg.same.setChecked(True)
    assert theme.linked
    assert theme.palette("dark").layers["ordinary"] == "#123456"
    assert dlg.buttons[("dark", "ordinary")].isHidden()
    dlg._reset()
    assert theme.raw["light"]["layers"]["ordinary"] == Theme.default_raw()["light"]["layers"]["ordinary"]
    assert theme.palette("dark").layers == theme.palette("light").layers
    dlg.same.setChecked(False)
    assert theme.palette("dark").surface != theme.palette("light").surface
    assert "same_layer_colors = false" in theme.to_toml()


def test_inflation_and_irmaa_basis_controls(window):
    window.form.inflation.setValue(3.0)
    assert window.form.profile().inflation == 0.03
    window.irmaa_basis.setCurrentIndex(1)
    window.recompute()
    irmaa = [a for a in window.series.annotations if a.kind == "irmaa"]
    assert irmaa and "published thresholds" in irmaa[0].detail


def test_window_fits_small_screens(window):
    """Both panels pinned must still fit a 1024 px wide screen."""
    for panel in (window.inputs_panel, window.options_panel):
        window.panels.set_pinned(panel, True)
        window.panels.set_open(panel, True)
    assert window.minimumSizeHint().width() <= 1000


def test_panels_pin_and_pop_open(window):
    panels, inputs = window.panels, window.inputs_panel
    window.resize(1024, 740)
    window.show()
    panels.set_pinned(inputs, False)
    panels.set_open(inputs, False)
    assert inputs.isHidden() and not window.inputs_action.isChecked()
    window.inputs_action.trigger()  # pops open over the graph
    assert inputs.isVisible() and inputs.parent() is window.centralWidget()
    assert window.inputs_action.isChecked()
    panels.close_floating()
    assert inputs.isHidden()
    inputs.pin.click()  # pinning puts it beside the graph
    assert inputs.parent() is panels.splitter
    assert window.settings.value("panels/inputs_pinned") in (True, "true")


def test_window_geometry_and_panel_width_restored(window):
    from rtpl.app.main_window import MainWindow

    window.resize(700, 500)  # the offscreen test screen is 800 x 600
    window.inputs_panel.saved_width = 360
    window.save_window_state()
    again = MainWindow()
    try:
        again.resize_to_screen()
        assert (again.width(), again.height()) == (700, 500)
        assert again.inputs_panel.preferred_width() == 360
        again.reset_layout()
        assert again.settings.value("window/geometry") is None
    finally:
        again.setWindowModified(False)
        again.deleteLater()


def test_pin_check_box_shows_state(window):
    panels, inputs = window.panels, window.inputs_panel
    panels.set_pinned(inputs, True)
    assert inputs.pin.isChecked() and inputs.pin.text() == "Pinned"
    panels.set_pinned(inputs, False)
    assert not inputs.pin.isChecked() and inputs.parent() is window.centralWidget()
