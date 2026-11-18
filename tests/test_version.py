"""The version is set once, in rtpl/__init__.py, and read everywhere else."""

import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

import pytest

import rtpl

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_major_minor_patch():
    assert re.fullmatch(r"\d+\.\d+\.\d+", rtpl.__version__)


def test_pyproject_reads_version_from_package():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert not re.search(r"^version\s*=\s*\"", text, re.M), "set the version only in src/rtpl/__init__.py"
    assert 'version = {attr = "rtpl.__version__"}' in text


def test_installed_metadata_matches():
    try:
        meta = importlib.metadata.version("retirement-tax-planner-lite")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("package not installed")
    assert meta == rtpl.__version__


def test_version_script_matches():
    out = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "version.py")],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == rtpl.__version__
