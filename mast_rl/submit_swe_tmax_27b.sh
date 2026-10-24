#!/bin/bash
# Qwen3.5-27B tmax terminal-agent RL on MAST H100 -- the 27B counterpart of the 9B
# TerminalWorld run (submit_swe_tmax_9b.sh + submit_swe_tmax_9b_tb2_inline.sh).
#
# Recipe: rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2, derived from rl_grpo_qwen3_5_9b_tmax
# (NOT the swe_r2e-descended rl_grpo_qwen3_5_27b_tmax), so it keeps the 9B's tuned
# settings -- fp32 master params, fp32 AdamW, DPPO chunked loss, salt-KV weight sync,
# preserve_all_thinking -- with only the model-size-forced deltas:
#   - trainer FSDP-32 x TP-2 (world 64 = 8 hosts);
#   - generator TP-2, DP-4 (4 engines per 8-GPU host), gpu_memory_limit 0.8;
#   - loss re-chunked to 64.
# Everything not model-size-driven matches the 9B TerminalWorld run: Terminus-2,
# the TerminalWorld+SWE-Smith mix, 32x16 = 512 rollouts/step no-zero-std-drop, and
# inline Terminal-Bench 2.0 validation on a dedicated eval-generator host.
#
# See torchtitan/experiments/rl/examples/tmax/README_TERMINALWORLD_27B.md.
#
# Hosts: 1 controller + 8 trainer (FSDP-32 x TP-2) + 12 generator (DP-4 x TP-2)
#        + 1 eval_generator = 22.
#
# Prereqs:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...     # or leave ~/.daytona_key in place
#   # data staged on the bucket:
#   #   /mnt/torchtrain_datasets/tree/yichuan/rts_data/mix_tw859_swe_v2clean.jsonl
#   #   /mnt/torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl
#   bash mast_rl/submit_swe_tmax_27b.sh                 # first submit (reinstalls torchtitan)
#   bash mast_rl/submit_swe_tmax_27b.sh --no-reinstall  # subsequent submits
set -u
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2}"
export SWE_GDN=1
export SWE_DISABLE_CUSTOM_ALL_REDUCE="${SWE_DISABLE_CUSTOM_ALL_REDUCE:-1}"
export DAYTONA_API_KEY="${DAYTONA_API_KEY:-$(cat ~/.daytona_key 2>/dev/null)}"
# Unified TorchTitan GDN model inside vLLM (gen == trainer model code + weights).
# The wrapper now supports TP>1 (986708d0b), so the 27B's forced TP-2 stays on it.
export SWE_GEN_BACKEND="${SWE_GEN_BACKEND:-torchtitan_wrapper}"

# --- harness: Terminus-2, recipe context / turn budget (same as the 9B run) ---
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
export TMAX_AGENT="${TMAX_AGENT:-terminus}"
export TMAX_TERMINUS_MAX_TURNS="${TMAX_TERMINUS_MAX_TURNS:-120}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-63488}"
export TMAX_TURN_MAX_TOKENS="${TMAX_TURN_MAX_TOKENS:-32768}"
export TMAX_EXEC_TIMEOUT_SEC="${TMAX_EXEC_TIMEOUT_SEC:-120}"
export TMAX_EVAL_TIMEOUT_SEC="${TMAX_EVAL_TIMEOUT_SEC:-600}"
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-2400}"

# --- data: TerminalWorld pass (859) + SWE-Smith in_main_pool (1244) = 2103 mix ---
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/rts_data/mix_tw859_swe_v2clean.jsonl}"

# --- GRPO/async shape: 32 groups x 16 siblings = 512 rollouts/step, no zero-std drop ---
export SWE_NUM_GROUPS_PER_TRAIN_STEP="${SWE_NUM_GROUPS_PER_TRAIN_STEP:-32}"
export SWE_GROUP_SIZE="${SWE_GROUP_SIZE:-16}"
export SWE_DROP_ZERO_STD="${SWE_DROP_ZERO_STD:-0}"
export SWE_MAX_ACTIVE_GROUPS="${SWE_MAX_ACTIVE_GROUPS:-512}"
export SWE_TRAIN_STEPS="${SWE_TRAIN_STEPS:-150}"

