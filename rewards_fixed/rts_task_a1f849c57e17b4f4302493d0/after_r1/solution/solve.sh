#!/bin/bash
set -euo pipefail

# Clone the repository
rm -rf ~/git/sample
git clone https://github.com/jrogue/sample.git ~/git/sample
cd ~/git/sample

# Inspect history and file contents
echo "=== Commit history ==="
git --no-pager log --oneline

echo "=== Files containing secret.key reference ==="
grep -r 'secret.key' . || true

# Remove the commit that directly added secret.key while replaying later work
bad_commit=$(git log --format=%H --diff-filter=A -- secret.key | tail -n 1)
test -n "$bad_commit"
git rebase --onto "$bad_commit^" "$bad_commit" master

# Clean the reference to secret.key from the Makefile, which was part of the
# earlier history. Repair it in the rewritten branch.
if grep -q 'secret.key' Makefile; then
    sed -i 's/ secret\.key//' Makefile
    git add Makefile
    git commit --amend --no-edit
fi

# Force-push the corrected history to the remote
git push origin +master

# Clone a fresh copy and write its contents to /app/result.txt
cd ~/git
rm -rf sample
git clone https://github.com/jrogue/sample.git sample
ls sample > /app/result.txt
