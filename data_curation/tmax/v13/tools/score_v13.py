#!/usr/bin/env python3
"""Post-hoc scoring of the v13 half-scale run on the same 70-judgment kit V12 was validated on.

Sets: acc_r1, acc_r2 (the 20 acceptance ids x2), movers (18 independently verified), flip (12 stratified flippers).
The expectations are v12's, carried over to the v13 contract:
  guards            10 known leaks  -> held back (FLAGGED, or UNSURE when not driven) with the required leak field, both repeats
  PASS controls      4              -> SEED both repeats
  A4 controls        3              -> SEED both repeats, expectation_movable noted, protected_paths listed   (v12: non-PASS)
  B1 controls        3              -> held back with overspecific_check, both repeats
  movers            18              -> leak / B1 movers held back with their field; A7-only and clean movers SEED
Instrument (what "not over-strict" means here): 1 the four PASS controls are SEED every repeat; 2 the leak axis is
clean on all ten controls; 3 000092 and 006700 are SEED; 4 no row is held back on a note alone; 5 the three A4
controls are SEED every repeat.
Regression: on every id, does v13 agree with the v12 validation row on the blocking axes (leak, unfair/unstable)?
Prints aggregates and ids only.
"""
import glob
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
V13 = os.path.abspath(os.path.join(HERE, ".."))
OF = os.path.abspath(os.path.join(V13, ".."))
TASKS = os.path.join(OF, "v9_1", "validation", "tasks")
CAND = os.path.join(OF, "v12", "validation", "candidates")
V12ROWS = os.path.join(OF, "v12", "validation")
SCHEMA = os.path.join(V13, "output_schema_v13.json")
sys.path.insert(0, HERE)
import lint_v13  # noqa: E402

LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
BSIDE = ("overspecific_check", "unstable_reward")
BLOCKING = LEAK + BSIDE
NOTES = ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")
HELD = ("FLAGGED", "UNSURE")

GUARDS = {
    "task_000156_2f395118": ["oracle_reachable"],
    "task_000241_547763df": ["value_derivable"],
    "task_000311_9e22fec5": ["value_derivable"],
    "task_004863_67089cd4": ["expectation_revealed", "oracle_reachable"],
    "task_004886_10504160": ["expectation_revealed", "value_derivable"],
    "task_000221_fb2fd737": ["oracle_reachable"],
    "task_000872_cf99cb19": ["oracle_reachable"],
    "task_001068_ba585e13": ["oracle_reachable"],
    "task_001257_21489dc1": ["oracle_reachable"],
    "task_000501_3c7739f3": ["value_derivable"],
}
CONTROLS_PASS = ["task_000316_02020782", "task_001104_dffa14ac", "task_000854_5a8ded74", "task_005042_071a51df"]
CONTROLS_A4 = ["task_000681_6f824421", "task_000927_43407097", "task_000056_f726766b"]
CONTROLS_B1 = ["task_007102_d981ab89", "task_000220_ab2204ea", "task_002378_139ea7d2"]
CONTROLS = CONTROLS_PASS + CONTROLS_A4 + CONTROLS_B1

# expected class ("SEED" / "HELD" / either), blocking fields required when held, note expected (reported, not scored)
MOVERS = {
    "task_000092_a7e4987d": ({"SEED"}, [], None),
    "task_006700_8f3aa427": ({"SEED"}, [], None),
    "task_000370_755f32f1": ({"HELD"}, ["overspecific_check"], None),
    "task_000595_d0be5c02": ({"SEED"}, [], "core_clause_unenforced"),
    "task_000884_a5826af0": ({"HELD"}, ["overspecific_check"], None),
    "task_001110_28c99a60": ({"HELD"}, ["overspecific_check"], "expectation_movable"),
    "task_000240_83a610a2": ({"HELD"}, ["overspecific_check"], None),
    "task_000289_c79a9c35": ({"HELD"}, ["overspecific_check"], None),
    "task_000306_9ab3da9c": ({"HELD"}, ["overspecific_check"], None),
    "task_000653_81e6ac67": ({"HELD"}, ["overspecific_check"], None),
    "task_001160_f4115be8": ({"SEED"}, [], "core_clause_unenforced"),
    "task_005170_60c2f84e": ({"HELD"}, ["overspecific_check"], None),
    "task_000644_f4228067": ({"HELD"}, ["overspecific_check"], None),
    "task_000815_ccae7035": ({"HELD"}, ["expectation_revealed"], None),
    "task_003704_a0afd889": ({"HELD"}, ["value_derivable"], None),
    "task_000242_341bf3b8": ({"SEED"}, [], None),
    "task_000509_3d105353": ({"SEED", "HELD"}, [], None),   # if held, it must be on B1
    "task_001221_6603ce68": ({"SEED", "HELD"}, [], None),   # UNSURE on an uncertain fact tolerated
}


