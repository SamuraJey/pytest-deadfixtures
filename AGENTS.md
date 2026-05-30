# Repository Guidelines

## Project Structure & Module Organization

This repository is a compact Python package for the `pytest-deadfixtures` plugin.
The plugin implementation lives in `pytest_deadfixtures.py` and is exposed through
`setup.py` as a pytest entry point. Tests are in `tests/`, with shared pytest
configuration in `tests/conftest.py`. User-facing documentation and examples live
in `README.rst`; release notes are kept in `CHANGES.rst`. Packaging and CI files
include `setup.py`, `pyproject.toml`, `tox.ini`, `MANIFEST.in`, and
`.github/workflows/ci.yaml`.

## Build, Test, and Development Commands

- `python -m pip install -r requirements.txt` installs local development tools.
- `python -m pip install -e .` installs the plugin in editable mode for testing.
- `pytest` runs the local test suite.
- `tox` runs the configured test matrix with coverage reporting.
- `pytest --dead-fixtures` dogfoods the plugin against this repository.
- `pre-commit run -a -v` or `make lint` runs formatting, import sorting, and lint checks.
- `python setup.py sdist bdist_wheel` builds release artifacts when packaging tools are installed.

## Coding Style & Naming Conventions

Use idiomatic Python with 4-space indentation and `snake_case` for functions,
fixtures, and variables. Keep the public plugin API small and explicit; constants
such as exit codes and output headlines are defined near the top of
`pytest_deadfixtures.py`. Formatting and linting are enforced through pre-commit:
Ruff format/check, isort, flake8, and standard hygiene hooks. The configured line
length is 90 characters.

## Testing Guidelines

Tests use pytest and the `pytester` plugin to create temporary test suites and
assert plugin behavior. Name new test files `test_*.py` and test functions
`test_<behavior>`. Prefer focused tests that exercise command-line options such as
`--dead-fixtures`, `--dup-fixtures`, and `--show-ignored-fixtures`. Before opening a
pull request, run `tox` and confirm coverage does not decrease.

## Commit & Pull Request Guidelines

Recent commits use short, direct messages such as `Add support for ignoring
specific fixtures` or `Update pre-commit configuration and replace black with
ruff`. Follow that style: start with an imperative verb and describe the behavior
changed. Pull requests should include a concise summary, linked issues when
applicable, test/lint results, and documentation or `CHANGES.rst` updates for
user-visible behavior changes.

## Agent-Specific Notes

Do not commit local runtime state such as `.omx/`. Keep changes small and verify
plugin behavior with pytest-based commands before modifying release metadata.
