.PHONY: install test test-v test-cov lint clean

install:
	uv tool install --force --reinstall .

test:
	uv run pytest tests/

test-v:
	uv run pytest tests/ -v

test-cov:
	uv run pytest tests/ --cov=dwh --cov-report=term-missing

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

clean:
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
