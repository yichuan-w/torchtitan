#!/bin/bash
set -e

REPO_ROOT="/root/git-katas/rebase-branch"
SPEC_FILE="$REPO_ROOT/CHANGELOG_SPEC.md"
CHANGELOG_FILE="$REPO_ROOT/changelog.md"
DIFF_REPORT_FILE="$REPO_ROOT/diff_report.md"
EXERCISE_DIR="$REPO_ROOT/exercise"

# ------------------------------------------------------------
# Stage 1 : inspect prerequisites
# ------------------------------------------------------------
if [ ! -f "$SPEC_FILE" ]; then
    echo "Error: CHANGELOG_SPEC.md not found. Aborting." >&2
    exit 1
fi

echo "=== Inspecting changelog specification ==="
cat "$SPEC_FILE"

# ------------------------------------------------------------
# Stage 2 : initialize kata (setup git repo)
# ------------------------------------------------------------
cd "$REPO_ROOT"
if [ ! -f setup.sh ]; then
    echo "Error: setup.sh not found. Cannot initialize kata." >&2
    exit 1
fi
source setup.sh

# ------------------------------------------------------------
# Stage 3 : derive changelog format from spec
# ------------------------------------------------------------
if grep -q "| Commit Hash | Date | Author | Message |" "$SPEC_FILE"; then
    echo "Spec format detected: standard markdown table"
else
    echo "Error: Unable to determine required format from spec." >&2
    exit 1
fi

# ------------------------------------------------------------
# Stage 4 : execute rebase and fast-forward
# ------------------------------------------------------------
echo "=== Performing rebase and fast-forward ==="
git -C "$EXERCISE_DIR" checkout uppercase
git -C "$EXERCISE_DIR" rebase master
git -C "$EXERCISE_DIR" checkout master
git -C "$EXERCISE_DIR" merge uppercase --ff-only

# ------------------------------------------------------------
# Stage 5 : generate changelog.md according to spec
# ------------------------------------------------------------
echo "=== Generating changelog ==="
cat > "$CHANGELOG_FILE" <<CH_HEADER
# Changelog

This changelog lists all commits on the \`master\` branch after rebase, in reverse chronological order.

| Commit Hash | Date | Author | Message |
|---|---|---|---|
CH_HEADER

git -C "$EXERCISE_DIR" log --format="| %H | %ad | %an | %s |" --date=short master >> "$CHANGELOG_FILE"

# basic sanity
if [ ! -s "$CHANGELOG_FILE" ]; then
    echo "Error: changelog.md is empty." >&2
    exit 1
fi

MASTER_TIP=$(git -C "$EXERCISE_DIR" rev-parse master)
if head -n 10 "$CHANGELOG_FILE" | grep -q "$MASTER_TIP"; then
    echo "Validation: master tip commit hash found in changelog."
else
    echo "Warning: master tip commit hash not found in top of changelog. Check format."
fi

# ------------------------------------------------------------
# Stage 6 : derive pre-rebase commit from reflog
# ------------------------------------------------------------
echo "=== Inspecting reflog to find pre-rebase commit ==="

# After rebase, uppercase@{1} refers to the original (pre-rebase) tip of uppercase.
# This hash is only available through the reflog and is not reachable from the
# current branch tip.
OLD_COMMIT=$(git -C "$EXERCISE_DIR" rev-parse "uppercase@{1}" 2>/dev/null)
if [ -z "$OLD_COMMIT" ]; then
    echo "Error: unable to retrieve pre-rebase commit from reflog." >&2
    exit 1
fi
echo "Pre-rebase commit (obtained from reflog): $OLD_COMMIT"

NEW_COMMIT=$(git -C "$EXERCISE_DIR" rev-parse master)
echo "Final master tip: $NEW_COMMIT"

# ------------------------------------------------------------
# Stage 7 : generate diff_report.md
# ------------------------------------------------------------
echo "=== Generating forensic diff report ==="

cat > "$DIFF_REPORT_FILE" <<DIFF_HEADER
# Forensic Diff Report

This report compares the final \`master\` tip with the pre-rebase commit of the
\`uppercase\` branch, as recovered from the reflog.

- **Pre-rebase commit (uppercase before rebase):** \`$OLD_COMMIT\`
- **Final master tip (after rebase + fast-forward):** \`$NEW_COMMIT\`

## Unified diff

\`\`\`diff
DIFF_HEADER

git -C "$EXERCISE_DIR" diff --patch "$OLD_COMMIT" "$NEW_COMMIT" >> "$DIFF_REPORT_FILE"

echo '```' >> "$DIFF_REPORT_FILE"

# ------------------------------------------------------------
# Stage 8 : validate diff report
# ------------------------------------------------------------
echo "=== Validating diff report ==="

if [ ! -s "$DIFF_REPORT_FILE" ]; then
    echo "Error: diff_report.md is empty." >&2
    exit 1
fi

if grep -q "$OLD_COMMIT" "$DIFF_REPORT_FILE"; then
    echo "Validation: diff_report.md references the pre-rebase commit hash."
else
    echo "Error: diff_report.md does not reference the pre-rebase commit hash." >&2
    exit 1
fi

if grep -q '^---' "$DIFF_REPORT_FILE" || grep -q '^@@' "$DIFF_REPORT_FILE"; then
    echo "Validation: diff_report.md contains a diff hunk."
else
    echo "Warning: diff_report.md may not contain recognizable diff output."
fi

echo "=== Done ==="
