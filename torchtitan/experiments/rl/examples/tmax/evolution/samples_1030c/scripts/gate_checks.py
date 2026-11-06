"""Count the rewrite agent's own `./sandbox check` verdicts (run 1030c) and classify failures by component.

A check prints one `VERDICT: pass|fail ...` line. Fail lines carry either `stage=<x>` (setup stage that failed)
or `reward=R solve_exit=E null_reward=N`; extra context lines that follow are kept for classification.
"""
import csv, glob, json, os, re, sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

BASE = "/data/users/yichuan/scratch/evolve_samples"
R = os.path.expanduser("~/rl/.torchtrain_datasets/tree/yichuan/tmax_evolution/agentpick-cfv2-evolve-both-1030/root/evolution/tasks")
NUM = re.compile(r"reward=([\d.]+)\s+solve_exit=(-?\d+)\s+null_reward=([\d.]+)")
PATH = re.compile(r"/(home|data|tmp|mnt)/\S+")


def classify(line, ctx):
    m = NUM.search(line)
    s = re.search(r"stage=(\S+)", line)
    if s:
        return f"stage:{s.group(1)}"
    if m:
        r, e, n = float(m.group(1)), int(m.group(2)), float(m.group(3))
        if n >= 1.0:
            return "untouched workspace passes"
        if r < 1.0:
            return "oracle fails (reference solution scores <1)"
        joined = " ".join(ctx).lower()
        if "probe" in joined:
            return "semantic probe"
        return "other (oracle ok, untouched ok)"
    joined = (line + " " + " ".join(ctx)).lower()
    if "probe" in joined:
        return "semantic probe"
    return "other"


def scan(rdir):
    rel = os.path.relpath(rdir, f"{BASE}/1030c/meta")
    task, _, rw = rel.split("/")
    job = rw.rsplit("--", 1)[-1]
    try:
        status = json.load(open(f"{rdir}/rewrite.json")).get("status")
    except Exception:
        status = None
    sd = os.path.join(R, rel, "sessions")
    checks = []
    try:
        sessions = sorted(s for s in os.listdir(sd) if s.endswith("--agent"))
    except Exception:
        sessions = []
    for s in sessions:
        try:
            with open(os.path.join(sd, s, "stderr.txt"), errors="replace") as f:
                pending = None
                for line in f:
                    if pending is not None:
                        pending[1].append(line.strip()[:300])
                        if len(pending[1]) >= 4:
                            checks.append(pending); pending = None
                        continue
                    if line.startswith("VERDICT:"):
                        v = line.strip()
                        if v.startswith("VERDICT: pass"):
                            checks.append((v, []))
                        elif v.startswith("VERDICT: fail"):
                            pending = (v, [])
                if pending is not None:
                    checks.append(pending)
        except Exception:
            pass
    out = []
    for v, ctx in checks:
        ok = v.startswith("VERDICT: pass")
        out.append({"ok": ok, "kind": None if ok else classify(v, ctx), "line": v, "ctx": ctx})
    return {"task": task, "rewrite": rw, "job": job, "status": status, "checks": out, "n_sessions": len(sessions)}


if __name__ == "__main__":
    dirs = sorted(glob.glob(f"{BASE}/1030c/meta/*/rewrites/*"))
    cache = f"{BASE}/analysis/gate_checks_cache.json"
    if os.path.exists(cache):
        res = json.load(open(cache))
    else:
        with ThreadPoolExecutor(64) as ex:
            res = list(ex.map(scan, dirs))
        json.dump(res, open(cache, "w"))
    rows = []
    for r in res:
        k = Counter(c["kind"] for c in r["checks"] if not c["ok"])
        rows.append({"task": r["task"], "rewrite": r["rewrite"], "job": r["job"], "status": r["status"],
                     "agent_sessions": r["n_sessions"], "checks": len(r["checks"]),
                     "failed": sum(not c["ok"] for c in r["checks"]), "fail_kinds": json.dumps(dict(k))})
    with open(f"{BASE}/figs/gate_checks_per_rewrite.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    md = ["# Rewrite agent `./sandbox check` verdicts (run 1030c)\n"]
    examples = defaultdict(list)
    for job in ("easier", "harder"):
        rs = [r for r in res if r["job"] == job]
        allc = [c for r in rs for c in r["checks"]]
        fails = [c for c in allc if not c["ok"]]
        kinds = Counter(c["kind"] for c in fails)
        with_fail = [r for r in rs if any(not c["ok"] for c in r["checks"])]
        dist = Counter(min(len(r["checks"]), 6) for r in rs)
        md.append(f"## {job}\n")
        md.append(f"- rewrites: {len(rs)} (with ≥1 agent session: {sum(r['n_sessions'] > 0 for r in rs)}; with ≥1 check: {sum(len(r['checks']) > 0 for r in rs)})")
        md.append(f"- `./sandbox check` runs: {len(allc)}, failed: {len(fails)} ({len(fails) / max(1, len(allc)):.1%})")
        md.append(f"- rewrites with ≥1 failed check: {len(with_fail)}; of those, ended accepted: {sum(r['status'] == 'accepted' for r in with_fail)}; ended {dict(Counter(r['status'] for r in with_fail if r['status'] != 'accepted'))}")
        md.append(f"- accepted rewrites whose last check passed: {sum(bool(r['status'] == 'accepted' and r['checks'] and r['checks'][-1]['ok']) for r in rs)} / {sum(r['status'] == 'accepted' for r in rs)}")
        md.append("- failures by component:")
        for kname, n in kinds.most_common():
            md.append(f"  - {kname}: {n}")
        md.append("- checks per rewrite (6 = 6+): " + ", ".join(f"{k}: {dist[k]}" for k in sorted(dist)) + "\n")
        for c in fails:
            if len(examples[(job, c["kind"])]) < 2:
                examples[(job, c["kind"])].append(PATH.sub("<path>", c["line"] + " || " + " | ".join(x for x in c["ctx"] if x)[:300]))
    md.append("## Example fail lines\n")
    for (job, kname), ex in sorted(examples.items()):
        md.append(f"**{job} / {kname}**")
        for e in ex:
            md.append(f"- `{e}`")
    open(f"{BASE}/figs/gate_checks.md", "w").write("\n".join(md) + "\n")
    print("\n".join(md))
