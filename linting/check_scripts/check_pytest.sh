#!/bin/bash
# Runs the full pytest suite (same as the CI test job). Exit code 0/1.

PYTEST_BIN=""
for candidate in .venv/bin/pytest .venv/Scripts/pytest "$(command -v pytest 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYTEST_BIN="$candidate"
        break
    fi
done
if [ -z "$PYTEST_BIN" ]; then
    echo "pytest not found — install dev dependencies: pip install -r requirements-dev.txt"
    exit 1
fi

if [ ! -f data/settings.yml ]; then
    echo "data/settings.yml not found — run ./start.sh once or copy data/settings.yml.example"
    exit 1
fi

echo "Running full test suite (pytest)..."
if ! "$PYTEST_BIN" -q; then
    echo "ERROR: tests failed — fix them before pushing."
    exit 1
fi
