#!/usr/bin/env python3
"""Post-hoc scoring of the V12 half-scale run (ACCEPTANCE.md section 1, section 3, section 4, section 6).

Sets: acc_r1, acc_r2 (the 20 acceptance ids x2), movers (18 verified), flip (12 stratified flippers).
Checks: schema validity; lints L1-L15 via lint_v12.lint(task_files, row, candidates); guards (non-PASS, required
floor field named, pre-registered tier class, repeat agreement); controls per axis (leak axis clean; PASS-required
4 PASS or PASS-held; A4 3 non-PASS with expectation_movable; B1 3 non-PASS with overspecific_check); movers vs their
V12 expectation; the four-clause over-rejection instrument; reject_shape / per-side status tallies; tier comparison
with V10.1 draft 1 on shared ids. Prints aggregates and ids only.
"""
import glob
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
V12 = os.path.abspath(os.path.join(HERE, ".."))
OF = os.path.abspath(os.path.join(V12, ".."))
TASKS = os.path.join(OF, "v9_1", "validation", "tasks")
CAND = os.path.join(HERE, "candidates")
SCHEMA = os.path.join(V12, "output_schema_v12.json")
D1 = os.path.join(OF, "v10_1", "validation", "draft1")
sys.path.insert(0, HERE)
import lint_v12  # noqa: E402

LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
A_FIELDS = LEAK + ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")
B_FIELDS = ("overspecific_check", "env_mismatch", "unstable_reward")
RULE_FIELDS = A_FIELDS + B_FIELDS + ("ambiguity", "trivial")
FLOOR_FIELDS = LEAK + ("expectation_movable", "core_clause_unenforced", "overspecific_check", "env_mismatch", "unstable_reward")

# ACCEPTANCE section 1: required floor field(s) (any of) + pre-registered tier class (+ reject_shape for REJECT-PROVEN)
GUARDS = {
    "task_000156_2f395118": (["oracle_reachable"], "REPAIR", None),
    "task_000241_547763df": (["value_derivable"], "REJECT-PROVEN", "deliverable_is_leaked_material"),
    "task_000311_9e22fec5": (["value_derivable"], "EITHER", "deliverable_is_leaked_material"),  # ACCEPTANCE section 0 (finishing pass): REPAIR or REJECT-PROVEN-with-shape both accepted
    "task_004863_67089cd4": (["expectation_revealed", "oracle_reachable"], "REPAIR", None),
    "task_004886_10504160": (["expectation_revealed", "value_derivable"], "REPAIR", None),
    "task_000221_fb2fd737": (["oracle_reachable"], "REPAIR", None),
    "task_000872_cf99cb19": (["oracle_reachable"], "REPAIR", None),
    "task_001068_ba585e13": (["oracle_reachable"], "REPAIR", None),
    "task_001257_21489dc1": (["oracle_reachable"], "REPAIR", None),
    "task_000501_3c7739f3": (["value_derivable"], "REPAIR", None),
}
CONTROLS_PASS = ["task_000316_02020782", "task_001104_dffa14ac", "task_000854_5a8ded74", "task_005042_071a51df"]
CONTROLS_A4 = ["task_000681_6f824421", "task_000927_43407097", "task_000056_f726766b"]
CONTROLS_B1 = ["task_007102_d981ab89", "task_000220_ab2204ea", "task_002378_139ea7d2"]
CONTROLS = CONTROLS_PASS + CONTROLS_A4 + CONTROLS_B1

