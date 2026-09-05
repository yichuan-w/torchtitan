#!/bin/bash
# The evolve loop's environment for one experiment root, shared by
# restart_evolve.sh (the unit) and a foreground dev round. Source it with
# TRL_PROFILE and TRL_BASE set; it exports everything evolve_ondella.py reads
# and leaves PY, TT, EVO, UNIT and LOG set for the caller.
#
#   TRL_PROFILE=andy TRL_BASE=<root> TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2 \
#     TT_DAYTONA_DISK_GB=2 . evolveloop_env.sh
#
# The three TT_DAYTONA_* are the trainer's fleet defaults and have to match its
# launch env: a row declaring no daytona_* of its own is verified at this size.
# No apostrophe in a ${var:?word} message: the word is quote-parsed, so a lone
# ' opens a string and the whole file stops parsing.
: "${TRL_PROFILE:?name the profile whose checkout this is; see runbook/profiles/}"
: "${TT_DAYTONA_CPU:?the per-sandbox vCPU default the trainer runs with}"
: "${TT_DAYTONA_MEM_GB:?the per-sandbox memory default the trainer runs with, GiB}"
: "${TT_DAYTONA_DISK_GB:?the per-sandbox disk default the trainer runs with, GiB}"
# The profile says which checkout runs, and that is the answer -- not where
# this file happens to sit. Two people launch from one account here and every
# path on the box carries the same name, so a script that infers its checkout
# from its own location runs whichever tree it was copied into and says
# nothing. Locating the profile is the only thing the script's position is used
# for; after that TRL_TT is the profile's, and so is everything derived from
# it. TRL_BASE is the experiment root: the ambient one wins, the profile's
# line is the default.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROFILE=$HERE/../../runbook/profiles/$TRL_PROFILE.env
[ -f "$PROFILE" ] || { echo "[env] no profile $TRL_PROFILE at $PROFILE" >&2; return 2 2>/dev/null || exit 2; }
TT=$(sed -n 's/^TRL_TT=//p' "$PROFILE")
[ -n "$TT" ] && [ -d "$TT" ] || { echo "[env] profile $TRL_PROFILE names TRL_TT=$TT, which is not a directory" >&2; return 2 2>/dev/null || exit 2; }
TRL_BASE=${TRL_BASE:-$(sed -n 's/^TRL_BASE=//p' "$PROFILE")}
[ -n "$TRL_BASE" ] && [ -d "$TRL_BASE" ] || { echo "[env] TRL_BASE=$TRL_BASE is not a directory" >&2; return 2 2>/dev/null || exit 2; }
EVO=$TT/torchtitan/experiments/rl/examples/tmax/evolution
case "$HERE" in "$TT"/*) ;; *) echo "[env] running $TT (profile $TRL_PROFILE); this script was invoked from $HERE" >&2 ;; esac
# The training venv: the loop's interpreter, and the one daytona_revalidate
# and ./sandbox run in. TRL_VENV is the name the runbook and launch_9b.sh use.
TRL_VENV=${TRL_VENV:-/scratch/gpfs/TRIDAO/al9080/titan-rl}
PY=$TRL_VENV/bin/python
UNIT=evolve-$(basename "$TRL_BASE")
LOG=$TRL_BASE/evolution/loop.log

# Without the daytona env, structural revalidation fails `no_docker`; without
# SYNTH_ENV_FILE the loop dies at startup with `no OPENAI_API_KEY`.
# shellcheck disable=SC1090
. ~/.config/daytona/env
export SYNTH_ENV_FILE=${SYNTH_ENV_FILE:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/.synth_env}
export TRL_PROFILE TRL_BASE TRL_VENV TRL_TT=$TT PYTHONPATH=$TT
# Agentic retune with the full rollout records as files, no chat fallback; a
# vague hint level, since specific where-to-look hints teach hint-following.
# The easier arm stays off: 0/k signals get a `deferred` ledger line and are
# replayed when it is turned on.
export SWE_RETUNE_AGENT=codex SWE_SIMPLIFY_HINT=vague SWE_EVOLVE_SIMPLIFY=0
export TT_DAYTONA_CPU TT_DAYTONA_MEM_GB TT_DAYTONA_DISK_GB
mkdir -p "$TRL_BASE/evolution"
