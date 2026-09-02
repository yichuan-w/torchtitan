#!/bin/bash
# One-shot training vitals for the take-N run on della-tridao. Run ON della-tridao.
# Every autotune decision starts and ends with this readout (diff two runs of it).
ROOT=/scratch/gpfs/TRIDAO/al9080/terminal-rl
L=${VITALS_LOG:-$ROOT/logs/rltrain_take8.log}
RUN=${VITALS_RUN:-$ROOT/runs/tw-mix-take8-20260825-195658}
NOW=$(date +%s)
echo "=== vitals $(date '+%m-%d %H:%M:%S') log=$(basename $L) ==="
echo "[unit] $(systemctl --user is-active rltrain.service 2>/dev/null) restarts=$(systemctl --user show rltrain.service -p NRestarts --value 2>/dev/null) logage=$(( NOW - $(stat -c %Y $L) ))s"
echo "[steps] last batches:"
grep -aE "\[trainer_loop\] step [0-9]+: got batch" $L | tail -3 | sed 's/^\[actor=<root>\] //' | cut -c1-80
echo "[cadence] step-file mtimes (structured logs turn over per session):"
ls -t $RUN/outputs/rl/structured_logs/rl_trainer.global_rank_0.*.jsonl 2>/dev/null | head -1 | xargs -r stat -c "%y %n" | cut -c1-70
W=200000
echo "[solve] recent window: $(tail -c $W $L | grep -ac "reward=1.00")/$(tail -c $W $L | grep -acE "reward=-?[01]\.")  cumulative: $(grep -ac "reward=1.00" $L)/$(grep -acE "reward=-?[01]\." $L)"
echo "[groups] recent finalizations:"; tail -c $W $L | grep -aoE "solved=[0-9]+/[0-9]+" | sort | uniq -c | sort -rn | head -5
echo "[buffer] releases recent: $(tail -c $W $L | grep -aoE 'RELEASE\([a-z_]+' | sort | uniq -c | tr '\n' ' ')"
echo "[stale] $(grep -aci stale $L)   [re-render] $(grep -ac 'mid-trajectory re-render' $L)"
echo "[turns] recent completed rollout turn distribution:"
tail -c $W $L | grep -aoE "turns=[0-9]+" | awk -F= '{if($2<=2)a++;else if($2<=15)b++;else if($2<=40)c++;else d++} END{printf "  <=2:%d 3-15:%d 16-40:%d >40:%d\n",a,b,c,d}'
echo "[engine] $(grep -aoE 'Running: [0-9]+ reqs, Waiting: [0-9]+' $L | tail -1)"
echo "[sandbox issues] recent: $(tail -c $W $L | grep -ac sandbox_issue)  types: $(tail -c $W $L | grep -aoE '"error_type": "[A-Za-z]+"' | sort | uniq -c | tr '\n' ' ')"
echo "[eval] $(grep -a 'validation trace report' $L | tail -1 | grep -aoE 'policy_version=[0-9]+.*' | cut -c1-70)"
