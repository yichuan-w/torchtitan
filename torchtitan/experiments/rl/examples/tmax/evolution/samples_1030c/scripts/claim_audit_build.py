"""Build per-rewrite records (accepted, run 1030c): author's notes + tests diff input_rev -> result_rev."""
import ast, difflib, glob, json, os
M = "/data/users/yichuan/scratch/evolve_samples/1030c/meta"
OUT = "/data/users/yichuan/scratch/evolve_samples/claim_audit"
recs = []
for f in sorted(glob.glob(f"{M}/*/rewrites/*/rewrite.json")):
    d = json.load(open(f))
    if d.get("status") != "accepted":
        continue
    rdir = os.path.dirname(f); task = rdir.split("/")[-3]
    i, o = d.get("input_rev"), d.get("result_rev")
    notes = ""
    for n in ("hardening.md", "simplify.json", "repair.json"):
        p = f"{rdir}/notes/{n}"
        if os.path.exists(p):
            notes += f"### {n}\n" + open(p, errors="replace").read()[:7000] + "\n"
    a, b = f"{M}/{task}/r{i}/tests", f"{M}/{task}/r{o}/tests"
    diff = []
    for t in sorted(set(os.listdir(a) if os.path.isdir(a) else []) | set(os.listdir(b) if os.path.isdir(b) else [])):
        x = open(f"{a}/{t}", errors="replace").read().splitlines() if os.path.exists(f"{a}/{t}") else []
        y = open(f"{b}/{t}", errors="replace").read().splitlines() if os.path.exists(f"{b}/{t}") else []
        diff += list(difflib.unified_diff(x, y, f"r{i}/tests/{t}", f"r{o}/tests/{t}", lineterm="", n=2))
    recs.append({"id": f"{task}@{os.path.basename(rdir)}", "job": d.get("job"), "notes": notes,
                 "tests_diff": "\n".join(diff)[:9000], "published_tests_dir": b})
print(len(recs), sum(1 for r in recs if r["job"] == "harder"))
K = 9
for k in range(K):
    with open(f"{OUT}/chunk_{k}.jsonl", "w") as w:
        for r in recs[k::K]:
            w.write(json.dumps(r) + "\n")
