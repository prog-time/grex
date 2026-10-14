#!/bin/sh
# Adds a "Files:" block with the staged changes to the message in $1. The block
# goes before trailers such as Co-Authored-By, which git and GitHub only
# recognise in the last paragraph of the message.

COMMIT_MSG_FILE="${1:-.git/COMMIT_EDITMSG}"

if grep -qE '^Merge' "$COMMIT_MSG_FILE"; then
  exit 0
fi

# Already added (e.g. the hook ran twice for one commit)
if grep -q '^Files:$' "$COMMIT_MSG_FILE"; then
  exit 0
fi

CHANGES=$(git diff --cached --name-status)
[ -z "$CHANGES" ] && exit 0

BLOCK=$(
  echo "------------------------------"
  echo "Files:"
  echo "$CHANGES" | while read -r STATUS FILE1 FILE2; do
    case "$STATUS" in
      (A) echo "Added:    $FILE1" ;;
      (M) echo "Changed:  $FILE1" ;;
      (D) echo "Deleted:  $FILE1" ;;
      (R*) echo "Renamed: $FILE1 → $FILE2" ;;
      (*) echo "$STATUS:  $FILE1" ;;
    esac
  done
)

# Split the message into body / trailing trailer paragraph / git comments and
# insert the block between body and trailers.
BLOCK="$BLOCK" awk '
  /^#/ { comments[++nc] = $0; next }
  { lines[++n] = $0 }
  END {
    last = n; while (last > 0 && lines[last] ~ /^[[:space:]]*$/) last--
    start = last; while (start > 0 && lines[start] !~ /^[[:space:]]*$/) start--
    trailers = (last > 0)
    for (i = start + 1; i <= last; i++) if (lines[i] !~ /^[A-Za-z0-9-]+: /) trailers = 0
    if (start == 0) trailers = 0   # a one-paragraph message is the title, not trailers
    body_end = trailers ? start : last
    while (body_end > 0 && lines[body_end] ~ /^[[:space:]]*$/) body_end--
    for (i = 1; i <= body_end; i++) print lines[i]
    print ""
    print ENVIRON["BLOCK"]
    if (trailers) { print ""; for (i = start + 1; i <= last; i++) print lines[i] }
    if (nc) { print ""; for (i = 1; i <= nc; i++) print comments[i] }
  }
' "$COMMIT_MSG_FILE" > "$COMMIT_MSG_FILE.tmp" && mv "$COMMIT_MSG_FILE.tmp" "$COMMIT_MSG_FILE"
