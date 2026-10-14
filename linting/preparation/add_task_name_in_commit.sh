#!/bin/sh
# Prefixes the first line of the message in $1 with "issues-N|" from the branch name.

COMMIT_MSG_FILE="${1:-.git/COMMIT_EDITMSG}"
BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

if echo "$BRANCH_NAME" | grep -qE 'issues-[0-9]+'; then
  TASK_ID=$(echo "$BRANCH_NAME" | grep -oE 'issues-[0-9]+')

  if ! grep -q "$TASK_ID" "$COMMIT_MSG_FILE"; then
    sed -i.bak "1s/^/$TASK_ID|/" "$COMMIT_MSG_FILE"
    rm -f "$COMMIT_MSG_FILE.bak"
  fi
fi