# ACCEPTANCE section 6: expected tiers (set) and required fields per mover
MOVERS = {
    "task_000092_a7e4987d": ({"PASS"}, [], None),
    "task_006700_8f3aa427": ({"PASS"}, [], None),
    "task_000370_755f32f1": ({"REJECT-PROVEN"}, ["overspecific_check"], "broken_premise"),
    "task_000595_d0be5c02": ({"REPAIR"}, ["core_clause_unenforced"], None),
    "task_000884_a5826af0": ({"REPAIR"}, ["overspecific_check"], None),
    "task_001110_28c99a60": ({"REPAIR"}, ["expectation_movable", "overspecific_check"], None),
    "task_000240_83a610a2": ({"REPAIR"}, ["overspecific_check"], None),
    "task_000289_c79a9c35": ({"REPAIR"}, ["overspecific_check"], None),
    "task_000306_9ab3da9c": ({"REPAIR"}, ["overspecific_check"], None),
    "task_000653_81e6ac67": ({"REPAIR"}, ["overspecific_check"], None),
    "task_001160_f4115be8": ({"REPAIR"}, ["core_clause_unenforced"], None),
    "task_005170_60c2f84e": ({"REPAIR"}, ["overspecific_check"], None),
    "task_000644_f4228067": ({"REJECT-PROVEN"}, ["overspecific_check"], "broken_premise"),
    "task_000815_ccae7035": ({"REJECT-PROVEN"}, ["expectation_revealed"], "deliverable_is_leaked_material"),
    "task_003704_a0afd889": ({"REPAIR"}, ["value_derivable"], None),
    "task_000242_341bf3b8": ({"PASS"}, [], None),
    "task_000509_3d105353": ({"PASS", "REPAIR"}, [], None),
    "task_001221_6603ce68": ({"PASS", "REVIEW"}, [], None),  # REVIEW with uncertain_fact on redis tolerated
}


def load_rows(set_label):
    rows = {}
    for f in sorted(glob.glob(os.path.join(HERE, f"rows_{set_label}", "*.jsonl"))):
        tid = os.path.basename(f)[:-6]
        lines = [l for l in open(f).read().splitlines() if l.strip()]
        try:
            rows[tid] = json.loads(lines[0]) if lines else None
        except json.JSONDecodeError:
            rows[tid] = None
    return rows


_cache = {}


def task_files(tid):
    if tid not in _cache:
        d = os.path.join(TASKS, tid)
        _cache[tid] = {n: (open(os.path.join(d, n), errors="replace").read() if os.path.exists(os.path.join(d, n)) else "")
                       for n in ("instruction.md", "setup.sh", "tests/test.sh")}
    return _cache[tid]


def candidates(tid):
    p = os.path.join(CAND, f"{tid}.json")
    return json.load(open(p)) if os.path.exists(p) else {"programs": [], "failing_statements": [], "imports": []}


def fired(r):
    return [k for k in RULE_FIELDS if r.get(k)]


def held(r):
    return bool(r.get("unconfirmed_dependency"))


def leak(r):
    return any(r.get(k) for k in LEAK) or r.get("verdict") == "ANSWER-LEAK"


