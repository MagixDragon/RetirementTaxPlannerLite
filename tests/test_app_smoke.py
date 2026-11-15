"""Opens the main window offscreen and draws a chart. Skipped without Qt."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_main_window_draws(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from rtpl.app.main_window import MainWindow
    from rtpl.model.profile import Profile

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow()
    w.form.set_profile(Profile.load("examples/profiles/retired_couple_ca.json"))
    w.recompute()
    assert w.series is not None and w.series.state.code == "CA"
    assert w.checks["state"].text() == "State Tax (CA)"
    assert w.table.rowCount() > 0
    w.checks["state"].click()  # user turns the state layer off
    assert w.visibility()["state"] is False
    w.set_theme_mode("dark")
    out = tmp_path / "chart.svg"
    w.figure.savefig(out)
    assert out.stat().st_size > 0
    w.close()
    app.processEvents()
