#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Freeze reference controls and the predefined traffic regression controls."""

import argparse
import json
from pathlib import Path

from q2_prepare import freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    directory = args.campaign
    config = json.loads((directory / "input.json").read_text())
    item = config["tasks"][args.run_id]
    if args.baseline:
        package = directory / "seeds" / item["seed_id"]
        row = item["row"]
        name = item["seed_id"] + "--baseline"
    else:
        package = directory / "runs" / args.run_id / "rewrite/package"
        row = json.loads((directory / "runs" / args.run_id / "row.json").read_text())
        name = args.run_id
    files = {
        str(p.relative_to(package / "solution")): p.read_text()
        for p in (package / "solution").rglob("*")
        if p.is_file()
    }
    files["reference.sh"] = files.pop("solve.sh")
    cases = []

    def add(suffix, expected, script, requirement):
        marker = "Q2_" + suffix.upper() + "_DONE"
        solution = {
            **files,
            "solve.sh": "set -e\n" + script + "\necho " + marker + "\n",
        }
        cases.append(
            dict(
                case_id=name + "--" + suffix,
                expected_reward=expected,
                solve_timeout=900,
                row=row,
                solution=solution,
                completion_marker=marker,
                requirement=requirement,
            )
        )

    add("reference", 1, "bash /solution/reference.sh", "All public requirements")
    if not args.baseline:
        assert item["category"] == "regression"
        alternative = (directory / "controls-source/traffic_alternative.sh").read_text()
        add(
            "valid_alternative",
            1,
            alternative,
            "Equivalent formatting and implementation are permitted",
        )
        add(
            "incorrect_summary",
            0,
            alternative
            + """
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/home/user/redaction_summary.json')
v = json.loads(p.read_text())
assert v['authorization_redactions'] == 2
v['authorization_redactions'] = 3
p.write_text(json.dumps(v))
print('Expected authorization_redactions=2; observed=3')
PY
""",
            "Summary counts reflect the original capture",
        )
        add(
            "drops_unrelated_headers",
            0,
            alternative
            + """
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/home/user/clean_traffic.json')
v = json.loads(p.read_text())
dropped = []
for row in v:
    for key in list(row['headers']):
        if key.lower() not in ('authorization', 'set-cookie'):
            dropped.append(key)
            del row['headers'][key]
assert dropped
p.write_text(json.dumps(v))
print('Expected retained headers; deleted:', dropped)
PY
""",
            "Retain unrelated headers and complete records",
        )
        add(
            "replaced_input_wrong_summary",
            0,
            alternative
            + """
python3 - <<'PY'
import json
from pathlib import Path
p = Path('/home/user/redaction_summary.json')
v = json.loads(p.read_text())
assert v == dict(authorization_redactions=2, insecure_cookie_responses=3, unique_blocked_ips=2)
Path('/home/user/traffic.json').write_text('[]')
p.write_text(json.dumps(dict.fromkeys(v, 0)))
print('Original counts=2,3,2; submitted counts=0,0,0; working input=[]')
PY
""",
            "Summary refers to the original capture even if the working input changes",
        )
        add(
            "replaced_input_correct_summary",
            1,
            alternative
            + """
python3 - <<'PY'
import json
from pathlib import Path
v = json.loads(Path('/home/user/redaction_summary.json').read_text())
assert v == dict(authorization_redactions=2, insecure_cookie_responses=3, unique_blocked_ips=2)
Path('/home/user/traffic.json').write_text('[]')
print('Original counts=2,3,2; submitted counts=2,3,2; working input=[]')
PY
""",
            "The public task permits a changed working input with correct final artifacts",
        )
    target = directory / "control-inputs" / (name + ".json")
    freeze(
        target,
        json.dumps(
            dict(
                label="andy-q2-trusted-inputs-controls",
                concurrency=len(cases),
                cases=cases,
                experiment_input=item,
                generation_config=config,
                assessment="Predefined controls; semantic review required in addition to numeric rewards",
            ),
            indent=2,
        )
        + "\n",
    )
    print(json.dumps(dict(path=str(target), controls=len(cases))))


if __name__ == "__main__":
    main()
