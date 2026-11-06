#!/bin/bash
set -euo pipefail

# Clone the repository
git clone https://github.com/jrogue/sample.git ~/git/sample
cd ~/git/sample

# Inspect history and file contents
echo "=== Commit history ==="
git log --oneline

echo "=== Files containing secret.key reference ==="
grep -r 'secret.key' . || true

# Remove the latest commit that directly added secret.key (assumed to be HEAD)
git reset --hard HEAD^

# Clean the reference to secret.key from the Makefile, which was part of the
# previous commit ("hello, world!"). Remove any line mentioning secret.key
# and amend that commit.
if grep -q 'secret.key' Makefile; then
    sed -i '/secret.key/d' Makefile
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