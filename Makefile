PYTHON ?= python

.PHONY: check clean environments format format-check install-hooks lint package test typecheck

check: format-check lint typecheck test

clean:
	$(RM) -r build dist

# Re-render the environment templates from tools/environments/ (never
# part of `check`: a rendered template's bytes are item identity, so
# regeneration is deliberate and its diff is reviewed as a contract
# change). `check` only verifies the committed renders match.
environments:
	$(PYTHON) tools/environments/generate.py

format:
	$(PYTHON) -m ruff format src tests tools

format-check:
	$(PYTHON) -m ruff format --check src tests tools

install-hooks:
	$(PYTHON) -m pre_commit install --hook-type pre-commit --hook-type pre-push

lint:
	$(PYTHON) -m ruff check src tests tools

package: clean
	$(PYTHON) -m build

test:
	$(PYTHON) -m pytest

typecheck:
	$(PYTHON) -m mypy
