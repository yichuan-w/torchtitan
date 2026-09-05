#!/bin/bash
# Handle signals the loop already closed, again, without touching anything
# it keeps: a change to the loop tried on the same tasks the production loop
# saw. Each replay is `evolve_ondella.py --signal <id>`, which implies --dry:
# the rewrite directory is written in full (package, sessions, rewrite.json
# marked dry) and no ledger line, lineage line or mix version is.
#
#   usage: TRL_PROFILE=andy TRL_BASE=<root> TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 \
#          TT_DAYTONA_DISK_GB=2 replay_signals.sh [n] [direction]
#     n           how many of the newest handled signals (default 3)
#     direction   harder (k/k, default) or easier (0/k)
#
# The ids come from the ledger's newest `handled` lines of that direction.
# Each replay costs one Codex session and a few sandboxes for a harder
# signal, so keep n small. The loop's singleton lock applies: stop the live
# loop first, or point TRL_BASE at a forked root.
set -euo pipefail
N=${1:-3}
DIR=${2:-harder}
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/evolveloop_env.sh"
LEDGER=$TRL_BASE/evolution/ledger.jsonl
[ -f "$LEDGER" ] || { echo "no ledger at $LEDGER"; exit 1; }
echo "checkout $TT at $(git -C "$TT" log --oneline -1); $(git -C "$TT" status --porcelain --untracked-files=no | wc -l | tr -d ' ') tracked file(s) differ from HEAD"
replayed=0
while IFS= read -r sid; do
  echo "replaying $sid"
  "$PY" "$EVO/evolve_ondella.py" --signal "$sid" --workers 1
  replayed=$((replayed+1))
done < <(python3 -c '
import json, sys
n, direction = int(sys.argv[2]), sys.argv[3]
seen = []
for line in open(sys.argv[1]):
    line = line.strip()
    if not line:
        continue
    row = json.loads(line)
    if row.get("outcome") == "handled" and row.get("direction") == direction:
        seen.append(row["signal"])
for sid in reversed(seen[-n:]):
    print(sid)
' "$LEDGER" "$N" "$DIR")
echo "replayed $replayed signal(s); results under $TRL_BASE/evolution/tasks/*/rewrites/ (rewrite.json has \"dry\": true); log $LOG"
