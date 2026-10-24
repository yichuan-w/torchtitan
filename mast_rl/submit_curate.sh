#!/bin/bash
# Submit the Qwen3-32B R2E pass-rate DATA-WASHING job to MAST (stage 1 of the
# curriculum). Every host runs a disjoint shard of curate_passrate.py: in-process
# vLLM (TP=8) serves the 32B policy, the Claude Code agent solves K samples per
# task in Daytona, and each (task,sample) binary grade is written to a shared
# bucket dir. Aggregate offline afterwards with aggregate_passrate.py to get the
# 20-70% band JSONL. See DATA_CURATION.md.
#
# Topology: reuses the base 32B recipe (1 controller + 1 trainer host) and adds
# SWE_NUM_GENERATORS generator hosts; with SWE_CURATE=1 NONE of them run the RL
# loop -- run.sh's SWE_CURATE branch makes each host wash one shard instead.
# So #shards = SWE_NUM_GENERATORS + 2 (controller + trainer + N generators).
#   full wash : SWE_NUM_GENERATORS=88  -> 90 hosts/shards, K=8, all 4578 tasks
#   validation: SWE_NUM_GENERATORS=2 SWE_CURATE_LIMIT=2 SWE_CURATE_K=4 -> 4 hosts, fast
#
# Prereqs:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...
#   bash mast_rl/submit_curate.sh                 # first submit (reinstalls torchtitan)
#   bash mast_rl/submit_curate.sh --no-reinstall  # subsequent submits
set -u

if [[ -z "${DAYTONA_API_KEY:-}" ]]; then
    echo "ERROR: export DAYTONA_API_KEY (app.daytona.io key) before submitting." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Make run.sh take the data-washing branch on every host (NOT the RL loop).
export SWE_CURATE=1
export SWE_CURATE_K="${SWE_CURATE_K:-8}"            # samples per task (pass-rate resolution ~1/K)
export SWE_TP="${SWE_TP:-8}"                        # vLLM TP per host (32B -> 8)
export SWE_CURATE_MODEL="${SWE_CURATE_MODEL:-/mnt/torchtrain_datasets/tree/qwen3/Qwen3-32B}"
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/swe_data/r2e_subset_4p5k.jsonl}"  # full R2E-Gym-Subset 4.5K
# SWE_CURATE_LIMIT caps tasks PER SHARD (0 = all); set small for a fast launcher
# validation. SWE_CURATE_OUT (bucket dir) defaults in run.sh to
# tree/yichuan/curate_runs/<job-name>.
[[ -n "${SWE_CURATE_LIMIT:-}" ]] && export SWE_CURATE_LIMIT
[[ -n "${SWE_CURATE_OUT:-}" ]] && export SWE_CURATE_OUT

# Daytona + agent harness knobs (same as the RL run).
export DAYTONA_API_KEY
export SWE_PROXY="${SWE_PROXY:-http://fwdproxy:8080}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-900}"
export SWE_EVAL_TIMEOUT_SEC="${SWE_EVAL_TIMEOUT_SEC:-400}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-22528}"
export SWE_BOOT_CONCURRENCY="${SWE_BOOT_CONCURRENCY:-8}"
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-24}"  # per-host active rollouts
export SHIM_PORT="${SHIM_PORT:-18031}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-2}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-3}"
SETTINGS_JSON="{\"permissions\":{\"defaultMode\":\"bypassPermissions\"},\"autoCompactEnabled\":true,\"autoCompactWindow\":16000}"
export SWE_CLAUDE_EXTRA_ARGS="${SWE_CLAUDE_EXTRA_ARGS:---settings '${SETTINGS_JSON}' --disable-slash-commands --disallowedTools WebFetch WebSearch Task Agent AskUserQuestion}"

# Curation needs no cross-host collectives (each host is independent); Socket is fine.
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"

# Base 32B recipe (1 trainer host) + N generator hosts = N+2 wash shards.
ARGS=(--config "${SWE_CONFIG:-rl_grpo_qwen3_32b_swe_r2e}")
ARGS+=(--num_generators "${SWE_NUM_GENERATORS:-88}")
exec bash "$(dirname "$0")/submit.sh" "${ARGS[@]}" "$@"
