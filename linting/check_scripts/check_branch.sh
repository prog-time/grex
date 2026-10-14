#!/bin/bash
# Commits go only to a task branch issues-N (see CONTRIBUTING.md): the number
# links every commit to its GitHub Issue.

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)

# Detached HEAD (rebase, bisect, cherry-pick in progress): nothing to check.
if [ -z "$BRANCH" ] || [ "$BRANCH" = "HEAD" ]; then
    exit 0
fi

if [[ "$BRANCH" =~ ^issues-[0-9]+$ ]]; then
    echo "Branch $BRANCH"
    exit 0
fi

echo "ERROR: commits are only allowed on a task branch issues-N, current branch: $BRANCH"
echo "Create one for your Issue: git switch -c issues-<number> (or /task_branch)."
exit 1
