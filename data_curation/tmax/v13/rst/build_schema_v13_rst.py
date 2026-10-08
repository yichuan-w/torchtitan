#!/usr/bin/env python3
"""Build output_schema_v13_rst.json: the v13 row schema plus the one field the RST variant adds.

Everything is taken from the frozen v13 builder (one directory up) so the two schemas cannot drift apart. Differences:
  rubric_version   const "v13-rst"
  task_id          RST ids only (rts_task_<hex>)
  bootstrap_fetch  new, placed after unstable_reward: null, or a string anchored in tests/test.sh. It records the
                   grade-time fetches of the shared harness bootstrap (apt, the uv installer, PyPI). It appears in NO
                   cross-field rule on purpose: it never holds a row back. lint_v13_rst.py checks it against the
                   bootstrap facts the stager extracted mechanically.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "tools")):   # the v13 builder sits beside the
    sys.path.insert(0, _d)                                                         # prompt, or one level down in tools/
import build_schema_v13 as v13  # noqa: E402

BOOT_ANCHOR = r"^tests/test\.sh:[0-9]+(-[0-9]+)?\b"

props = {}
for key, spec in v13.PROPS.items():
    props[key] = spec
    if key == "unstable_reward":
        props["bootstrap_fetch"] = {"anyOf": [{"type": "null"}, {"type": "string", "pattern": BOOT_ANCHOR,
                                                                 "minLength": 24}]}
props["rubric_version"] = {"const": "v13-rst"}
props["task_id"] = {"type": "string", "pattern": r"^rts_task_[0-9a-f]{8,32}$"}

SCHEMA = dict(v13.SCHEMA)
SCHEMA.update({"$id": "rst-seed-audit-row-v13-rst", "title": "RST seed audit row, rubric v13-rst",
               "description": "One row per task. Blocking: A1 A2 A3 B1 B3. Notes (no tier or verdict effect): A4 A6 A7. "
                              "bootstrap_fetch: harness record, no tier or verdict effect, in no cross-field rule.",
               "required": list(props), "properties": props})

if __name__ == "__main__":
    out = os.path.join(HERE, "output_schema_v13_rst.json")
    json.dump(SCHEMA, open(out, "w"), indent=1)
    print("written", out, "|", len(props), "fields |", len(SCHEMA["allOf"]), "cross-field rules (unchanged from v13)")
