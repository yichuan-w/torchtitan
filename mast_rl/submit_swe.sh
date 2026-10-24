#!/bin/bash
# Submit the Qwen3-32B SWE-R2E coding-agent (Claude Code + Daytona) RL job to MAST.
#
# Default config rl_grpo_qwen3_32b_swe_r2e_fsdp16: async RL loop, 1 controller +
# 2 trainer (FSDP-16) + 3 generator (TP=8) = 6 H100 hosts. The controller runs the
# SWE branch of run.sh: no retriever; it boots a Daytona sandbox per rollout, runs
# Claude Code against the on-box Anthropic adapter, and grades the patch with the
# R2E hidden tests. Daytona egress goes through fwdproxy. Rollout collection
# overlaps training (max_offpolicy_steps=2) so each train step is short.
#
# Prereqs:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...     # app.daytona.io key
#   bash mast_rl/submit_swe.sh                 # first submit (reinstalls torchtitan)
#   bash mast_rl/submit_swe.sh --no-reinstall  # subsequent submits
set -u

if [[ -z "${DAYTONA_API_KEY:-}" ]]; then
    echo "ERROR: export DAYTONA_API_KEY (app.daytona.io key) before submitting." >&2
    exit 1
fi

# The local launcher (launcher.py) parses the recipe via ``import
# mast_rl.config_registry``; put the repo root on PYTHONPATH so mast_rl resolves.
REPO_ROOT="${TORCHTITAN_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Selects run.sh's SWE controller branch + configures the harness; mast.py forwards
# these to the controller role's env. Toolchain + R2E data ship in mast_rl/swe_assets.
export SWE_RUN=1
# Agent mode: host_loop (lean host-side ReAct, ~1k system prompt) NOT the in-sandbox
# Claude Code bridge. The bridge's real Claude Code CLI ships a ~20k-token tool suite
# that starves the 24576 context AND its tool calls for a non-Claude policy are not
# executed (episodes stall at ~3 turns). Eval on Qwen3.6-27B: bridge 0/25 vs
# host_loop 6/25 -- see project_swe_harness_bridge_starvation. Overridable.
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/swe_data/r2e_subset_4p5k.jsonl}"  # full R2E-Gym-Subset 4.5K (SkyRL/DeepSWE data) on the bucket
export DAYTONA_API_KEY
export SWE_PROXY="${SWE_PROXY:-http://fwdproxy:8080}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-900}"
export SWE_EVAL_TIMEOUT_SEC="${SWE_EVAL_TIMEOUT_SEC:-400}"
# Agent token budget. Kept ~2k under the 32B recipe's 24576 model context so the
# adapter's per-turn cap (budget - prompt_len) never overflows vLLM max_model_len.
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-22528}"
export SWE_BOOT_CONCURRENCY="${SWE_BOOT_CONCURRENCY:-32}"
# Profiling 91a0cf: generators are ~96% IDLE (Running ~3 reqs, KV ~4%) and Daytona
# Tier 4 has headroom (164 transient 401s/5h, retried) -- the coding agent is
# latency-bound (most wall-clock is tool-exec over the file-relay bridge, not
# generation), so only ~24 of 48 rollouts generate at once. Push concurrency hard;
# the generators + Daytona absorb it. Cap = the controller's single asyncio loop
# driving the bridges. Keep bridge polls under the Tier-4 API rate (50k/min = 833/s):
# at C=128 worst-case all-polling = 128/0.2 = 640/s (typically ~half poll), safe.
# poll 0.3 cuts each rollout's poll rate to 3.3/s so we can run MORE concurrent
# rollouts under the Tier-4 833/s API budget: at C=256, typically ~half poll at once
# = ~427/s (safe); worst-case all-polling 853/s causes occasional 429s (retried).
# Higher C keeps more rollouts mid-generation so the idle generators get used.
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-256}"
export SWE_BRIDGE_POLL_INTERVAL="${SWE_BRIDGE_POLL_INTERVAL:-0.3}"
export SHIM_PORT="${SHIM_PORT:-18031}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-2}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-3}"
# fold(autoCompact) OFF; lean tool set (the init registered 20 tools, ~15 useless
# for SWE -- cron/worktree/plan-mode/task/workflow/notebook -- bloating the ~16k
# turn-0 prompt + giving the model wrong tools to pick); append-system-prompt nudges
# the two failures seen in traces (guessing file paths, Edit-without-Read).
SETTINGS_JSON="{\"permissions\":{\"defaultMode\":\"bypassPermissions\"},\"autoCompactEnabled\":false}"
SWE_SYS_PROMPT="You are fixing a real bug in the repo at /testbed. Find the right file by searching with Bash (grep -rn on class/function names from the issue, or find) -- do NOT guess file paths. ALWAYS Read a file before you Edit it. Make minimal precise edits; if an Edit fails because old_string does not match, Read the file again and copy the exact text, or rewrite the file with Write. After editing, run the repo tests with Bash to verify."
export SWE_CLAUDE_EXTRA_ARGS="${SWE_CLAUDE_EXTRA_ARGS:---settings '${SETTINGS_JSON}' --disable-slash-commands --append-system-prompt '${SWE_SYS_PROMPT}' --disallowedTools NotebookEdit CronCreate CronDelete CronList ScheduleWakeup EnterPlanMode ExitPlanMode EnterWorktree ExitWorktree TaskCreate TaskGet TaskList TaskOutput TaskStop TaskUpdate Workflow WebFetch WebSearch AskUserQuestion}"

# Async + CPU-staged weight sync (the new torchstore: SHM PUT + MonarchRDMA GET).
# IB (RoCE) accelerates the multi-host FSDP-16 all-gather/reduce-scatter AND the
# MonarchRDMA weight-sync GET (needs rdma-core==60.0, already pinned in the env).
export NCCL_TRANSPORT="${NCCL_TRANSPORT:-IB}"

# num_generators defaults to the recipe's value (3 for _fsdp16); set
# SWE_NUM_GENERATORS to override. Model is env-selectable via SWE_CONFIG, e.g.
#   SWE_CONFIG=rl_grpo_qwen3_32b_swe_r2e_fsdp24 bash mast_rl/submit_swe.sh
ARGS=(--config "${SWE_CONFIG:-rl_grpo_qwen3_32b_swe_r2e_fsdp16}")
if [[ -n "${SWE_NUM_GENERATORS:-}" ]]; then
    ARGS+=(--num_generators "${SWE_NUM_GENERATORS}")
fi
exec bash "$(dirname "$0")/submit.sh" "${ARGS[@]}" "$@"
