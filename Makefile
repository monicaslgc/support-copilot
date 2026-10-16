.PHONY: help install run doctor seed demo test lint check clean
.DEFAULT_GOAL := help

help:  ## list the commands
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

install:  ## install the package with its development dependencies
	pip install -e ".[dev]"

run:  ## start the app and review page at http://127.0.0.1:8000/
	uvicorn support_copilot.main:app --reload

doctor:  ## check the configuration (add ARGS=--live to test the helpdesk credentials)
	python -m support_copilot.doctor $(ARGS)

seed:  ## load sample tickets into a running server
	python scripts/seed_demo.py

demo:  ## play the user stories against a running server
	python scripts/demo.py all

test:  ## run the tests (offline)
	pytest -q

lint:  ## style, bug and type checks
	ruff check .
	mypy

check: lint test  ## what CI runs

clean:  ## remove caches and local databases
	rm -rf .pytest_cache .ruff_cache .mypy_cache *.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
