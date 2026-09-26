#!/usr/bin/env python3
"""Self-test of output_schema_v13_rst.json. Takes rows that are valid under v13 (the GLM low-effort validation arm),
turns them into v13-rst rows, and checks what must and must not validate. Prints counts only. Portable: it runs
from the working tree and from the repository layout alike."""
import copy, glob, json, os, sys
import jsonschema
HERE = os.path.dirname(os.path.abspath(__file__))
V = jsonschema.Draft202012Validator(json.load(open(os.path.join(HERE, "output_schema_v13_rst.json"))))
BF = "tests/test.sh:8-14 apt-get -> distro mirrors: curl; curl -> astral.sh: uv 0.9.7; uvx -> pypi.org: pytest, pytest-json-ctrf"


def rst(row, bf=BF):
    out = {}
    for k, v in row.items():
        out[k] = v
        if k == "unstable_reward":
            out["bootstrap_fetch"] = bf
    out["rubric_version"] = "v13-rst"
    out["task_id"] = "rts_task_0123456789abcdef01234567"
    return out


# Source rows that are valid under v13: the GLM low-effort validation arm, as separate row files in the working tree
# or as the one bundle the repository ships (results/glm53_low.rows.jsonl).
src = []
for f in sorted(glob.glob(os.path.join(HERE, "..", "validation_glm53_low", "rows_*", "*.jsonl"))):
    try:
        src.append(json.loads(open(f).readline()))
    except Exception:
        pass
bundle = os.path.join(HERE, "..", "results", "glm53_low.rows.jsonl")
if not src and os.path.exists(bundle):
    for line in open(bundle):
        if line.strip():
            r = json.loads(line)
            r.pop("_set", None)
            src.append(r)
if not src:
    print("no v13 source rows found (looked for ../validation_glm53_low/rows_* and ../results/glm53_low.rows.jsonl)")
    sys.exit(2)
ok = lambda r: not list(V.iter_errors(r))
must_pass = must_fail = 0
bad = []
tiers = {}
for r in src:
    tiers.setdefault(r["tier"], r)
    for case, row in (("with record", rst(r)), ("null record", rst(r, None))):
        must_pass += 1
        if not ok(row):
            bad.append(("should validate", r["task_id"], case))
for tier, r in tiers.items():
    base = rst(r)
    neg = {"field missing": {k: v for k, v in base.items() if k != "bootstrap_fetch"},
           "anchored in setup.sh": dict(base, bootstrap_fetch="setup.sh:3-5 apt-get -> distro mirrors: curl"),
           "no anchor": dict(base, bootstrap_fetch="apt-get, astral.sh and pypi at grade time"),
           "v13 version string": dict(base, rubric_version="v13"),
           "tmax id": dict(base, task_id="task_000123_0123abcd"),
           "unknown key": dict(base, harness_note="x")}
    for case, row in neg.items():
        must_fail += 1
        if ok(row):
            bad.append(("should NOT validate", tier, case))
print(f"source rows {len(src)} | must-validate cases {must_pass} | must-fail cases {must_fail} | wrong {len(bad)}")
for b in bad[:10]:
    print("  ", b)
sys.exit(1 if bad else 0)
