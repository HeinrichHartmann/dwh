#!/bin/bash
# Install git pre-commit hook for ruff linting

set -e

HOOK_FILE=".git/hooks/pre-commit"

echo "Installing pre-commit hook..."

cat > "$HOOK_FILE" << 'EOF'
#!/bin/sh
# Pre-commit hook to run ruff linting

echo "Running ruff linter..."

# Run ruff check
if ! uv run ruff check src/ tests/; then
    echo "❌ Ruff check failed. Please fix linting errors before committing."
    exit 1
fi

# Run ruff format check
if ! uv run ruff format --check src/ tests/; then
    echo "❌ Code formatting check failed. Run 'uv run ruff format src/ tests/' to fix."
    exit 1
fi

echo "✓ All linting checks passed"
exit 0
EOF

chmod +x "$HOOK_FILE"

echo "✓ Pre-commit hook installed at $HOOK_FILE"
echo ""
echo "The hook will run 'make lint' before each commit."
echo "To bypass the hook (not recommended), use: git commit --no-verify"
