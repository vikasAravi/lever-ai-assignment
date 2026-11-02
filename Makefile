# One entry point for setup and checks. Test targets land with the test harness.
PYTHON ?= /opt/homebrew/bin/python3.11
VENV   := .venv
PY     := $(VENV)/bin/python

.PHONY: setup check test-unit

# Create the virtualenv, install the package with dev extras, install the git pre-commit hook.
setup:
	test -x $(PY) || $(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e '.[dev]'
	git config core.hooksPath scripts/git-hooks
	@echo "setup: ok ($$($(PY) --version))"

# Static checks (CLAUDE.md R1, R2). More join here as their areas land (R6, R7, catalog drift).
check:
	python3 scripts/check_pack.py
	python3 scripts/secret_scan.py

# Unit tests, whole output (CLAUDE.md R4), plus JUnit XML for test-output/summary.md.
test-unit:
	@mkdir -p test-output
	$(PY) -m pytest tests/unit -rA --junitxml=test-output/unit.xml
