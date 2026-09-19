#!/usr/bin/env bash
# Restart the hip trainer on the next checkpoint so a code change reaches the
# rollout side without losing steps: wait for <run>/trainer/checkpoint/step-N
# to finish writing (its .metadata lands last), stop the trainer's process
# group, relaunch with RL_RESUME_FROM=<run>. The loop is untouched.
#   restart_at_step.sh <run name> <step>
# The trainer is found by its exact command line, never by a loose pattern:
# a `pgrep -f torchtitan.experiments.rl.train` also matches any shell whose
# own command line mentions that string, such as a monitoring loop.
set -uo pipefail
RUN=${1:?run name}; STEP=${2:?step}
ROOT=/data/yichuan_wang/trl; BASE=$ROOT/work/titan/tmax-v2-evolve
PID=$(pgrep -u "$(id -un)" -f "^$ROOT/venv/bin/python -u -m torchtitan.experiments.rl.train " | head -1)
[ -n "$PID" ] || { echo "no trainer process"; exit 1; }
[ "$(pgrep -u "$(id -un)" -fc "^$ROOT/venv/bin/python -u -m torchtitan.experiments.rl.train ")" = 1 ] || { echo "more than one trainer process"; exit 1; }
CK=$BASE/runs/$RUN/trainer/checkpoint/step-$STEP
echo "[$(date -u +%FT%TZ)] waiting for $CK/.metadata"
until [ -f "$CK/.metadata" ]; do sleep 30; done
sleep 90
echo "[$(date -u +%FT%TZ)] checkpoint step-$STEP complete; stopping trainer pid $PID"
PG=$(ps -o pgid= -p "$PID" | tr -d ' ')
[ "$PG" = "$PID" ] || { echo "pgid $PG != pid $PID; refusing"; exit 1; }
kill -TERM -- "-$PG"; for _ in $(seq 1 45); do pgrep -g "$PG" >/dev/null || break; sleep 2; done
pgrep -g "$PG" >/dev/null && { kill -KILL -- "-$PG"; sleep 3; }
until [ "$(rocm-smi --showmemuse 2>/dev/null | grep -c 'VRAM%): 0')" = 8 ]; do sleep 3; done
echo "[$(date -u +%FT%TZ)] gpus free; relaunching with RL_RESUME_FROM=$RUN"
cd $ROOT && RL_RESUME_FROM=$RUN bash scripts/titan_tmax_evolve_host.sh
echo "[$(date -u +%FT%TZ)] done"
