#!/bin/bash
# prepare-commit-msg hook. Git passes:
#   $1 — the commit message file
#   $2 — where the message came from: empty (plain `git commit`), message (-m/-F),
#        template, merge, squash or commit (--amend / -c / -C)

set -e

MSG_FILE="${1:-.git/COMMIT_EDITMSG}"
SOURCE="${2:-}"

# Merge and squash messages are written by git; leave them alone.
case "$SOURCE" in
    merge|squash) exit 0 ;;
esac

# Generate a message only when the author did not write one.
if [ -z "$SOURCE" ]; then
    bash linting/preparation/prepare-commit-message.sh "$MSG_FILE"
fi

# Still no message (no Claude CLI, or it returned nothing): add nothing, so git
# aborts the commit as empty instead of committing a bare file list.
if ! grep -qv -e '^#' -e '^[[:space:]]*$' "$MSG_FILE"; then
    exit 0
fi

bash linting/preparation/add_task_name_in_commit.sh "$MSG_FILE"

# An amended commit already has its file list.
if [ "$SOURCE" != "commit" ]; then
    bash linting/preparation/add_files_in_commit_message.sh "$MSG_FILE"
fi
