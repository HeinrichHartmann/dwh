.PHONY: install test test-v test-cov clean help

help:
	@echo "DWH Development Commands:"
	@echo "  make install   - Install as a tool"
	@echo "  make test      - Run all tests"
	@echo "  make test-v    - Run tests with verbose output"
	@echo "  make test-cov  - Run tests with coverage report"
	@echo "  make clean     - Clean up temporary files"

install:
	uv tool install --force --reinstall .

test:
	uv run pytest tests/

test-v:
	uv run pytest tests/ -v

test-cov:
	uv run pytest tests/ --cov=dwh --cov-report=term-missing

clean:
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .coverage htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
