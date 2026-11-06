#!/bin/bash
set -e

REPO_DIR="/root/git-katas/rebase-branch/exercise"
WORK_DIR="/root/git-katas/rebase-branch"
AUDIT_FILE="/root/audit.txt"

# --------------- inspect / initialize ---------------
cd "$WORK_DIR"

if [ -d "$REPO_DIR/.git" ]; then
    echo "Repository already initialized, skipping setup."
    cd "$REPO_DIR"
else
    echo "Initializing kata environment..."
    source setup.sh
    # setup.sh cd's into the exercise directory automatically
fi

# Confirm we are inside the repo
if [ "$(git rev-parse --show-toplevel)" != "$(realpath "$REPO_DIR")" ]; then
    echo "Error: not inside the expected repository" >&2
    exit 1
fi

# --------------- rebase and fast-forward ---------------
echo "Rebasing uppercase onto master..."
git checkout uppercase
git rebase master

echo "Fast-forwarding master to include rebased commits..."
git checkout master
git merge --ff-only uppercase

# --------------- derive baseline and final commits ---------------
# The reflog entry master@{1} refers to master's state before the fast-forward,
# which is the original master commit before the rebase workflow.
BASELINE_COMMIT=$(git rev-parse --verify master@{1}) || {
    echo "Error: cannot resolve master@{1}. Reflog entry missing." >&2
    exit 1
}
echo "Baseline commit from reflog: $BASELINE_COMMIT"

FINAL_COMMIT=$(git rev-parse master)
echo "Final commit: $FINAL_COMMIT"

REFLOG_REF="master@{1}"

# --------------- generate unified diff ---------------
TEMP_DIFF="/tmp/audit.diff"
git diff "$BASELINE_COMMIT" master > "$TEMP_DIFF"

# --------------- validate diff ---------------
if [ ! -s "$TEMP_DIFF" ]; then
    echo "Error: generated diff is empty; expected changes from the rebase." >&2
    exit 1
fi

# Self-check: compare generated diff against actual git diff using the same range
git diff "$BASELINE_COMMIT" master | diff - "$TEMP_DIFF" > /dev/null || {
    echo "Error: generated diff does not match actual git diff." >&2
    exit 1
}

# --------------- finalize: write forensics header and diff ---------------
{
    echo "Baseline commit: $BASELINE_COMMIT"
    echo "Final commit: $FINAL_COMMIT"
    echo "Reflog reference: $REFLOG_REF"
    cat "$TEMP_DIFF"
} > "$AUDIT_FILE"

echo "Audit report written to $AUDIT_FILE"