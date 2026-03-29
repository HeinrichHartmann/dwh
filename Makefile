.PHONY: install test clean

install:
	uv tool install --force --reinstall .

test:
	uv run python -m dwh --help

clean:
	rm -rf dist/
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
