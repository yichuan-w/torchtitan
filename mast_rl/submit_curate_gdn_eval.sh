#!/bin/bash
# Eval-only (pass-rate) MAST job for Qwen3.6-27B (Gated DeltaNet) with the
# host_loop harness at 40K context -- reproduce/measure accuracy with the three
# harness fixes (empty-spin break, empty-diff=0, XML tool-call recovery).
#
# curate_passrate on every host (SWE_CURATE=1), NO training loop. host_loop +
# qwen3.6 renderer + vLLM-native GDN prefill + 40K context + temp=1.0 (matches
# the historical run that hit ~24% on the orange3 subset). Broad strided sample
# across repos for a representative number.
#
# Prereq: conda activate rlmast; export DAYTONA_API_KEY (or ~/.dtn_key present).
#   bash mast_rl/submit_curate_gdn_eval.sh            # first submit (reinstalls torchtitan -> ships fixes)
set -u

export DAYTONA_API_KEY="${DAYTONA_API_KEY:-$(cat /home/yichuan/.dtn_key 2>/dev/null | tr -d '[:space:]')}"

# GDN generation stack (mirrors the training submit).
export SWE_CONFIG="${SWE_CONFIG:-rl_grpo_qwen3_5_27b_swe_r2e}"   # region=eag entitlement + host_type
export SWE_GDN=1
export SWE_RENDERER="${SWE_RENDERER:-qwen3.6}"
export SWE_AGENT_MODE="${SWE_AGENT_MODE:-host_loop}"
export SWE_VLLM_ADDITIONAL_CONFIG="${SWE_VLLM_ADDITIONAL_CONFIG:-{\"gdn_prefill_backend\": \"triton\"}}"
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1
export SWE_CURATE_MODEL="${SWE_CURATE_MODEL:-/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.6-27B}"
export SWE_PROMPT_DATA="${SWE_PROMPT_DATA:-/mnt/torchtrain_datasets/tree/yichuan/swe_data/r2e_subset_4p5k.jsonl}"

# 40K context (the setting that historically reached ~24%; the 24K training cap
# is what starves the rollouts). TP=4 matches the validated GDN generator.
export SWE_TP="${SWE_TP:-4}"
export SWE_MAX_MODEL_LEN="${SWE_MAX_MODEL_LEN:-40960}"
export SWE_MAX_CONTEXT_LEN="${SWE_MAX_CONTEXT_LEN:-36864}"
export SWE_MAX_GEN_LEN="${SWE_MAX_GEN_LEN:-4096}"

# Sample: 4 hosts (controller+trainer+2 gen) each wash a strided shard.
export SWE_CURATE_K="${SWE_CURATE_K:-1}"          # 1 sample/task (pass/fail)
export SWE_CURATE_LIMIT="${SWE_CURATE_LIMIT:-60}" # per shard -> ~240 tasks total
export SWE_NUM_GENERATORS="${SWE_NUM_GENERATORS:-2}"
export SWE_CURATE_OUT="${SWE_CURATE_OUT:-/mnt/torchtrain_datasets/tree/yichuan/curate_runs/q36_gdn_eval_40k_fixed}"

exec bash "$(dirname "$0")/submit_curate.sh" "$@"
