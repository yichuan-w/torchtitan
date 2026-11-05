"""Item 3: easier chains (r0 first group 0/12 -> easier accepted -> new rev mixed and rising) vs control (easier rejected/failed, stays r0)."""
import glob, json, os, sys
import pandas as pd

groups = pd.read_csv(sys.argv[1])
meta, out = sys.argv[2], sys.argv[3]
fin = groups.dropna(subset=["solved", "rev"]).sort_values("group")


def category(task):
    if "__" in task:
        return "swe (repo bug)"
    head = task.split("_2026")[0]
    return head if head != task else task.split("_")[0]


CORPUS = json.load(open(os.path.join(os.path.dirname(meta), "task_corpus.json")))


def source(task):
    if task in CORPUS:
        return CORPUS[task]
    try:
        return json.load(open(f"{meta}/{task}/r0/seed.json")).get("source", "?")
    except Exception:
        return "?"


def rewrites(task):
    rs = []
    for f in sorted(glob.glob(f"{meta}/{task}/rewrites/*/rewrite.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        d["_dir"] = os.path.dirname(f)
        rs.append(d)
    return sorted(rs, key=lambda d: d.get("started") or d.get("finished") or "")


rows = []
for task, g in fin.groupby("task"):
    r0 = g[g.rev == 0]
    if r0.empty:
        continue
    first = r0.iloc[0]
    if first.solved != 0:
        continue
    fr = json.loads(first.finish) if isinstance(first.finish, str) else {}
    tier = "all 12 submitted" if first.n_submitted == first.n_hdr == 12 else "some not submitted"
    rw = [d for d in rewrites(task) if d.get("input_rev") == 0 and d.get("job") == "easier"]
    if not rw:
        continue
    d = rw[0]
    st = d.get("status")
    row = {"task": task, "source": source(task), "category": category(task), "tier": tier,
           "first_group": int(first.group), "first_finish": json.dumps(fr), "easier_status": st, "rewrite_dir": d["_dir"]}
    if st == "accepted":
        new = g[g.rev == d["result_rev"]]
        seq = list(new.solved.astype(int))
        row["new_rev"] = d["result_rev"]
        row["new_seq"] = seq
        rising = len(seq) >= 2 and any(0 < s < 12 for s in seq) and seq[-1] > seq[0]
        row["kind"] = "chain" if rising else "accepted, not rising"
        row["gain"] = (max(seq) - seq[0]) if seq else None
    elif st in ("failed", "kept", "rejected"):
        later = [x for x in rewrites(task) if x.get("status") == "accepted"]
        row["kind"] = "control" if not later and (g.rev == 0).all() else "control, later rewritten"
        row["r0_seq"] = list(r0.solved.astype(int))
    else:
        continue
    row["revs_seq"] = json.dumps({int(r): list(x.solved.astype(int)) for r, x in g.groupby("rev")})
    rows.append(row)
df = pd.DataFrame(rows)
df.to_csv(f"{out}/item3_rows.csv", index=False)
print(pd.crosstab(df.kind, df.tier, margins=True))
for kind in ["chain", "control"]:
    s = df[df.kind == kind]
    print(f"\n== {kind} by source x tier"); print(pd.crosstab(s.source, s.tier, margins=True))
    print(f"== {kind} by category (top)"); print(s.category.value_counts().head(15).to_string())
ctl = df[df.kind == "control"]
if len(ctl):
    print("\ncontrol r0 later solved>0 frac:", round(ctl.r0_seq.apply(lambda q: any(v > 0 for v in q[1:])).mean(), 3),
          " mean groups:", round(ctl.r0_seq.apply(len).mean(), 2))
top = df[(df.kind == "chain") & (df.tier == "all 12 submitted")].sort_values("gain", ascending=False).head(3)
print("\nTOP3"); print(top[["task", "source", "category", "new_seq", "gain", "revs_seq", "rewrite_dir"]].to_string())