def main():
    import jsonschema
    validator = jsonschema.Draft202012Validator(json.load(open(SCHEMA)))
    sets = {s: load_rows(s) for s in ("acc_r1", "acc_r2", "movers", "flip")}
    out = {"sets": {}}
    for s, rows in sets.items():
        tiers, pass_held, shapes, sa, sb, schema_bad, lint_bad, unparse = Counter(), 0, Counter(), Counter(), Counter(), {}, {}, []
        for tid, r in rows.items():
            if r is None:
                unparse.append(tid)
                continue
            tiers[r.get("tier")] += 1
            if r.get("tier") == "PASS" and held(r):
                pass_held += 1
            if r.get("reject_shape"):
                shapes[r["reject_shape"]] += 1
            sa[r.get("exploit_status_a")] += 1
            sb[r.get("exploit_status_b")] += 1
            errs = [e.message[:100] for e in validator.iter_errors(r)]
            if errs:
                schema_bad[tid] = errs[:3]
            try:
                lv = lint_v12.lint(task_files(tid), r, candidates(tid))
            except Exception as ex:
                lv = [f"LINT-ERROR:{type(ex).__name__}:{str(ex)[:60]}"]
            if not str(r.get("agent", "")).startswith(f"judge:{s}:b"):
                lv = sorted(set(lv) | {"AGENT-LABEL"})
            if lv:
                lint_bad[tid] = lv
        out["sets"][s] = {"rows": len(rows), "tiers": dict(tiers), "pass_held": pass_held, "reject_shapes": dict(shapes),
                          "exploit_status_a": dict(sa), "exploit_status_b": dict(sb), "unparseable": unparse,
                          "schema_invalid": schema_bad, "lint_violations": lint_bad}

    reps = ["acc_r1", "acc_r2"]
    acc = {}
    for tid, (req, tclass, shape) in GUARDS.items():
        rs = [sets[s].get(tid) for s in reps]
        present = [r for r in rs if r]
        tiers = [r.get("tier") if r else "MISSING" for r in rs]
        e = {"kind": "guard", "tiers": tiers, "verdicts": [r.get("verdict") if r else None for r in rs], "fired": [fired(r) if r else None for r in rs],
             "shapes": [r.get("reject_shape") if r else None for r in rs],
             "all_non_pass": len(present) == 2 and all(t not in ("PASS", "MISSING") for t in tiers),
             "required_floor_every_repeat": len(present) == 2 and all(any(r.get(k) for k in req) for r in present),
             "class_ok_every_repeat": len(present) == 2 and all(
                 (tclass == "EITHER" and (t == "REPAIR" or (t == "REJECT-PROVEN" and r.get("reject_shape") == shape))) or
                 (tclass != "EITHER" and t == tclass and (shape is None or r.get("reject_shape") == shape)) or
                 (t == "REVIEW" and r.get("exploit_status_a") != "static_trace" and r.get("exploit_status_b") != "static_trace")
                 for t, r in zip(tiers, present)),
             "expected_class": tclass}
        if tid == "task_004886_10504160":
            e["untouched"] = [str(r.get("untouched_image_passes"))[:8] if r else None for r in rs]
            e["r5_ok"] = len(present) == 2 and all(str(r.get("untouched_image_passes", "")).startswith(("yes", "unknown")) for r in present)
        acc[tid] = e
    for tid in CONTROLS:
        rs = [sets[s].get(tid) for s in reps]
        present = [r for r in rs if r]
        tiers = [r.get("tier") if r else "MISSING" for r in rs]
        e = {"kind": "control", "tiers": tiers, "verdicts": [r.get("verdict") if r else None for r in rs], "fired": [fired(r) if r else None for r in rs],
             "leak_axis_clean_every_repeat": len(present) == 2 and not any(leak(r) for r in present), "leak_repeats": sum(leak(r) for r in present)}
        if tid in CONTROLS_PASS:
            e["pass_every_repeat"] = len(present) == 2 and all(t == "PASS" for t in tiers)
            e["held_repeats"] = sum(held(r) for r in present)
        elif tid in CONTROLS_A4:
            e["a4_hits"] = sum(bool(r.get("expectation_movable")) for r in present)
            e["non_pass_every_repeat"] = len(present) == 2 and all(t != "PASS" for t in tiers)
        else:
            e["b1_hits"] = sum(bool(r.get("overspecific_check")) for r in present)
            e["unverified_repeats"] = sum(str(r.get("literal_derivation") or "").startswith("unverified") for r in present)
            e["non_pass_every_repeat"] = len(present) == 2 and all(t != "PASS" for t in tiers)
        acc[tid] = e
    out["acceptance"] = acc

    mv = {}
    for tid, (exp_tiers, req, shape) in MOVERS.items():
        r = sets["movers"].get(tid)
        if not r:
            mv[tid] = {"tier": None, "ok": False}
            continue
        ok = r.get("tier") in exp_tiers and all(r.get(k) for k in req) and (shape is None or r.get("reject_shape") == shape)
        if tid == "task_000509_3d105353" and r.get("tier") == "REPAIR":
            ok = ok and bool(r.get("overspecific_check"))
        mv[tid] = {"tier": r.get("tier"), "fired": fired(r), "shape": r.get("reject_shape"), "expected": sorted(exp_tiers), "ok": ok, "held": held(r), "leak": leak(r)}
    out["movers"] = mv

    # over-rejection instrument (ACCEPTANCE section 4)
    all_rows = [(s, tid, r) for s, rows in sets.items() for tid, r in rows.items() if r]
    bad_shape = [f"{s}/{tid}" for s, tid, r in all_rows if r.get("tier") == "REJECT-PROVEN" and r.get("reject_shape") not in ("instruction_prints_answer", "deliverable_is_leaked_material", "broken_premise", "pre_solved")]
    instrument = {
        "1_pass_required_every_repeat": all(acc[t]["pass_every_repeat"] for t in CONTROLS_PASS),
        "2_leak_axis_clean_ten_controls": all(acc[t]["leak_axis_clean_every_repeat"] for t in CONTROLS),
        "3_000092_006700_pass": all(mv[t].get("tier") == "PASS" for t in ("task_000092_a7e4987d", "task_006700_8f3aa427")),
        "4_every_reject_names_a_shape": not bad_shape, "bad_shape_rows": bad_shape,
    }
    instrument["passes"] = all(v for k, v in instrument.items() if k[0].isdigit())
    out["over_rejection_instrument"] = instrument
    out["acceptance_summary"] = {
        "guards_all_non_pass": sum(acc[t]["all_non_pass"] for t in GUARDS),
        "guards_required_floor": sum(acc[t]["required_floor_every_repeat"] for t in GUARDS),
        "guards_class_ok": sum(acc[t]["class_ok_every_repeat"] for t in GUARDS),
        "guards_class_failed": [t for t in GUARDS if not acc[t]["class_ok_every_repeat"]],
        "r5_004886": acc["task_004886_10504160"].get("r5_ok"),
        "controls_leak_clean": sum(acc[t]["leak_axis_clean_every_repeat"] for t in CONTROLS),
        "pass_required_ok": sum(acc[t]["pass_every_repeat"] for t in CONTROLS_PASS),
        "a4": {t: (acc[t]["a4_hits"], acc[t]["non_pass_every_repeat"]) for t in CONTROLS_A4},
        "b1": {t: (acc[t]["b1_hits"], acc[t]["unverified_repeats"], acc[t]["non_pass_every_repeat"]) for t in CONTROLS_B1},
        "movers_ok": sum(1 for v in mv.values() if v["ok"]), "movers_failed": [t for t, v in mv.items() if not v["ok"]],
    }

    # comparison with V10.1 draft 1 on shared ids (acceptance ids: draft1 acc_r1; movers/flip: draft1 strat)
    d1 = {}
    for s in ("rows_acc_r1", "rows_strat"):
        for f in glob.glob(os.path.join(D1, s, "*.jsonl")):
            try:
                d1[(s, os.path.basename(f)[:-6])] = json.loads(open(f).readline()).get("tier")
            except Exception:
                pass
    comp = Counter()
    moved = []
    for s, tid, r in all_rows:
        key = ("rows_acc_r1", tid) if s in ("acc_r1", "acc_r2") else ("rows_strat", tid)
        if s == "acc_r2":
            continue
        t1 = d1.get(key)
        if t1:
            comp[(t1, r.get("tier"))] += 1
            if t1 != r.get("tier"):
                moved.append(f"{tid}:{t1}->{r.get('tier')}")
    out["vs_v10_1_draft1"] = {"pairs": {f"{a}->{b}": n for (a, b), n in sorted(comp.items())}, "agreement": sum(n for (a, b), n in comp.items() if a == b), "compared": sum(comp.values()), "moved": moved}
    json.dump(out, open(os.path.join(HERE, "score_v12.json"), "w"), indent=1)

    print("== sets")
    for s, e in out["sets"].items():
        print(f"{s}: rows {e['rows']} tiers {e['tiers']} PASS-held {e['pass_held']} shapes {e['reject_shapes']} schema_invalid {len(e['schema_invalid'])} lint_violations {len(e['lint_violations'])} unparseable {len(e['unparseable'])}")
    print("== acceptance", json.dumps(out["acceptance_summary"]))
    for t in GUARDS:
        a = acc[t]
        print(f"  guard {t}: tiers {a['tiers']} shapes {a['shapes']} fired {a['fired']}")
    for t in CONTROLS:
        a = acc[t]
        print(f"  control {t}: tiers {a['tiers']} verdicts {a['verdicts']} fired {a['fired']} leak={a['leak_repeats']}")
    print("== movers", out["acceptance_summary"]["movers_ok"], "/ 18")
    for t, v in mv.items():
        flag = "" if v["ok"] else "  MISS"
        print(f"  {t}: {v.get('tier')} fired {v.get('fired')} shape {v.get('shape')} expected {v.get('expected')}{flag}")
    print("== over-rejection instrument", json.dumps(instrument))
    print("== vs V10.1 draft1", json.dumps(out["vs_v10_1_draft1"]["pairs"]), "agreement", out["vs_v10_1_draft1"]["agreement"], "/", out["vs_v10_1_draft1"]["compared"])
    for m in moved:
        print("  ", m)
    print("written", os.path.join(HERE, "score_v12.json"))


if __name__ == "__main__":
    main()
