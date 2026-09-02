#!/bin/bash
# The verification tool a task-evolution agent calls on its own work.
#
# Builds the package in this directory on Daytona, runs solution/solve.sh, and
# grades it with the package's real verifier -- the same path the training
# harness uses, so a pass here means the task is genuinely solvable as written.
#
# Prints a compact verdict and exits 0 only when the reference solution scores
# 1.0. Everything the agent needs in order to fix a failure is in the output.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=/scratch/gpfs/TRIDAO/al9080/terminal-rl
PY=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
PKG=${1:-.}

[ -f "$PKG/instruction.md" ] || { echo "VERDICT: error -- no instruction.md in $PKG"; exit 2; }

# shellcheck disable=SC1091
. ~/.config/daytona/env

# Per-invocation error file: several of these run at once and a shared path
# would interleave one run's stderr into another's report.
ERR=$(mktemp -t agent_validate.XXXXXX.err)
trap 'rm -f "$ERR"' EXIT

# The sandbox platform fails a couple of percent of creates under load, and a
# transient one here is expensive out of proportion: the agent reads it as "the
# environment is broken", gives up, and the whole session is spent. Retry the
# infra-shaped failures; a real verdict -- pass or fail -- returns immediately.
JSON=""
for attempt in 1 2 3; do
    # Resolved from the harness directory, not from this script's location:
    # evolve_codex.py copies this file into the agent's workdir as ./validate,
    # so there $HERE is the workdir and holds no daytona_revalidate.py. The
    # caller exports EVOLVE_HARNESS_DIR; $HERE stays as the fallback for
    # running the script in place. One copy of the revalidator, next to the
    # module it imports -- the out-of-repo scripts/ dir once held a second,
    # drifted one.
    OUT=$("$PY" "${EVOLVE_HARNESS_DIR:-$HERE}/daytona_revalidate.py" "$PKG" 2>"$ERR")
    JSON=$(printf '%s\n' "$OUT" | tail -1)
    case "$JSON" in
        *'"stage": "daytona_error"'*|"")
            [ "$attempt" -lt 3 ] && { echo "(sandbox platform error, retrying $attempt/3)"; sleep 20; continue; }
            ;;
    esac
    break
done

"$PY" - "$JSON" "$ERR" <<'PY'
import json, sys
try:
    v = json.loads(sys.argv[1])
except Exception:
    print("VERDICT: error -- validator produced no verdict")
    print(open(sys.argv[2], errors='replace').read()[-1500:])
    raise SystemExit(2)
ok = bool(v.get("ok"))
stage = v.get("stage")
print(f"VERDICT: {'pass' if ok else 'fail'}   reward={v.get('reward')}   "
      f"solve_exit={v.get('solve_exit')}   stage={stage}")
if not ok:
    # `why` carries the exception the validator caught. Printing only `tail`
    # meant every failure that happened before the container ran showed an
    # empty report, and an empty report reads as "the platform is broken" --
    # which is how sessions were lost to a package the agent could have fixed.
    if v.get("why"):
        print(f"\n--- why ---\n{v['why']}")
    print("\n--- what the run printed (tail) ---")
    print((v.get("tail") or "(empty)")[-4000:])
    if stage == "package_error":
        print("\nThis is the package, not the platform: a file the harness "
              "needs is missing or malformed, and retrying will not change it. "
              "Restore or add what the message names, then run this again.")
    elif stage == "daytona_error":
        print("\nThe sandbox platform failed before the package was built. "
              "This one is not yours to fix; it was already retried.")
    else:
        print("\nFix the task so the reference solution scores 1.0, then run "
              "this again.")
raise SystemExit(0 if ok else 1)
PY
