#!/usr/bin/env python3
"""Score the V12 600-task production run.

Not an acceptance profile (these tasks have no pre-registered gold apart from the 20 embedded instrument ids).
It reports: coverage, schema validity, lints L1-L15, the tier/verdict/reject_shape/per-side distributions, the
embedded instrument (10 guards must be non-PASS with a floor field; 4 controls must be PASS; the 6 A4/B1 controls
must be non-PASS on their axis and leak-clean), and the V8-vs-V12 confusion matrix with the moved ids by mechanism --
the measurement this run exists for. Prints aggregates and ids only; no task prose.
"""
import glob
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = os.path.join(HERE, "run600")
V12 = os.path.abspath(os.path.join(HERE, ".."))
OF = os.path.abspath(os.path.join(V12, ".."))
SCHEMA = os.path.join(V12, "output_schema_v12.json")
sys.path.insert(0, os.path.join(V12, "validation"))
import lint_v12  # noqa: E402

MAN = json.load(open(os.path.join(RUN, "manifest.json")))
SELECTED = MAN["selected"]
V8_TIER = MAN["v8_tier_of_each"]
INSTRUMENT_GUARDS = ["task_000156_2f395118", "task_000241_547763df", "task_000311_9e22fec5", "task_004863_67089cd4",
                     "task_004886_10504160", "task_000221_fb2fd737", "task_000872_cf99cb19", "task_001068_ba585e13",
                     "task_001257_21489dc1", "task_000501_3c7739f3"]
INSTRUMENT_PASS = ["task_000316_02020782", "task_001104_dffa14ac", "task_000854_5a8ded74", "task_005042_071a51df"]
INSTRUMENT_A4 = ["task_000681_6f824421", "task_000927_43407097", "task_000056_f726766b"]
INSTRUMENT_B1 = ["task_007102_d981ab89", "task_000220_ab2204ea", "task_002378_139ea7d2"]
LEAK = ("oracle_reachable", "expectation_revealed", "value_derivable")
A_FIELDS = LEAK + ("expectation_movable", "weak_verifier_exploit", "core_clause_unenforced")
B_FIELDS = ("overspecific_check", "env_mismatch", "unstable_reward")
RULE_FIELDS = A_FIELDS + B_FIELDS + ("ambiguity", "trivial")
FLOOR = LEAK + ("expectation_movable", "core_clause_unenforced", "overspecific_check", "env_mismatch", "unstable_reward")
SHAPES = ("instruction_prints_answer", "deliverable_is_leaked_material", "broken_premise", "pre_solved")

_files = {}


def task_files(tid):
    if tid not in _files:
        d = os.path.join(RUN, "tasks", tid)
        _files[tid] = {n: (open(os.path.join(d, n), errors="replace").read() if os.path.exists(os.path.join(d, n)) else "")
                       for n in ("instruction.md", "setup.sh", "tests/test.sh")}
    return _files[tid]


def candidates(tid):
    p = os.path.join(RUN, "candidates", f"{tid}.json")
    return json.load(open(p)) if os.path.exists(p) else {"programs": [], "failing_statements": [], "imports": []}


def fired(r):
    return [k for k in RULE_FIELDS if r.get(k)]


PIN_WORDS = ("hash-pin", "hash pin", "sha256", "sha-256", "checksum the", "pin the", "pinned", "regenerate", "regenerated",
             "generate the fixture", "write the fixture", "snapshot")


def hook_replaceable(r):
    """The 'pure pin' class: a defect a generic training-side hook (snapshot the declared inputs before rollout,
    restore them before grading) would neutralise, so no per-task verifier repair is needed.

    Mechanical test: the only floor that fired is A4 (`expectation_movable`), the row is REPAIR, and the named
    `verifier_fix` is a pin / regenerate of material (not a behavioural change to the checks). Returns
    'pure' (only A4 fired), 'partial' (A4 among several fired floors, pin fix named) or None.
    """
    if r.get("tier") != "REPAIR" or not r.get("expectation_movable"):
        return None
    fix = (r.get("verifier_fix") or "").lower()
    if not any(w in fix for w in PIN_WORDS):
        return None
    floors = [k for k in FLOOR if r.get(k)]
    return "pure" if floors == ["expectation_movable"] else "partial"


