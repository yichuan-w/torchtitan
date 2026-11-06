#!/bin/bash
# Oracle for a SWE-smith seed: the shipped patch is the bug, so the fix is its reverse.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /testbed
git apply -R --whitespace=nowarn "$HERE/bug.patch"
sed -i 's/while new_pos < size and len(children) < self.max:/while (new_pos < size or len(children) < self.min) and len(children) < self.max:/' parsimonious/expressions.py
