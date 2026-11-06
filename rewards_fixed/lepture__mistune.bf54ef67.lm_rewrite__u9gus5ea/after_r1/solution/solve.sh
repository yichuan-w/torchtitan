#!/bin/bash
# Apply the focused renderer repair to the planted upstream behavior.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /testbed
git apply --whitespace=nowarn "$HERE/bug.patch"
