#!/bin/bash
# Runs ruff (the project linter, same as CI) on the staged Python files.
# Usage: check_ruff.sh <file> ...

PY_FILES=()
for file in "$@"; do
    [[ "$file" =~ \.py$ ]] && [ -f "$file" ] && PY_FILES+=("$file")
done

if [ ${#PY_FILES[@]} -eq 0 ]; then
    echo "No Python files to check"
    exit 0
fi

RUFF_BIN=""
for candidate in .venv/bin/ruff .venv/Scripts/ruff "$(command -v ruff 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        RUFF_BIN="$candidate"
        break
    fi
done
if [ -z "$RUFF_BIN" ]; then
    echo "ruff not found — install dev dependencies: pip install -r requirements-dev.txt"
    exit 1
fi

if ! "$RUFF_BIN" check "${PY_FILES[@]}"; then
    echo "ERROR: ruff found issues. Fix them (many with: $RUFF_BIN check --fix <files>) and commit again."
    exit 1
fi
echo "ruff passed (${#PY_FILES[@]} files checked)"