# --- selection + throughput (same as the 9B run) ---
export SWE_SELECTION_WINDOW_GROUPS="${SWE_SELECTION_WINDOW_GROUPS-64}"
export SWE_MAX_BYPASS_GROUPS="${SWE_MAX_BYPASS_GROUPS-off}"
export SWE_STRICT_FIFO="${SWE_STRICT_FIFO:-0}"
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-768}"
export SWE_NUM_ROLLOUT_WORKERS="${SWE_NUM_ROLLOUT_WORKERS:-16}"
export TT_DAYTONA_CREATE_CONCURRENCY="${TT_DAYTONA_CREATE_CONCURRENCY:-8}"
export SWE_MAX_NUM_SEQS="${SWE_MAX_NUM_SEQS:-32}"
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-12}"
# 27B DCP is ~330GB/snapshot (~3x the 9B's 109GB); interval 20 over 150 steps
# writes ~2.3TB. Do not drop this to the 9B's 5 without the storage headroom.
export SWE_CKPT_INTERVAL="${SWE_CKPT_INTERVAL:-20}"

# --- Daytona sandbox resources (same as the 9B run) ---
export TT_DAYTONA_CPU="${TT_DAYTONA_CPU:-2}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-4}"
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-10}"
export TT_DAYTONA_HEARTBEAT_SEC="${TT_DAYTONA_HEARTBEAT_SEC:-180}"
export TT_DAYTONA_RPC_RETRIES="${TT_DAYTONA_RPC_RETRIES:-2}"

# --- inline Terminal-Bench 2.0 validation on a dedicated eval-generator host ---
# The eval generator inherits the recipe's generator parallelism, so it is also
# DP-4 x TP-2 (8 GPUs = 1 host), not the 9B's DP-8 x TP-1.
export SWE_TB2_VAL_DATA="${SWE_TB2_VAL_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tb2_eval.jsonl}"
export SWE_VAL_INTERVAL="${SWE_VAL_INTERVAL:-20}"
export SWE_VAL_SAMPLES="${SWE_VAL_SAMPLES:-89}"
export SWE_NUM_EVAL_GENERATORS="${SWE_NUM_EVAL_GENERATORS:-1}"
export SWE_NUM_EVAL_ROLLOUT_WORKERS="${SWE_NUM_EVAL_ROLLOUT_WORKERS:-4}"
export SWE_EVAL_ROLLOUT_CONCURRENCY="${SWE_EVAL_ROLLOUT_CONCURRENCY:-128}"
# Eval-only gpu_memory_limit (training generators stay at the config's 0.8). The
# b44113 run's eval generator OOM'd (DP gloo timeout cascade) because TB-2.0's long
# multi-turn episodes filled KV to ~0.75 and a large chunked-prefill's activation
# spike then blew the 80GB card. 0.7 leaves ~8GB of activation headroom on the eval
# engines without shrinking training-generator KV.
export SWE_EVAL_GPU_MEMORY_LIMIT="${SWE_EVAL_GPU_MEMORY_LIMIT:-0.7}"

# Keep idle generator meshes warm across long 27B steps + async eval gaps (a naive
# 6h timeout let an idle mesh's next weight pull die on a gloo "connection closed").
export MONARCH_MESH_ORPHAN_TIMEOUT="${MONARCH_MESH_ORPHAN_TIMEOUT:-24h}"
export MONARCH_MESH_PROC_SPAWN_MAX_IDLE="${MONARCH_MESH_PROC_SPAWN_MAX_IDLE:-24h}"

export WANDB_RUN_NAME="${WANDB_RUN_NAME:-mix-tw-swe-27b-terminus-bs32-nodrop}"
export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_27b_mix_tw_swe}"

# 27B GDN weights on the bucket (same dir every 27B run uses; the qwen3_5 27B arch
# loads them). Override with GDN_HF_ASSETS if a Qwen3.5-27B dir is staged separately.
GDN_ASSETS="${GDN_HF_ASSETS:-/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.6-27B}"

echo "config=${SWE_CONFIG} steps=${SWE_TRAIN_STEPS} val_interval=${SWE_VAL_INTERVAL}"
echo "groups=${SWE_NUM_GROUPS_PER_TRAIN_STEP} x ${SWE_GROUP_SIZE} conc=${SWE_ROLLOUT_CONCURRENCY} gens=${SWE_NUM_GENERATORS}+1eval"
echo "hosts=1 controller + 8 trainer + ${SWE_NUM_GENERATORS} generator + ${SWE_NUM_EVAL_GENERATORS} eval"
exec bash "$(dirname "$0")/submit_swe.sh" --hf_assets_path "${GDN_ASSETS}" "$@"
