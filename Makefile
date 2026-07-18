.PHONY: test lint format format-check preflight validate-csv local-backtest paper-replay check tree

test:
	python -m pytest

lint:
	python -m ruff check .

format:
	python -m ruff format .

format-check:
	python -m ruff format --check .

preflight:
	python scripts/preflight_check.py

validate-csv:
	python -m trading_bot_lab validate-csv

local-backtest:
	python scripts/run_local_backtest.py

paper-replay:
	python scripts/run_paper_replay.py

check: lint format-check test preflight

tree:
	python scripts/print_tree.py
