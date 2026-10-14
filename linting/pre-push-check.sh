#!/bin/bash

set -e

echo "Lint (ruff)..."
RUFF_BIN=.venv/bin/ruff; [ -x "$RUFF_BIN" ] || RUFF_BIN=.venv/Scripts/ruff; [ -x "$RUFF_BIN" ] || RUFF_BIN=ruff
"$RUFF_BIN" check .
echo "----------"

bash linting/check_scripts/check_pytest.sh
echo "----------"