def load_rows(base, set_label):
    rows = {}
    for f in sorted(glob.glob(os.path.join(base, f"rows_{set_label}", "*.jsonl"))):
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


def cls(r):
    return "SEED" if r.get("tier") == "SEED" else "HELD"


def axes(r):
    return ("leak" if any(r.get(k) for k in LEAK) else "-", "unfair" if any(r.get(k) for k in BSIDE) else "-")


def main():
    import jsonschema
    validator = jsonschema.Draft202012Validator(json.load(open(SCHEMA)))
    sets = {s: load_rows(HERE, s) for s in ("acc_r1", "acc_r2", "movers", "flip")}
    old = {s: load_rows(V12ROWS, s) for s in sets}
    out = {"sets": {}}
    for s, rows in sets.items():
        tiers, status, schema_bad, lint_bad, unparse, sizes = Counter(), Counter(), {}, {}, [], []
        for tid, r in rows.items():
            if r is None:
                unparse.append(tid)
                continue
            tiers[r.get("tier")] += 1
            status[r.get("exploit_status")] += 1
            sizes.append(len(json.dumps(r, ensure_ascii=False)))
            errs = [e.message[:100] for e in validator.iter_errors(r)]
            if errs:
                schema_bad[tid] = errs[:3]
            try:
                lv = lint_v13.lint(task_files(tid), r, candidates(tid))
            except Exception as ex:
                lv = [f"LINT-ERROR:{type(ex).__name__}:{str(ex)[:60]}"]
            if not str(r.get("agent", "")).startswith(f"judge:{s}:b"):
                lv = sorted(set(lv) | {"AGENT-LABEL"})
            if lv:
                lint_bad[tid] = lv
        out["sets"][s] = {"rows": len(rows), "tiers": dict(tiers), "exploit_status": dict(status), "unparseable": unparse,
                          "schema_invalid": schema_bad, "lint_violations": lint_bad,
                          "mean_row_chars": round(sum(sizes) / max(len(sizes), 1))}

    reps = ["acc_r1", "acc_r2"]
    acc = {}
    for tid, req in GUARDS.items():
        rs = [sets[s].get(tid) for s in reps]
        present = [r for r in rs if r]
        acc[tid] = {"kind": "guard", "tiers": [r.get("tier") if r else "MISSING" for r in rs],
                    "blocking": [[k for k in BLOCKING if r.get(k)] if r else None for r in rs],
                    "held_every_repeat": len(present) == 2 and all(r.get("tier") in HELD for r in present),
                    "required_leak_every_repeat": len(present) == 2 and all(any(r.get(k) for k in req) for r in present)}
    for tid in CONTROLS:
        rs = [sets[s].get(tid) for s in reps]
        present = [r for r in rs if r]
        e = {"kind": "control", "tiers": [r.get("tier") if r else "MISSING" for r in rs],
             "blocking": [[k for k in BLOCKING if r.get(k)] if r else None for r in rs],
             "notes": [[k for k in NOTES if r.get(k)] if r else None for r in rs],
             "leak_axis_clean_every_repeat": len(present) == 2 and not any(any(r.get(k) for k in LEAK) for r in present)}
        if tid in CONTROLS_PASS or tid in CONTROLS_A4:
            e["seed_every_repeat"] = len(present) == 2 and all(r.get("tier") == "SEED" for r in present)
        if tid in CONTROLS_A4:
            e["a4_noted"] = sum(bool(r.get("expectation_movable")) for r in present)
            e["pins_listed"] = sum(bool(r.get("protected_paths")) for r in present)
        if tid in CONTROLS_B1:
            e["b1_hits"] = sum(bool(r.get("overspecific_check")) for r in present)
            e["held_every_repeat"] = len(present) == 2 and all(r.get("tier") in HELD for r in present)
        acc[tid] = e
    out["acceptance"] = acc

    mv = {}
    for tid, (exp, req, note) in MOVERS.items():
        r = sets["movers"].get(tid)
        if not r:
            mv[tid] = {"tier": None, "ok": False}
            continue
        ok = cls(r) in exp and (cls(r) == "SEED" or all(r.get(k) for k in req))
        if tid == "task_000509_3d105353" and cls(r) == "HELD":
            ok = ok and bool(r.get("overspecific_check"))
        mv[tid] = {"tier": r.get("tier"), "blocking": [k for k in BLOCKING if r.get(k)], "notes": [k for k in NOTES if r.get(k)],
                   "expected": sorted(exp), "ok": ok, "expected_note_present": (bool(r.get(note)) if note else None)}
    out["movers"] = mv

    all_rows = [(s, tid, r) for s, rows in sets.items() for tid, r in rows.items() if r]
    held_on_note = [f"{s}/{tid}" for s, tid, r in all_rows if r.get("tier") != "SEED"
                    and not any(r.get(k) for k in BLOCKING + ("ambiguity", "uncertain_fact"))]
    instrument = {
        "1_pass_controls_seed_every_repeat": all(acc[t]["seed_every_repeat"] for t in CONTROLS_PASS),
        "2_leak_axis_clean_ten_controls": all(acc[t]["leak_axis_clean_every_repeat"] for t in CONTROLS),
        "3_000092_006700_seed": all(mv[t].get("tier") == "SEED" for t in ("task_000092_a7e4987d", "task_006700_8f3aa427")),
        "4_no_row_held_on_a_note_alone": not held_on_note, "held_on_note_rows": held_on_note,
        "5_a4_controls_seed_every_repeat": all(acc[t]["seed_every_repeat"] for t in CONTROLS_A4),
    }
    instrument["passes"] = all(v for k, v in instrument.items() if k[0].isdigit())
    out["instrument"] = instrument

    # regression against the v12 validation rows on the two blocking axes, and what became of v12's held rows
    agree, diff, trans, note_recall = Counter(), [], Counter(), Counter()
    for s, tid, r in all_rows:
        o = old[s].get(tid)
        if not o:
            continue
        for i, name in enumerate(("leak", "unfair")):
            a_new, a_old = axes(r)[i], axes(o)[i]
            agree[(name, a_new == a_old)] += 1
            if a_new != a_old:
                diff.append(f"{s}/{tid}: {name} v12={a_old} v13={a_new}")
        trans[(o.get("tier"), r.get("tier"))] += 1
        for k in NOTES:
            if o.get(k) and not any(o.get(b) for b in BLOCKING):
                note_recall[(k, bool(r.get(k)))] += 1
    out["vs_v12"] = {"axis_agreement": {f"{n}:{'same' if ok else 'differs'}": c for (n, ok), c in sorted(agree.items())},
                     "axis_differences": diff, "tier_transitions": {f"{a}->{b}": c for (a, b), c in sorted(trans.items(), key=str)},
                     "note_recall_on_rows_v12_held_for_a_note_only": {f"{k}:{'kept' if ok else 'dropped'}": c for (k, ok), c in sorted(note_recall.items())}}
    out["summary"] = {
        "guards_held": sum(acc[t]["held_every_repeat"] for t in GUARDS),
        "guards_required_leak": sum(acc[t]["required_leak_every_repeat"] for t in GUARDS),
        "controls_leak_clean": sum(acc[t]["leak_axis_clean_every_repeat"] for t in CONTROLS),
        "pass_controls_seed": sum(acc[t]["seed_every_repeat"] for t in CONTROLS_PASS),
        "a4_controls": {t: {"seed": acc[t]["seed_every_repeat"], "noted": acc[t]["a4_noted"], "pins": acc[t]["pins_listed"]} for t in CONTROLS_A4},
        "b1_controls": {t: {"held": acc[t]["held_every_repeat"], "b1": acc[t]["b1_hits"]} for t in CONTROLS_B1},
        "movers_ok": sum(1 for v in mv.values() if v["ok"]), "movers_failed": [t for t, v in mv.items() if not v["ok"]],
    }
    json.dump(out, open(os.path.join(HERE, "score_v13.json"), "w"), indent=1)

    print("== sets")
    for s, e in out["sets"].items():
        print(f"{s}: rows {e['rows']} tiers {e['tiers']} status {e['exploit_status']} schema_invalid {len(e['schema_invalid'])} "
              f"lint {len(e['lint_violations'])} unparseable {len(e['unparseable'])} mean_row_chars {e['mean_row_chars']}")
    print("== summary", json.dumps(out["summary"]))
    for t in GUARDS:
        print(f"  guard {t}: tiers {acc[t]['tiers']} blocking {acc[t]['blocking']}")
    for t in CONTROLS:
        print(f"  control {t}: tiers {acc[t]['tiers']} blocking {acc[t]['blocking']} notes {acc[t]['notes']}")
    print("== movers", out["summary"]["movers_ok"], "/ 18")
    for t, v in mv.items():
        print(f"  {t}: {v.get('tier')} blocking {v.get('blocking')} notes {v.get('notes')} expected {v.get('expected')}{'' if v['ok'] else '  MISS'}")
    print("== instrument", json.dumps(instrument))
    print("== vs v12", json.dumps(out["vs_v12"]["axis_agreement"]), json.dumps(out["vs_v12"]["tier_transitions"]))
    for d in diff:
        print("  ", d)
    print("== note recall", json.dumps(out["vs_v12"]["note_recall_on_rows_v12_held_for_a_note_only"]))
    lints = Counter(l for e in out["sets"].values() for v in e["lint_violations"].values() for l in v)
    print("== lints", dict(lints.most_common()))
    print("written", os.path.join(HERE, "score_v13.json"))


if __name__ == "__main__":
    main()
