#!/bin/bash
# Rebuild the agentic TB2.1 -> top-10 pipeline.
#
# Stage 3 (the rerank) needs an agent runtime and is not run from here; the
# script stops in front of it, prints what to do, and picks up again at merge
# once rerank/ is populated. Run with `--from <stage>` to resume.
#
#   ./run.sh              full pipeline, pausing before stage 3
#   ./run.sh --from merge  just re-derive the CSVs from rerank/
#   ./run.sh --verify      check the published outputs still reproduce
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-/data/yichuan_wang/leann2-venv/bin/python}"
export HF_HOME="${HF_HOME:-/data/yichuan_wang/cache/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export HF_HUB_DISABLE_XET=1
export PYTHONUNBUFFERED=1

FROM="cache"
case "${1:-}" in
  --from) FROM="${2:?--from needs a stage: cache|recall|packets|merge}" ;;
  --verify) exec "$PY" "$HERE/verify.py" "${@:2}" ;;
  "") ;;
  *) echo "usage: $0 [--from cache|recall|packets|merge] | --verify [--full]" >&2; exit 2 ;;
esac

STAGES=(cache recall packets merge)
stage_index() {
  local i
  for i in "${!STAGES[@]}"; do [[ "${STAGES[$i]}" == "$1" ]] && { echo "$i"; return; }; done
  echo "unknown stage: $1" >&2; exit 2
}
FROM_IDX=$(stage_index "$FROM")
stage_at_or_after() { [[ "$(stage_index "$1")" -ge "$FROM_IDX" ]]; }

if stage_at_or_after cache; then
  echo "== 1/4 corpus cache =="
  "$PY" "$HERE/bootstrap_cache.py"
fi

if stage_at_or_after recall; then
  echo "== 2/4 retrieval (4 retrievers per corpus; GPU, ~12 min) =="
  # Caps its own GPU allocation: this node's cards run training at ~95% memory.
  "$PY" "$HERE/build_candidates.py" --batch 16 --per-retriever 25
fi

if stage_at_or_after packets; then
  echo "== 3/4 packets =="
  "$PY" "$HERE/make_packets.py"
  "$PY" "$HERE/dispatch.py" plan
fi

if [[ "$FROM" != "merge" ]]; then
  done_n=$(ls "$HERE"/rerank/*.json 2>/dev/null | grep -vc '/_' || true)
  if [[ "${done_n:-0}" -lt 89 ]]; then
    cat <<EOF

== STAGE 3: rerank (needs an agent runtime) ==
$done_n/89 judgement files present in rerank/.

  $PY $HERE/dispatch.py prompts

prints 18 prompts. Give each to one agent, all in parallel; each writes
rerank/<tb_id>.json against AGENT_SPEC.md. Track with:

  $PY $HERE/dispatch.py status

Then finish with:

  $0 --from merge
EOF
    exit 0
  fi
fi

echo "== 4/4 merge + validate =="
"$PY" "$HERE/merge_agentic.py" --strict
echo
echo "outputs in $HERE/results/"
