#!/bin/bash
# Run the reference rewriter (this repository's evolve loop) over one job, in
# the foreground. Any other rewriter replaces this script; README.md says what
# it reads and how it publishes. It runs through rewriter_client.py, which
# wakes the loop on the scheduler's group_done and gives each fold to the pool;
# with no scheduler (SWE_BENCH_SCHEDULER=0) it is the plain file-based loop.
# REWRITER_MODE=ladder runs ladder_rewriter.py instead: the same rewrites, kept
# side by side as versions of the pool (README.md, "The ladder rewriter").
# REWRITER_MODE=teacher runs teacher_rewriter.py: one codex thread, called before training
# (teacher_rewriter.py <job> --prepare) and after every group close to all failed or all
# passed, keeps the pool (README.md, "The teacher rewriter").
# REWRITER_MODE=interventions runs intervention_rewriter.py: every version the
# original plus a named set of interventions (README.md, "The intervention
# rewriter").
#
#   REWRITER_ENV=<env file> EVOLVE_WORK_ROOT=<node-local dir> [REWRITER_MODE=ladder|interventions] \
#       ./start_reference_rewriter.sh <job dir>
#
# REWRITER_ENV holds the loop's settings and credentials; see
# reference_rewriter.env.example.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB="$(cd "${1:?usage: start_reference_rewriter.sh <job dir>}" && pwd)"
[ -f "$JOB/job.json" ] || { echo "$JOB has no job.json; make it with bench.py new" >&2; exit 2; }
: "${REWRITER_ENV:?set REWRITER_ENV (see reference_rewriter.env.example)}"
: "${EVOLVE_WORK_ROOT:?set EVOLVE_WORK_ROOT to a node-local scratch directory}"
set -a; . "$REWRITER_ENV"; set +a
export TRL_BASE="$JOB"
export TRL_TT="${TRL_TT:-$(cd "$HERE/../../../../../.." && pwd)}"
export PYTHONPATH="$TRL_TT"
: "${TRL_VENV:=/scratch/gpfs/TRIDAO/al9080/titan-rl}"
mkdir -p "$EVOLVE_WORK_ROOT"
BENCH="$TRL_TT/torchtitan/experiments/rl/examples/tmax/overfit_bench"
case "${REWRITER_MODE:-reference}" in
    reference)
        exec "$TRL_VENV/bin/python" "$BENCH/rewriter_client.py" \
            reference "$JOB" --interval 120 --workers 16 >> "$JOB/evolution/loop.log" 2>&1 ;;
    ladder)
        exec "$TRL_VENV/bin/python" "$BENCH/ladder_rewriter.py" \
            "$JOB" --workers 16 >> "$JOB/evolution/loop.log" 2>&1 ;;
    teacher)
        exec "$TRL_VENV/bin/python" "$BENCH/teacher_rewriter.py" \
            "$JOB" >> "$JOB/evolution/loop.log" 2>&1 ;;
    interventions)
        exec "$TRL_VENV/bin/python" "$BENCH/intervention_rewriter.py" \
            "$JOB" --workers 16 >> "$JOB/evolution/loop.log" 2>&1 ;;
    *) echo "REWRITER_MODE is reference, ladder, interventions or teacher, not $REWRITER_MODE" >&2; exit 2 ;;
esac
