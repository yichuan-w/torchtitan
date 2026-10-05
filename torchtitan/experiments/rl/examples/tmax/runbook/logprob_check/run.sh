#!/bin/bash
# Offline trainer-vs-generator logprob check for the tmax 9B generator. Needs 3 free GPUs.
#
#   bash run.sh <model_dir> <rollout_samples.jsonl> <out_dir>
#
# 64 tmax trajectories are replayed for 8 turns through vLLM under three configs, and the
# decode logprobs are re-scored with the trainer's TorchTitan model:
#   default : vLLM's default compile (inductor VLLM_COMPILE, custom_ops=['none'])
#   rope    : same, but rotary_embedding runs as its CUDA op (what the generator uses now)
#   eager   : enforce_eager
# Prints KL k1 / mean|d| / max per config, plus an environment fingerprint. On CoreWeave
# B300 (2026-10-05) the result was: default k1 0.0017, rope 0.0002, eager 0.00009.
# The rollouts can be any tmax run's runs/<run>/trainer/rollout_samples.jsonl.
set -euo pipefail
MODEL=$1 ROLLOUTS=$2 OUT=$3
HERE=$(cd "$(dirname "$0")" && pwd)
TT_ROOT=$(cd "$HERE/../../../../../../.." && pwd)
mkdir -p "$OUT"
export PYTHONPATH=$TT_ROOT${PYTHONPATH:+:$PYTHONPATH} VLLM_ENABLE_V1_MULTIPROCESSING=0 VLLM_DISABLE_COMPILE_CACHE=1
GPUS=(${CUDA_VISIBLE_DEVICES//,/ })
[ ${#GPUS[@]} -ge 3 ] || GPUS=(0 1 2)

CUDA_VISIBLE_DEVICES=${GPUS[0]} python "$HERE/envfp.py" > "$OUT/envfp.txt" 2>/dev/null || true
gen() {  # gpu tag [args]
    local g=$1 t=$2; shift 2
    CUDA_VISIBLE_DEVICES=$g TORCHINDUCTOR_CACHE_DIR=$OUT/inductor_$t python "$HERE/vllm_multiturn.py" \
        --model "$MODEL" --rollouts "$ROLLOUTS" --out "$OUT/$t.json" "$@" > "$OUT/gen_$t.log" 2>&1
}
gen "${GPUS[0]}" default &
gen "${GPUS[1]}" rope --compile_json '{"custom_ops": ["none", "+rotary_embedding"]}' &
gen "${GPUS[2]}" eager --eager 1 &
wait
i=0
for t in default rope eager; do
    CUDA_VISIBLE_DEVICES=${GPUS[$i]} python "$HERE/tt_score.py" "$OUT/$t.seqs.jsonl" --model "$MODEL" \
        > "$OUT/score_$t.log" 2>&1 &
    i=$((i + 1))
done
wait
cat "$OUT/envfp.txt"
for t in default rope eager; do
    printf '%-8s %s | %s\n' "$t" \
        "$(grep -oE '"gen_tok_per_s": [0-9.]+' "$OUT/gen_$t.log" || echo 'gen FAILED')" \
        "$(grep 'RESULT vLLM decode' "$OUT/score_$t.log" | cut -c27- || echo 'score FAILED')"
done
