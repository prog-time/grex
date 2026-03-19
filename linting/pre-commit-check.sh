#!/bin/bash

set -e

ALL_FILE_ARRAY=()
while IFS= read -r line; do
    ALL_FILE_ARRAY+=("$line")
done < <(git diff --cached --name-only --diff-filter=ACM || true)

#echo "ALL_FILE_ARRAY по индексам:"
#for i in "${!ALL_FILE_ARRAY[@]}"; do
#    echo "[$i] = '${ALL_FILE_ARRAY[$i]}'"
#done

echo "Find tests..."
bash linting/check_scripts/find_test.sh "${ALL_FILE_ARRAY[@]}"
echo "----------"

echo "Static analysis (mypy)..."
bash linting/check_scripts/check_mypy.sh "${ALL_FILE_ARRAY[@]}"
echo "----------"

echo "Fixing code style (flake8)..."
bash linting/check_scripts/check_flake8.sh "${ALL_FILE_ARRAY[@]}"
echo "----------"
