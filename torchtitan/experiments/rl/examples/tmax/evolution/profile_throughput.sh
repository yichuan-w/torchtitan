#!/usr/bin/env bash
# One reading of where the training loop's time goes.
#
# Run it before and after a knob change with the same window, or the two numbers
# are not comparable: throughput swings by 3x between a lull and a full pool, so a
# single short sample says almost nothing. Default window is 6 minutes.
set -uo pipefail
R=/scratch/gpfs/TRIDAO/al9080/terminal-rl
L=$R/logs/rltrain_take8.log
PY=/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python
W=${1:-360}
LABEL=${2:-profile}

echo "=== $LABEL  (window ${W}s, $(date +%H:%M:%S)) ==="

# --- engine side: the split between prefill and decode is the whole question ---
echo "--- vLLM engines (last 12 reports) ---"
grep -oE "Avg prompt throughput: [0-9.]+ tokens/s, Avg generation throughput: [0-9.]+ tokens/s, Running: [0-9]+ reqs, Waiting: [0-9]+ reqs, GPU KV cache usage: [0-9.]+%, Prefix cache hit rate: [0-9.]+%" "$L" 2>/dev/null \
 | tail -12 | $PY -c "
import sys, re, statistics as st
rows=[]
for l in sys.stdin:
    m=re.findall(r'[0-9.]+', l)
    if len(m)>=6: rows.append([float(x) for x in m[:6]])
if not rows: print('  (no engine reports)'); raise SystemExit
p,g,run,wait,kv,hit = (st.median([r[i] for r in rows]) for i in range(6))
print(f'  prefill   {p:>10,.0f} tok/s')
print(f'  decode    {g:>10,.0f} tok/s      prefill/decode = {p/max(g,1):.1f}x')
print(f'  running   {run:>10,.0f} reqs     waiting {wait:,.0f}')
print(f'  KV cache  {kv:>10.1f} %')
print(f'  prefix hit{hit:>10.1f} %')
"

# --- loop side: what the engines actually buy us ---
a=$(grep -ac status=completed "${RUN:-${TRL_BASE:?}/runs/latest}/stdout.log")
s0=$(date +%s)
sleep "$W"
b=$(grep -ac status=completed "${RUN:-${TRL_BASE:?}/runs/latest}/stdout.log")
s1=$(date +%s)
$PY -c "
d=$b-$a; dt=$s1-$s0; r=d*60/dt
print(f'--- loop ---')
print(f'  rollouts  {d} in {dt}s = {r:.1f}/min')
print(f'  a 512-rollout step therefore takes {512/max(r,0.01):.1f} min')
"
echo "--- GPU ---"
nvidia-smi --query-gpu=index,utilization.gpu --format=csv,noheader | tr '\n' ' '; echo
