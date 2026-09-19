"""One-line health readout of a hip training run: engine speed at load, rollout
finish reasons, timeouts by task rev. usage: run_health.py <run dir>"""
import glob, json, re, sys, statistics, collections, os
RUN = sys.argv[1]
pat = re.compile(r"generation throughput: ([0-9.]+) tokens/s, Running: (\d+) reqs")
rows = [(float(m[1]), int(m[2])) for line in open(f"{RUN}/stdout.log", errors="ignore") if (m := pat.search(line)) and int(m[2]) >= 20]
recent = rows[-120:]
eng = f"engines(last {len(recent)} stat lines, running>=20): running={statistics.median(r for _, r in recent):.0f} per-seq tok/s={statistics.median(g / r for g, r in recent):.1f}" if recent else "engines: no stat lines at load yet"
fr = collections.Counter(); pt = []; n = 0
for f in glob.glob(f"{RUN}/rollouts/*/*.jsonl"):
    with open(f) as fh:
        d = json.loads(fh.readline())
    if "finish_reason" not in d:
        continue
    n += 1; fr[d["finish_reason"]] += 1
    if d["finish_reason"] == "submit":
        pt.append(d["timing"].get("agent_secs", 0) / max(d["turns"], 1))
to = fr["hit_time_budget"]
ro = f"rollouts={n} timeout={to} ({100*to/max(n,1):.1f}%) submit={fr['submit']} other={n-to-fr['submit']} agent s/turn(submits) med={statistics.median(pt):.0f} p90={sorted(pt)[int(.9*(len(pt)-1))]:.0f}" if pt else f"rollouts={n} (none submitted yet)"
step = ""
for line in open(f"{RUN}/stdout.log", errors="ignore"):
    m = re.search(r"trainer_loop\] step (\d+): weights pulled", line)
    if m: step = m[1]
print(f"{os.path.basename(RUN)} steps_done={step or 0} | {eng} | {ro}")
