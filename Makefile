.PHONY: setup test lint eval serve check docker
setup: ; python -m venv .venv && .venv/bin/pip install -e ".[dev]"
test: ; pytest
lint: ; ruff check src tests && mypy
eval: ; python -m aiarch.evaluation.runner --gate --out reports/eval.json
serve: ; python -m aiarch.cli serve
check: lint test eval
docker: ; docker compose up --build
