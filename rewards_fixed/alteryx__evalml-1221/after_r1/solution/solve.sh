#!/usr/bin/env bash
set -euo pipefail

# The repo is wherever the image put it: the shallowest .git whose HEAD is the
# task's base commit. Depth-limited so a stray vendored .git cannot win.
find_repo() {
  want="$1"
  for d in $(find / -maxdepth 2 -name .git -type d 2>/dev/null | sort); do
    r="$(dirname "$d")"
    if [ "$(git -C "$r" rev-parse HEAD 2>/dev/null)" = "$want" ]; then
      echo "$r"; return 0
    fi
  done
  # No fallback. Grading "any repo at all" is worse than not grading: the old
  # fallback ran the graded tests against whatever .git turned up first, and
  # whatever it scored was recorded as a fact about this task.
  echo "no repository at base commit $want" >&2
  return 1
}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
R="$(find_repo 4387fe071fac8f1f54d0297362e1e10e6fa273b0)" || { echo "no repository found" >&2; exit 1; }
cd "$R"
git apply --whitespace=nowarn "$HERE/fix.patch"
