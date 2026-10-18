PYTHON ?= python3

all: format lint test docs

format:
	$(PYTHON) -m black .
	$(PYTHON) -m isort --profile=black .

format-check:
	$(PYTHON) -m black --check .
	$(PYTHON) -m isort --profile=black --check .

pylint:
	$(PYTHON) -m pylint sourcerunner

typecheck:
	$(PYTHON) -m mypy sourcerunner

lint: format-check pylint typecheck

test:
	$(PYTHON) -m pytest -sv --cov=sourcerunner -m unit tests
