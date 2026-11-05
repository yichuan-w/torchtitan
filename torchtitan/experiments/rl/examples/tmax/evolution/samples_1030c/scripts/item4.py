"""Item 4: gate funnel per direction, latest reasons per stage, and easier rewrites that jump 0/12 -> 12/12."""
import difflib, glob, json, os, re, sys
from collections import Counter, defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

base, out = sys.argv[1], sys.argv[2]
meta = f"{base}/meta"
led = [json.loads(l) for l in open(f"{base}/evolution/ledger.jsonl")]


def bucket(d):
    st, r = d.get("status"), (d.get("reason") or "").strip()
    if st == "accepted":
        return "accepted"
    if st == "running" or not st:
        return "unfinished at run end"
    if st == "kept":
        return "rewriter gave up (GIVE UP / BLOCKED)"
    if r.startswith("Filtered"):
        return "provider cyber classifier"
    if r.startswith(("AgentSessionError", "JSONDecodeError", "TimeoutExpired")):
        return "agent session error / timeout"
    if r.startswith(("SemanticProbeMisses", "SemanticProbeContract")) or "Semantic probe" in r:
        return "semantic probe failed"
    if d.get("stage") == "identity":
        return "identity gate (image USER changed)"
    if d.get("stage") == "daytona_oracle":
        return "oracle failed"
    return "agent validation / no change / other"


def redact(s):
    s = re.sub(r"/home/\S+|/data/\S+|/tmp/\S+", "<path>", s)
    return s[:300]


rws = []
for f in glob.glob(f"{meta}/*/rewrites/*/rewrite.json"):
    d = json.load(open(f))
    d["_dir"] = os.path.dirname(f)
    rws.append(d)
fun = defaultdict(Counter)
for d in led:
    if d.get("outcome") == "superseded":
        fun[d["direction"]]["signal superseded before handling"] += 1
latest = defaultdict(list)
for d in rws:
    b = bucket(d)
    fun[d.get("job")][b] += 1
    if b != "accepted":
        latest[(d.get("job"), b)].append((d.get("finished") or "", d.get("task"), redact(d.get("reason") or "")))
order = ["accepted", "rewriter gave up (GIVE UP / BLOCKED)", "provider cyber classifier", "agent session error / timeout",
         "semantic probe failed", "agent validation / no change / other", "identity gate (image USER changed)", "oracle failed",
         "unfinished at run end", "signal superseded before handling"]
tab = pd.DataFrame({j: [fun[j][k] for k in order] for j in ["easier", "harder"]}, index=order)
tab.loc["total signals"] = tab.sum()
print(tab.to_string())
tab.to_csv(f"{out}/item4_funnel.csv")
f, axes = plt.subplots(1, 2, figsize=(12, 3.8), sharey=True)
for ax, j in zip(axes, ["easier", "harder"]):
    vals = [fun[j][k] for k in order][::-1]
    cols = ["#2a78d6" if k == "accepted" else "#eb6834" for k in order][::-1]
    ax.barh(range(len(order)), vals, color=cols, height=0.7)
    for i, v in enumerate(vals):
        ax.text(v * 1.12 + 0.6, i, str(v), va="center", fontsize=8, color="#52514e")
    ax.set_yticks(range(len(order))); ax.set_yticklabels(order[::-1], fontsize=8)
    tot = sum(fun[j].values())
    ax.set_title(f"{j}: {tot} signals, {fun[j]['accepted']} accepted ({fun[j]['accepted'] / tot:.0%})", loc="left", fontsize=10)
    ax.set_xscale("symlog", linthresh=10)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
f.tight_layout(); f.savefig(f"{out}/item4_gate_funnel.png", dpi=170)
with open(f"{out}/item4_latest_reasons.md", "w") as w:
    for (j, b), xs in sorted(latest.items()):
        w.write(f"\n### {j} / {b} ({len(xs)})\n")
        for fin, t, r in sorted(xs, reverse=True)[:2]:
            w.write(f"- `{t}` ({fin}): {r}\n")

def solution_text(sd):
    """Reference solution as text: solve.sh and patches, or every string in gpt6_actions.json (recorded keystrokes)."""
    if not os.path.isdir(sd):
        return ""
    parts = []
    for fn in sorted(os.listdir(sd)):
        p = os.path.join(sd, fn)
        if fn.endswith(".json"):
            def strings(x):
                if isinstance(x, str):
                    yield x
                elif isinstance(x, dict):
                    for v in x.values():
                        yield from strings(v)
                elif isinstance(x, list):
                    for v in x:
                        yield from strings(v)
            try:
                parts += list(strings(json.load(open(p))))
            except Exception:
                pass
        else:
            parts.append(open(p, errors="replace").read())
    return "\n".join(parts)


# easier 0/12 -> 12/12
items = pd.read_csv(f"{out}/item2_rows.csv")
jump = items[(items.job == "easier") & (items.arm == "accepted") & (items.m1 == 0) & (items.p1 == 12)]
ws = lambda s: re.sub(r"\s+", " ", s or "").strip()
rows = []
for _, r in jump.iterrows():
    cand = [d for d in rws if d.get("task") == r.task and d.get("job") == "easier" and d.get("status") == "accepted"
            and d.get("signal", "").endswith(f"--g{int(r.signal_group)}")]
    if not cand:
        continue
    d = cand[0]
    a, b = d["input_rev"], d["result_rev"]
    rd = lambda rel: open(f"{meta}/{r.task}/r{rel}", errors="replace").read() if os.path.exists(f"{meta}/{r.task}/r{rel}") else ""
    i0, i1 = rd(f"{a}/instruction.md"), rd(f"{b}/instruction.md")
    dl = [l for l in difflib.unified_diff(i0.splitlines(), i1.splitlines(), lineterm="", n=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    added = ws(" ".join(l[1:] for l in dl if l.startswith("+")))
    sol = ws(solution_text(f"{meta}/{r.task}/r{b}/solution"))
    m = difflib.SequenceMatcher(None, added, sol, autojunk=False).find_longest_match(0, len(added), 0, len(sol)) if added and sol else None
    df0, df1 = rd(f"{a}/environment/Dockerfile"), rd(f"{b}/environment/Dockerfile")
    rows.append({"task": r.task, "instr_lines_changed": len(dl), "lcs_added_vs_solve": m.size if m else 0,
                 "lcs_text": added[m.a:m.a + m.size] if m else "", "dockerfile_changed": df0 != df1,
                 "changed": d.get("changed"), "p_seq": [r.p1, r.p2, r.p3], "dir": d["_dir"], "a": a, "b": b})
jd = pd.DataFrame(rows).sort_values("lcs_added_vs_solve", ascending=False)
jd.to_csv(f"{out}/item4_jump_0_to_12.csv", index=False)
print(f"\n0/12 -> 12/12 easier: {len(jd)}  dockerfile changed: {jd.dockerfile_changed.sum()}")
print(jd[["task", "instr_lines_changed", "lcs_added_vs_solve", "dockerfile_changed", "p_seq"]].to_string(index=False))
