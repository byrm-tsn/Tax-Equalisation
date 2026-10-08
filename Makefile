# Common tasks. Run `make install` once, then `make check` before committing.
# Every target except `install` uses the virtual environment in .venv.

PYTHON ?= python3.13
VENV := .venv/bin

.PHONY: install test lint typecheck check run example

install:
	$(PYTHON) -m venv .venv && $(VENV)/pip install -e ".[dev,web]"

test:
	$(VENV)/pytest -q

lint:
	$(VENV)/ruff check src tests && $(VENV)/ruff format --check src tests

typecheck:
	$(VENV)/mypy src/teq_engine src/teq_guidance src/teq_web

check: lint typecheck test

run:
	$(VENV)/python src/manage.py migrate --verbosity 0 && $(VENV)/python src/manage.py runserver

example:
	$(VENV)/python src/manage.py estimate --example
