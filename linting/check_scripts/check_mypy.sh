#!/bin/bash
# Runs mypy when a staged Python file is in one of the packages CI type-checks
# (pyproject.toml [tool.mypy] files). Same command as the CI job: plain `mypy`.
# Usage: check_mypy.sh <file> ...

SCOPE=$(grep -E '^files = \[' pyproject.toml 2>/dev/null | grep -oE '"[^"]+"' | tr -d '"')
if [ -z "$SCOPE" ]; then
    echo "No [tool.mypy] files in pyproject.toml — skipping"
    exit 0
fi

IN_SCOPE=0
for file in "$@"; do
    [[ "$file" =~ \.py$ ]] || continue
    for pkg in $SCOPE; do
        [[ "$file" == "$pkg"/* ]] && IN_SCOPE=1
    done
done
if [ $IN_SCOPE -eq 0 ]; then
    echo "No staged files in type-checked packages ($(echo $SCOPE | tr '\n' ' '))"
    exit 0
fi

MYPY_BIN=""
for candidate in .venv/bin/mypy .venv/Scripts/mypy "$(command -v mypy 2>/dev/null)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        MYPY_BIN="$candidate"
        break
    fi
done
if [ -z "$MYPY_BIN" ]; then
    echo "mypy not found — install dev dependencies: pip install -r requirements-dev.txt"
    exit 1
fi

if ! "$MYPY_BIN"; then
    echo "ERROR: mypy found type errors."
    exit 1
fi
