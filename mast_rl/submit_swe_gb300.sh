#!/bin/bash
# Submit the Qwen3-32B SWE-R2E coding-agent (Claude Code + Daytona) RL job to MAST
# on **GB300** (Grace ARM64, 4 GPU/host, ~278GB/GPU).
#
# 1 controller + 1 trainer (FSDP-4 bf16) + 1 generator (TP=4) = 3 GB300 hosts. The
# recipe carries the GB300 placement (host_type=gb300, region=lco,
# rm_attribution=msl_infra_hw_enab_agentrl), so no host flags are needed here.
#
# REQUIRES an aarch64 build host + the aarch64 env (build_conda_aarch64.sh): the x86
# rlmast pack cannot exec on GB300. Run this FROM the aarch64 host.
#
# Prereqs (on the aarch64 host):
#   conda activate rlmast_arm
#   export DAYTONA_API_KEY=dtn_...     # app.daytona.io key
#   bash mast_rl/submit_swe_gb300.sh                 # first submit (reinstalls torchtitan)
#   bash mast_rl/submit_swe_gb300.sh --no-reinstall  # subsequent submits
set -u

# Submit from the aarch64 env (submit.sh checks CONDA_DEFAULT_ENV == RLMAST_ENV).
export RLMAST_ENV="${RLMAST_ENV:-rlmast_arm}"

if [[ -z "${DAYTONA_API_KEY:-}" ]]; then
    echo "ERROR: export DAYTONA_API_KEY (app.daytona.io key) before submitting." >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Selects run.sh's SWE controller branch + configures the harness; mast.py forwards
# these to the controller role's env. The Claude-Code/node toolchain + R2E data ship
# in mast_rl/swe_assets; the toolchain runs INSIDE the (x86 cloud) Daytona sandbox,
# so the x64 tarballs are correct even though the GB300 controller is aarch64.
export SWE_RUN=1
export DAYTONA_API_KEY
export SWE_PROXY="${SWE_PROXY:-http://fwdproxy:8080}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-900}"
export SWE_EVAL_TIMEOUT_SEC="${SWE_EVAL_TIMEOUT_SEC:-400}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-20480}"
export SWE_BOOT_CONCURRENCY="${SWE_BOOT_CONCURRENCY:-2}"
export SHIM_PORT="${SHIM_PORT:-18031}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-2}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-5}"
SETTINGS_JSON="{\"permissions\":{\"defaultMode\":\"bypassPermissions\"},\"autoCompactEnabled\":true,\"autoCompactWindow\":16000}"
export SWE_CLAUDE_EXTRA_ARGS="${SWE_CLAUDE_EXTRA_ARGS:---settings '${SETTINGS_JSON}' --disable-slash-commands --disallowedTools WebFetch WebSearch Task Agent AskUserQuestion}"

# CPU-staged weight sync (recipe sets weight_sync_direct_rdma=False) -> plain Socket
# NCCL, no rdma-core dependency. The SWE rollout dominates wall-clock.
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-Socket}"

exec bash "$(dirname "$0")/submit.sh" \
    --config rl_grpo_qwen3_32b_swe_r2e_gb300 --num_generators 1 "$@"
