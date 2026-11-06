"""All accepted rewrites whose tests changed: instruction diff + full tests diff, for a verifier-regression sweep."""
import difflib, glob, json, os
M = "/data/users/yichuan/scratch/evolve_samples/1030c/meta"
OUT = "/data/users/yichuan/scratch/evolve_samples/full_regress"
os.makedirs(OUT, exist_ok=True)
def rd(p):
    return open(p, errors="replace").read().splitlines() if os.path.exists(p) else []
recs = []; nochange = 0
for f in sorted(glob.glob(f"{M}/*/rewrites/*/rewrite.json")):
    d = json.load(open(f))
    if d.get("status") != "accepted": continue
    rdir = os.path.dirname(f); task = rdir.split("/")[-3]; i, o = d["input_rev"], d["result_rev"]
    a, b = f"{M}/{task}/r{i}", f"{M}/{task}/r{o}"
    names = sorted(set(os.listdir(f"{a}/tests") if os.path.isdir(f"{a}/tests") else []) | set(os.listdir(f"{b}/tests") if os.path.isdir(f"{b}/tests") else []))
    td = []
    for t in names:
        td += list(difflib.unified_diff(rd(f"{a}/tests/{t}"), rd(f"{b}/tests/{t}"), f"before/tests/{t}", f"after/tests/{t}", lineterm="", n=3))
    if not td:
        nochange += 1; continue
    idiff = list(difflib.unified_diff(rd(f"{a}/instruction.md"), rd(f"{b}/instruction.md"), "before/instruction.md", "after/instruction.md", lineterm="", n=1))
    recs.append({"id": f"{task}@{os.path.basename(rdir)}", "job": d.get("job"), "instruction_diff": "\n".join(idiff),
                 "tests_diff": "\n".join(td)[:20000], "tests_diff_truncated": len("\n".join(td)) > 20000,
                 "before_dir": a, "after_dir": b})
print("tests changed", len(recs), "tests unchanged", nochange, "harder", sum(r["job"] == "harder" for r in recs))
K = 9
for k in range(K):
    with open(f"{OUT}/chunk_{k}.jsonl", "w") as w:
        for r in recs[k::K]: w.write(json.dumps(r) + "\n")
