#!/usr/bin/env python3
"""Compare the GLM-5.3 effort arms of the v13 kit against each other and against the claude-opus-4-8 reference run.

For each arm: coverage, schema validity, lints, tier mix, the acceptance instrument (guards held, controls SEED,
movers), agreement with the Opus reference on the two blocking axes, and what a judgment cost (wall seconds, output
tokens, turns). Aggregates and ids only. Usage: compare_efforts.py [effort ...]
"""
import glob, json, os, statistics, sys
from collections import Counter

V13 = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(V13, "validation"))
import lint_v13, score_v13 as S  # noqa: E402
import jsonschema  # noqa: E402

V = jsonschema.Draft202012Validator(json.load(open(os.path.join(V13, "output_schema_v13.json"))))
ARMS = sys.argv[1:] or ["low", "high", "xhigh"]
SETS = ("acc_r1", "acc_r2", "movers", "flip")
HELD = ("FLAGGED", "UNSURE")


def rows_of(base):
    out = {}
    for s in SETS:
        for f in glob.glob(os.path.join(base, f"rows_{s}", "*.jsonl")):
            try:
                out[(s, os.path.basename(f)[:-6])] = json.loads(open(f).readline())
            except Exception:
                out[(s, os.path.basename(f)[:-6])] = None
    return out


def costs_of(base):
    p = os.path.join(base, "results", "summary_live.jsonl")
    out = {}
    if os.path.exists(p):
        for l in open(p):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("worker"):
                out[r["worker"]] = r
    return out


def axes(r):
    return (any(r.get(k) for k in S.LEAK), any(r.get(k) for k in S.BSIDE))


ref = rows_of(os.path.join(V13, "validation"))
print(f"reference: claude-opus-4-8 medium, {len(ref)} rows\n")
hdr = f"{'arm':6s} {'rows':>5s} {'SEED':>5s} {'FLAG':>5s} {'UNSURE':>6s} {'bad':>4s} {'lint':>5s} {'guards':>7s} {'ctrlSEED':>9s} {'movers':>7s} {'leak=':>6s} {'unfair=':>8s} {'med s':>7s} {'med out':>8s} {'med turns':>9s}"
print(hdr); print("-" * len(hdr))
summary = {}
for arm in ARMS:
    base = os.path.join(V13, f"validation_glm53_{arm}")
    rows, cost = rows_of(base), costs_of(base)
    got = {k: r for k, r in rows.items() if r}
    bad = sum(1 for r in got.values() if list(V.iter_errors(r)))
    lint = sum(1 for k, r in got.items() if lint_v13.lint(S.task_files(k[1]), r, S.candidates(k[1])))
    tiers = Counter(r["tier"] for r in got.values())
    guards = sum(1 for g in S.GUARDS for s in ("acc_r1", "acc_r2") if got.get((s, g)) and got[(s, g)]["tier"] in HELD)
    ctrl = sum(1 for c in S.CONTROLS_PASS + S.CONTROLS_A4 for s in ("acc_r1", "acc_r2") if got.get((s, c)) and got[(s, c)]["tier"] == "SEED")
    mv = 0
    for tid, (exp, req, _note) in S.MOVERS.items():
        r = got.get(("movers", tid))
        if r and (("SEED" if r["tier"] == "SEED" else "HELD") in exp) and (r["tier"] == "SEED" or all(r.get(x) for x in req)):
            mv += 1
    both = [k for k in got if ref.get(k)]
    leak_same = sum(1 for k in both if axes(got[k])[0] == axes(ref[k])[0])
    unfair_same = sum(1 for k in both if axes(got[k])[1] == axes(ref[k])[1])
    cs = [cost.get(f"{k[0]}__{k[1]}") for k in got]
    cs = [c for c in cs if c and c.get("duration_s")]
    med = lambda f: (round(statistics.median([c[f] for c in cs if c.get(f)])) if cs else 0)
    print(f"{arm:6s} {len(got):5d} {tiers['SEED']:5d} {tiers['FLAGGED']:5d} {tiers['UNSURE']:6d} {bad:4d} {lint:5d} "
          f"{str(guards)+'/20':>7s} {str(ctrl)+'/14':>9s} {str(mv)+'/18':>7s} {str(leak_same)+'/'+str(len(both)):>6s} "
          f"{str(unfair_same)+'/'+str(len(both)):>8s} {med('duration_s'):7d} {med('output_tokens'):8d} {med('num_turns'):9d}")
    summary[arm] = {"rows": len(got), "tiers": dict(tiers), "schema_invalid": bad, "rows_with_lint": lint,
                    "guards_held_of_20": guards, "controls_seed_of_14": ctrl, "movers_ok_of_18": mv,
                    "leak_axis_same": f"{leak_same}/{len(both)}", "unfair_axis_same": f"{unfair_same}/{len(both)}",
                    "median_seconds": med("duration_s"), "median_output_tokens": med("output_tokens"), "median_turns": med("num_turns")}
ref_tiers = Counter(r["tier"] for r in ref.values() if r)
print(f"{'opus':6s} {len(ref):5d} {ref_tiers['SEED']:5d} {ref_tiers['FLAGGED']:5d} {ref_tiers['UNSURE']:6d}"
      f"{'':5s}{'':6s}{'18/20':>8s} {'14/14':>9s} {'15/18':>7s}")
print("\nguards: the 10 known leaks x2 repeats that must be held back; ctrlSEED: the 4 clean + 3 movable-expectation controls x2 that must be SEED")
json.dump(summary, open(os.path.join(V13, "effort_comparison.json"), "w"), indent=1)
print("written", os.path.join(V13, "effort_comparison.json"))
