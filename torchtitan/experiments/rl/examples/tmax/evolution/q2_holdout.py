#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Author Q2 acceptance controls without showing the grader or reference."""

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from q2_prepare import freeze
from q2_rewrite import subscription_driver

REQUEST = """Prepare independent Q2 acceptance controls using AGENTS.md.
The grader and reference solution are withheld. Use correct.sh as an independently
implemented valid alternative. Include semantic mistakes in retained requirements
and changed requirements, legal boundary cases, and replacement of working inputs
with a final answer wrong for the original fixture, wherever applicable.
For a removed requirement, the correct control must demonstrate that it can be
omitted while all remaining requirements are satisfied. Do not label omission of
a removed requirement as an error. For tasks requiring reusable programs, execute
witness inputs that distinguish correct and faulty behavior, then leave the faulty
program installed. For final-artifact tasks, leave an incorrect final artifact.
If changing a working input is permitted, also save valid-1.sh: a self-contained
correct solution that changes the input but leaves the required final artifacts
correct for the original task. Execute it in a fresh container.
Save coverage.json in run/verifier-probes with keys retained, changed, boundary,
input_replacement, and legal_input_change. Each value must list the control script
names and public requirements it covers, or explain precisely why it is inapplicable.
Keep controls self-contained and exit zero only after confirming their witness.
Finish after the witness runs. This evaluation will not repair the generated grader.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    config = json.loads((campaign / "input.json").read_text())
    settings = json.loads((campaign / "execution.json").read_text())
    checkout = Path(settings["checkout"])
    evo = checkout / "torchtitan/experiments/rl/examples/tmax/evolution"
    os.environ.update(
        TRL_BASE=settings["project_root"],
        TRL_TT=str(checkout),
        PYTHONPATH=str(checkout),
        TRL_VENV_PY=sys.executable,
        SYNTH_MODEL=config["model"],
        SYNTH_EFFORT=config["reasoning_effort"],
        EVOLVE_AGENT_TIMEOUT=str(config["session_timeout_sec"]),
        EVOLVE_CODEX_DRIVER="exec",
        DAYTONA_ENV_FILE=settings["daytona_env"],
        SWE_BOOT_RETRIES="1",
        TT_DAYTONA_CREATE_RETRIES="1",
        TT_DAYTONA_LABEL="andy-q2-holdout-author",
    )
    sys.path.insert(0, str(checkout))
    sys.path.insert(0, str(evo))
    import evolve_codex as ec
    from torchtitan.experiments.rl.examples.tmax import layout

    subscription_driver(ec, settings)
    directory = campaign / "runs" / args.run_id
    assert (directory / "result.json").exists()
    output = campaign / "control-inputs" / (args.run_id + ".json")
    if output.exists():
        print(f"Frozen controls already exist: {output}", flush=True)
        return
    rewrite = layout.RewriteDir(directory / "heldout")
    rewrite.path.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[
            logging.FileHandler(rewrite.path / "progress.log"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    public = directory / "rewrite/package"
    if not rewrite.package.exists():
        ec._blind_layout(public, rewrite.package)
        shutil.rmtree(rewrite.package / "tests")
        (rewrite.package / "tests").mkdir()
        (rewrite.package / "tests/test.sh").write_text("#!/bin/sh\nexit 2\n")
        for name in ("seed_size.json", "seed_literals.json"):
            (rewrite.package / "run" / name).unlink(missing_ok=True)
        shutil.copy2(
            ec.VERIFIER_SPEC.with_name("independent_verifier_probes.md"),
            rewrite.package / "AGENTS.md",
        )
    before = ec._probe_hashes(rewrite.package, ("run",))
    item = config["tasks"][args.run_id]
    request = (
        REQUEST
        + "\nOriginal public task, for identifying retained and removed requirements:\n"
        + item["row"]["metadata"]["problem_statement"]
        + "\nRequested change:\n"
        + item["change"]
        + "\nThe current instruction.md determines acceptance. Report any mismatch "
        "with the requested change in coverage.json; do not silently repair it.\n"
    )
    revision = subprocess.check_output(
        ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    audit_input = dict(
        config=config,
        execution=settings,
        evaluator_revision=revision,
        request=request,
        role=(rewrite.package / "AGENTS.md").read_text(),
        public_hashes=before,
    )
    freeze(rewrite.path / "input.json", json.dumps(audit_input, indent=2) + "\n")
    logging.info(
        "item=%s status=holdout_author_start revision=%s", args.run_id, revision
    )
    with ec.session(rewrite, "probe", timeout=ec.AGENT_TIMEOUT) as session:
        try:
            result = ec._run_codex(
                session, rewrite.package, request + ec._budget(ec.AGENT_TIMEOUT)
            )
        finally:
            ec._sandbox_down(rewrite.package)
    if result.returncode:
        raise RuntimeError(f"Control author exited {result.returncode}")
    ec._check_verdict(rewrite.package)
    assert ec._probe_hashes(rewrite.package, ("run",)) == before
    probes = rewrite.package / "run/verifier-probes"
    contract = json.loads((probes / "contract.json").read_text())
    coverage = json.loads((probes / "coverage.json").read_text())
    assert set(coverage) >= {
        "retained",
        "changed",
        "boundary",
        "input_replacement",
        "legal_input_change",
    }
    row = json.loads((directory / "row.json").read_text())
    cases = []
    scripts = [("correct", 1)] + [
        (f"wrong-{n}", 0) for n in range(1, len(contract["cases"]) + 1)
    ]
    scripts += [(p.stem, 1) for p in sorted(probes.glob("valid-*.sh"))]
    for name, expected in scripts:
        script = (probes / (name + ".sh")).read_text()
        marker = "Q2_" + name.upper().replace("-", "_") + "_DONE"
        cases.append(
            dict(
                case_id=args.run_id + "--" + name,
                expected_reward=expected,
                solve_timeout=900,
                row=row,
                solution={"solve.sh": "set -e\n" + script + "\necho " + marker + "\n"},
                completion_marker=marker,
            )
        )
    reference = {
        str(p.relative_to(public / "solution")): p.read_text()
        for p in (public / "solution").rglob("*")
        if p.is_file()
    }
    reference["reference.sh"] = reference.pop("solve.sh")
    reference[
        "solve.sh"
    ] = "set -e\nbash /solution/reference.sh\necho Q2_REFERENCE_DONE\n"
    cases.insert(
        0,
        dict(
            case_id=args.run_id + "--reference",
            expected_reward=1,
            solve_timeout=900,
            row=row,
            solution=reference,
            completion_marker="Q2_REFERENCE_DONE",
        ),
    )
    freeze(
        output,
        json.dumps(
            dict(
                label="andy-q2-heldout-controls",
                concurrency=len(cases),
                cases=cases,
                contract=contract,
                coverage=coverage,
                author_input=audit_input,
                scripts_sha256={
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in probes.iterdir()
                    if p.is_file()
                },
            ),
            indent=2,
        )
        + "\n",
    )
    logging.info(
        "item=%s status=holdout_controls_frozen count=%d grading=pending",
        args.run_id,
        len(cases),
    )


if __name__ == "__main__":
    main()
