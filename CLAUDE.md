# Notes for contributors and coding assistants

* **Version:** every pull request that changes anything under `src/`,
  `packaging/` or `pyproject.toml` must raise `__version__` in
  `src/rtpl/__init__.py` (the only place it is set): PATCH for fixes, MINOR
  for features. The *version-bump* check fails otherwise, and each merge to
  `main` with a new version publishes a GitHub Release. See
  `docs/packaging.md`.
* Run the tests with `QT_QPA_PLATFORM=offscreen uv run --extra dev pytest -q`.