def main():
    import jsonschema
    validator = jsonschema.Draft202012Validator(json.load(open(SCHEMA)))
    rows, unparse = {}, []
    for f in sorted(glob.glob(os.path.join(RUN, "rows", "*.jsonl"))):
        tid = os.path.basename(f)[:-6]
        lines = [l for l in open(f).read().splitlines() if l.strip()]
        try:
            rows[tid] = json.loads(lines[0]) if lines else None
        except json.JSONDecodeError:
            rows[tid] = None
        if rows.get(tid) is None:
            unparse.append(tid)
    missing = [t for t in SELECTED if t not in rows]
    extra = [t for t in rows if t not in set(SELECTED)]

    tiers, verdicts, shapes, sa, sb, held = Counter(), Counter(), Counter(), Counter(), Counter(), 0
    schema_bad, lint_bad, fired_counts = {}, {}, Counter()
    for tid, r in rows.items():
        if r is None:
            continue
        tiers[r.get("tier")] += 1
        verdicts[r.get("verdict")] += 1
        if r.get("reject_shape"):
            shapes[r["reject_shape"]] += 1
        sa[r.get("exploit_status_a")] += 1
        sb[r.get("exploit_status_b")] += 1
        if r.get("unconfirmed_dependency"):
            held += 1
        for k in fired(r):
            fired_counts[k] += 1
        errs = [e.message[:90] for e in validator.iter_errors(r)]
        if errs:
            schema_bad[tid] = errs[:2]
        try:
            lv = lint_v12.lint(task_files(tid), r, candidates(tid))
        except Exception as ex:
            lv = [f"LINT-ERROR:{type(ex).__name__}"]
        if lv:
            lint_bad[tid] = lv

    inst = {}
    for tid in INSTRUMENT_GUARDS:
        r = rows.get(tid)
        inst[tid] = {"kind": "guard", "tier": r and r.get("tier"), "floor": bool(r) and any(r.get(k) for k in FLOOR),
                     "ok": bool(r) and r.get("tier") != "PASS" and any(r.get(k) for k in FLOOR), "fired": r and fired(r)}
    for tid in INSTRUMENT_PASS:
        r = rows.get(tid)
        inst[tid] = {"kind": "pass-required", "tier": r and r.get("tier"), "ok": bool(r) and r.get("tier") == "PASS",
                     "held": bool(r) and bool(r.get("unconfirmed_dependency"))}
    for tid, axis in [(t, "A4") for t in INSTRUMENT_A4] + [(t, "B1") for t in INSTRUMENT_B1]:
        r = rows.get(tid)
        key = "expectation_movable" if axis == "A4" else "overspecific_check"
        inst[tid] = {"kind": axis, "tier": r and r.get("tier"), "ok": bool(r) and r.get("tier") != "PASS" and bool(r.get(key)),
                     "leak_clean": bool(r) and not any(r.get(k) for k in LEAK)}
    leak_clean_controls = all(inst[t].get("leak_clean", True) for t in INSTRUMENT_PASS + INSTRUMENT_A4 + INSTRUMENT_B1 if t in inst)
    for t in INSTRUMENT_PASS:
        r = rows.get(t)
        inst[t]["leak_clean"] = bool(r) and not any(r.get(k) for k in LEAK)
    instrument_summary = {
        "guards_ok": sum(inst[t]["ok"] for t in INSTRUMENT_GUARDS), "guards_total": len(INSTRUMENT_GUARDS),
        "pass_required_ok": sum(inst[t]["ok"] for t in INSTRUMENT_PASS), "pass_required_total": len(INSTRUMENT_PASS),
        "a4_ok": sum(inst[t]["ok"] for t in INSTRUMENT_A4), "b1_ok": sum(inst[t]["ok"] for t in INSTRUMENT_B1),
        "controls_leak_axis_clean": all(inst[t]["leak_clean"] for t in INSTRUMENT_PASS + INSTRUMENT_A4 + INSTRUMENT_B1),
        "every_reject_names_a_shape": all((r.get("reject_shape") in SHAPES) for r in rows.values() if r and r.get("tier") == "REJECT-PROVEN"),
    }

    pairs, moved = Counter(), defaultdict(list)
    for tid, r in rows.items():
        if not r:
            continue
        v8 = V8_TIER.get(tid)
        pairs[(v8, r.get("tier"))] += 1
        if v8 != r.get("tier"):
            moved[f"{v8}->{r.get('tier')}"].append(f"{tid}:{'+'.join(fired(r)) or 'none'}{':' + r['reject_shape'] if r.get('reject_shape') else ''}")
    hook = {"pure": [], "partial": []}
    for tid, r in rows.items():
        if r and (k := hook_replaceable(r)):
            hook[k].append(tid)

    v8_pass = [t for t, r in rows.items() if r and V8_TIER.get(t) == "PASS"]
    kept = [t for t in v8_pass if rows[t].get("tier") == "PASS"]
    leaked = [t for t in v8_pass if rows[t] and any(rows[t].get(k) for k in LEAK)]
    v8_nonpass = [t for t, r in rows.items() if r and V8_TIER.get(t) in ("REPAIR", "REVIEW", "REJECT-PROVEN")]
    admitted = [t for t in v8_nonpass if rows[t].get("tier") == "PASS"]

    out = {
        "run": MAN["run"], "selected": len(SELECTED), "judged": len([r for r in rows.values() if r]),
        "missing": missing, "extra": extra, "unparseable": unparse,
        "tiers": dict(tiers), "verdicts": dict(verdicts), "reject_shapes": dict(shapes), "pass_held": held,
        "exploit_status_a": dict(sa), "exploit_status_b": dict(sb), "fired_field_counts": dict(fired_counts.most_common()),
        "schema_invalid": schema_bad, "lint_violations": lint_bad,
        "lint_distribution": dict(Counter(l for v in lint_bad.values() for l in v).most_common()),
        "instrument": inst, "instrument_summary": instrument_summary,
        "hook_replaceable": {"pure": sorted(hook["pure"]), "partial": sorted(hook["partial"]),
                             "n_pure": len(hook["pure"]), "n_partial": len(hook["partial"]),
                             "note": "pure = the only fired floor is A4 (movable ground truth) and the named fix is a pin/regenerate; "
                                     "a training-side hook that snapshots declared inputs before rollout and restores them before "
                                     "grading neutralises these without a per-task verifier change. partial = A4 with a pin fix "
                                     "alongside other fired floors, which still need their own repair."},
        "v8_vs_v12": {"pairs": {f"{a}->{b}": n for (a, b), n in sorted(pairs.items(), key=lambda x: -x[1])},
                      "agreement": sum(n for (a, b), n in pairs.items() if a == b), "compared": sum(pairs.values()),
                      "v8_PASS_judged": len(v8_pass), "v8_PASS_kept_PASS": len(kept),
                      "v8_PASS_with_a_leak_field": len(leaked), "v8_PASS_leak_ids": sorted(leaked)[:40],
                      "v8_nonPASS_judged": len(v8_nonpass), "v8_nonPASS_admitted_PASS": len(admitted),
                      "moved": {k: v for k, v in sorted(moved.items(), key=lambda x: -len(x[1]))}},
    }
    json.dump(out, open(os.path.join(HERE, "score_run600.json"), "w"), indent=1)

    print(f"== coverage {out['judged']}/{out['selected']} judged | missing {len(missing)} | unparseable {len(unparse)} | extra {len(extra)}")
    print(f"== tiers {out['tiers']} | PASS-held {held} | shapes {out['reject_shapes']}")
    print(f"== verdicts {out['verdicts']}")
    print(f"== fired fields {out['fired_field_counts']}")
    print(f"== per side A {out['exploit_status_a']} B {out['exploit_status_b']}")
    print(f"== schema invalid {len(schema_bad)} | rows with lint {len(lint_bad)} {out['lint_distribution']}")
    print(f"== hook-replaceable (training-side pin would neutralise): pure {len(hook['pure'])}, partial {len(hook['partial'])}")
    print("== embedded instrument", json.dumps(instrument_summary))
    for t in INSTRUMENT_GUARDS + INSTRUMENT_PASS + INSTRUMENT_A4 + INSTRUMENT_B1:
        i = inst[t]
        print(f"   {t} [{i['kind']}] tier {i['tier']} ok={i['ok']}")
    c = out["v8_vs_v12"]
    print(f"== V8 vs V12: agreement {c['agreement']}/{c['compared']}")
    print(f"   V8 PASS judged {c['v8_PASS_judged']} -> kept PASS {c['v8_PASS_kept_PASS']} ({100*c['v8_PASS_kept_PASS']//max(c['v8_PASS_judged'],1)}%), carrying a leak field {c['v8_PASS_with_a_leak_field']}")
    print(f"   V8 non-PASS judged {c['v8_nonPASS_judged']} -> admitted PASS {c['v8_nonPASS_admitted_PASS']}")
    for k, n in c["pairs"].items():
        print(f"   {k}: {n}")
    print("written", os.path.join(HERE, "score_run600.json"))


if __name__ == "__main__":
    main()
