"""Deterministic candidates for verifier regressions: before-test functions deleted, or with assert/check lines removed."""
import ast, glob, json, os, re
F = "/data/users/yichuan/scratch/evolve_samples/full_regress"
recs = [json.loads(l) for f in sorted(glob.glob(f"{F}/chunk_*.jsonl")) for l in open(f)]
CHECK = re.compile(r"\b(assert|pytest\.raises|fail\(|exit 1|\[\[|grep|diff|cmp|test )")
def funcs(d):
    out = {}
    for p in glob.glob(f"{d}/tests/*.py"):
        try: tree = ast.parse(open(p, errors="replace").read())
        except SyntaxError: continue
        src = open(p, errors="replace").read().splitlines()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = src[n.lineno - 1:n.end_lineno]
                out[n.name] = [l.strip() for l in body]
    return out
def shell(d):
    lines = []
    for p in glob.glob(f"{d}/tests/*.sh"):
        lines += [l.strip() for l in open(p, errors="replace") if CHECK.search(l)]
    return lines
cands = []
for r in recs:
    b, a = funcs(r["before_dir"]), funcs(r["after_dir"])
    after_all = {l for v in a.values() for l in v}
    c = []
    for name, body in b.items():
        if name not in a:
            if name.startswith("test") or any("assert" in l for l in body):
                c.append({"kind": "deleted", "func": name})
            continue
        gone = [l for l in body if ("assert" in l or "raise" in l) and l not in a[name] and l not in after_all]
        if gone:
            c.append({"kind": "asserts_removed", "func": name, "removed": gone[:8]})
    sb, sa = shell(r["before_dir"]), set(shell(r["after_dir"]))
    gone = [l for l in sb if l not in sa]
    if gone:
        c.append({"kind": "shell_checks_removed", "removed": gone[:8]})
    if c:
        cands.append({"id": r["id"], "job": r["job"], "before_dir": r["before_dir"], "after_dir": r["after_dir"],
                      "instruction_diff": r["instruction_diff"], "candidates": c})
print("records with candidates", len(cands), "of", len(recs), "candidates", sum(len(x["candidates"]) for x in cands))
known = {json.loads(l)["id"] for l in open("/data/users/yichuan/scratch/evolve_samples/claim_audit/regress_out.jsonl") if json.loads(l)["label"] == "regression"}
print("known covered", len(known & {x["id"] for x in cands}), "/", len(known))
K = 9
for k in range(K):
    with open(f"{F}/cand_{k}.jsonl", "w") as w:
        for x in cands[k::K]: w.write(json.dumps(x) + "\n")
