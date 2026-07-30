PYTHON ?= python

.PHONY: check clean format format-check install-hooks lint package test typecheck

check: format-check lint typecheck test

clean:
	$(RM) -r build dist

format:
	$(PYTHON) -m ruff format src tests

format-check:
	$(PYTHON) -m ruff format --check src tests

install-hooks:
	$(PYTHON) -m pre_commit install --hook-type pre-commit --hook-type pre-push

lint:
	$(PYTHON) -m ruff check src tests

package: clean
	$(PYTHON) -m build

test:
	$(PYTHON) -m pytest

typecheck:
	$(PYTHON) -m mypy
