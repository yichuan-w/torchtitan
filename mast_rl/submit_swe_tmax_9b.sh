#!/bin/bash
# Submit the Qwen3.5-9B (Gated DeltaNet hybrid, text-only) AI2 tmax terminal-agent
# RL job to MAST -- the paper's TMax-9B target.
#
# Same shape as submit_swe_tmax.sh (the 27B tmax launch) but repointed at the 9B
# config + 9B GDN weights: trainer FSDP-8 + vLLM-native GDN generator TP-1 (DP
# controller, host_loop agent as ROOT, drop_zero_std True (paper's
# filter_zero_std_samples), group_size 32. The controller boots a Daytona sandbox
# per rollout from the task's public docker image, runs the host_loop agent, then
# grades in place with `bash /tests/test.sh` -> /logs/verifier/reward.txt. All MAST
# plumbing (roles, fwdproxy egress, torchstore weight sync, host counts) is
# inherited from submit_swe.sh unchanged.
#
# SWE_GDN=1 makes run.sh point CUDA_HOME at the pip nvidia/cu13 ptxas 13.2 for the
# FLA/triton GDN kernels; the generator runs gdn_prefill_backend=triton + fp32
# lm_head. num_generators defaults to 6 (the paper's count); the PCI 96-quota lets
# you push higher via SWE_NUM_GENERATORS=12 (each generator is one MAST host).
#
# Prereqs:
#   conda activate rlmast
#   export DAYTONA_API_KEY=dtn_...     # app.daytona.io key
#   # tmax JSONL staged on the bucket (see prepare_tmax_data.py):
#   #   /mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_train.jsonl (14601 tasks)
#   #   /mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_smoke.jsonl  (5 tasks, bootstrap)
#   bash mast_rl/submit_swe_tmax_9b.sh                 # first submit (reinstalls torchtitan)
#   bash mast_rl/submit_swe_tmax_9b.sh --no-reinstall  # subsequent submits
set -u
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_9b_tmax}"
export SWE_GDN=1
# Disable vLLM custom all-reduce on the GDN generator. The generator is TP=1 (48
# single-GPU engines, DP-routed by the controller), so there is no TP all-reduce;
# but vLLM's multi-GPU device-communicator init still imports flashinfer's cuda_ipc
# (CudaRTLibrary), which intermittently loads tilelang's libcudart_stub.so ->
# "undefined symbol: cudaDeviceReset" -> generator init crash -> job DEAD (seen on
# job ...a049ca). This flag avoids that import path. The working GDN eval recipe
# (submit_curate_gdn_eval.sh) already sets it.
export SWE_DISABLE_CUSTOM_ALL_REDUCE="${SWE_DISABLE_CUSTOM_ALL_REDUCE:-1}"
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
# tmax terminal-agent JSONL (produced by prepare_tmax_data.py) on the bucket.
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_train.jsonl}"
# Timeouts aligned to the official TMax env (swerl_vanillux_sandbox.py): per-bash
# command 120s (kills a hung command at 2 min so slow turns do not drag the episode)
# and test.sh verifier 600s (the _MIN_TEST_TIMEOUT_S floor). These replace the old
# 300s/900s values, which were 2.5x/1.5x the official caps and stretched step time.
export TMAX_EXEC_TIMEOUT_SEC="${TMAX_EXEC_TIMEOUT_SEC:-120}"
export TMAX_EVAL_TIMEOUT_SEC="${TMAX_EVAL_TIMEOUT_SEC:-600}"
# Reward source. Default 0 = the tmax/RTS contract: the verifier's binary
# reward.txt (1 only when every test passed). =1 trains on the same verifier run's
# CTRF per-test pass fraction instead (config_registry -> reward_mode=dense), which
# costs one extra sandbox read per graded rollout. Validation stays binary either
# way, so its solve rate stays comparable across runs. Partial credit on RTS is
# reachable without solving the task (check_01 only asserts an artifact exists), so
# watch rollout/ctrf_test_pass_frac against rollout/sparse_reward_mean: the former
# rising while the latter is flat means partial-credit farming, not learning.
export SWE_REWARD_DENSE="${SWE_REWARD_DENSE:-0}"
# Per-test CTRF metrics WITHOUT changing the reward (implied by SWE_REWARD_DENSE=1).
export TMAX_CTRF_DIAGNOSTICS="${TMAX_CTRF_DIAGNOSTICS:-0}"
# Per-agent wall-clock (default 2400s = 40 min). The controller-side whole-rollout
# reclaim guard is time_budget + eval + 300, so this also bounds how long a stuck
# rollout can hold an off-policy buffer slot before being reclaimed. A prior
# effectively-infinite value (100000s) pushed that guard to ~28h, letting a hung
# Daytona sandbox clog an active slot for up to a day and starve the trainer. The
# 40-minute default gives legitimate long episodes more room while keeping the
# reclaim guard bounded at 55 minutes with the default verifier timeout.
export SWE_TIME_BUDGET_SEC="${SWE_TIME_BUDGET_SEC:-2400}"
# Full TMax-9B context (65536). The adapter session budget stays ~2k under the
# model ceiling (65536) with room for one 16384-token generation, matching the
# proven 22528-under-24576 ratio; the batcher seq_len (65536) exceeds it so a full
# episode is never dropped during packing.
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-63488}"
# Fresh dump dir so a new job never tries to resume a prior run's checkpoint
# (the shared mast_runs/<config> default caused a dataloader-state resume collision).
export SWE_DUMP_DIR="${SWE_DUMP_DIR:-/mnt/torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax}"
# The shared Daytona account currently has 5000 vCPU, 20000 GiB memory, and
# 25000 GiB storage. At 2 vCPU and 4 GiB memory per sandbox, TMax concurrency is
# well within those limits, but live usage is shared and must still be checked
# before raising rollout concurrency.
export TT_DAYTONA_CPU="${TT_DAYTONA_CPU:-2}"
export TT_DAYTONA_MEM_GB="${TT_DAYTONA_MEM_GB:-4}"
# Daytona stores every command session and its raw logs under
# /root/.daytona/sessions for the lifetime of a rollout. A 5 GiB allocation left
# too little headroom over the task image for some long multi-turn rollouts.
# Use 10 GiB by default and raise individual disk-heavy tasks through their JSONL
# metadata. Concurrent command/log RPC and fs-upload failures are tracked
# separately because they are not proven to be caused by ENOSPC.
export TT_DAYTONA_DISK_GB="${TT_DAYTONA_DISK_GB:-10}"
# Keep long in-process generation turns alive despite Daytona's 10-minute idle
# auto-stop, and retry only provider RPCs whose operation is idempotent.
export TT_DAYTONA_HEARTBEAT_SEC="${TT_DAYTONA_HEARTBEAT_SEC:-180}"
export TT_DAYTONA_RPC_RETRIES="${TT_DAYTONA_RPC_RETRIES:-2}"
# W20 retains a meaningful anti-greedy prefix while the H7 trace replay cuts
# modeled window blocking by 59% versus W16. Keep the global max-bypass stall off
# until TMax has a hard group-RPC timeout or reliable cross-worker cancellation handshake.
export SWE_SELECTION_WINDOW_GROUPS="${SWE_SELECTION_WINDOW_GROUPS-20}"
export SWE_MAX_BYPASS_GROUPS="${SWE_MAX_BYPASS_GROUPS-off}"
export SWE_STRICT_FIFO="${SWE_STRICT_FIFO:-0}"
# Rollout concurrency remains independent of group selection. Split the proven
# C512 pool over 16 worker processes so each worker owns 32 sibling slots.
export SWE_ROLLOUT_CONCURRENCY="${SWE_ROLLOUT_CONCURRENCY:-512}"
export SWE_NUM_ROLLOUT_WORKERS="${SWE_NUM_ROLLOUT_WORKERS:-16}"
export TT_DAYTONA_CREATE_CONCURRENCY="${TT_DAYTONA_CREATE_CONCURRENCY:-8}"
# This is a per-engine decode batching ceiling, not a reservation. Keeping it at
# 32 avoids an artificially small derived ceiling while vLLM still admits only
# requests that fit the available KV cache.
# `-` not `:-`: an empty value (set by the MoE launchers) means "let the controller
# derive the per-engine ceiling from the rollout pool"; only a truly unset var gets 32.
export SWE_MAX_NUM_SEQS="${SWE_MAX_NUM_SEQS-32}"
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-6}"
GDN_ASSETS="${GDN_HF_ASSETS:-/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.5-9B}"
exec bash "$(dirname "$0")/submit_swe.sh" --hf_assets_path "${GDN_ASSETS}" "$@"
