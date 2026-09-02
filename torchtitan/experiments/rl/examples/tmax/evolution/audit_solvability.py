#!/usr/bin/env python3
"""Read each task and judge whether an agent could have solved it.

A reference solution passing its verifier proves the task is internally
consistent. It does not prove the task is solvable from the instruction, because
the reference was written by someone who already knew the answer and is free to
hardcode it — one task here is a crackme whose solution simply contains the
password.

Five lexical detectors were tried before this and none separated the tasks a
solver never managed from the ones it always did. That is the wrong instrument:
"is the information an agent needs actually reachable" is a reading task, so this
reads. The model gets the instruction, the reference solution, the verifier and
the environment, and answers four questions with evidence for each.

An earlier version had an `environment` verdict for tasks needing the network.
Crossed against measured pass@5 it carried no information — flagged tasks were
solved at 0.85 against 0.88 for clean ones, because the container has network
access and installing things simply works. The auditor was guessing at a runtime
fact it did not know, so the prompt now states that fact instead of asking. The
verdict it freed up went to the opposite defect the old version missed entirely:
a verifier so loose that a stub file passes, which is exactly the task that
teaches a model to hack rewards.

Usage:
  audit_solvability.py --ids results/zero_pass_ids.txt --tar tasks.tar \\
      --results results/solvability_audit.jsonl [--workers 6]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import synth_client as llm  # noqa: E402

PROMPT = """You are auditing one terminal task for defects, not for quality.

The task ships an instruction, an environment, a reference solution and a
verifier. Facts you may rely on, because they were established by running, not by
reading: the image builds, the reference solution passes the verifier, and the
container HAS working network access while the agent solves — `apt-get`, `pip`
and `curl` work. Needing the network is therefore normal, not a defect; tasks
that install tools at solve time get solved. Do not flag network use.

Your question: **is this task broken as a training example?** It can be broken in
two opposite directions, and you are looking for both:

Too strict — the agent cannot win:

1. Does `solution/solve.sh` use any value — a password, a version, an exact
   string, a magic number, a path — that appears nowhere in `instruction.md`, the
   Dockerfile, or the environment files, and could not be derived by running
   something inside the container? Quote it. (Derivable at runtime counts as
   fine: a checksum the agent can compute, a version a command prints.)
2. Does the verifier require a *mechanism* rather than a *result*: a particular
   tool, a virtualenv, a specific intermediate file, when the instruction only
   states the goal? Quote the assertion.
3. Does the verifier compare against an exact literal that depends on one
   particular way of doing the task, where another correct way would produce
   something different? Quote it.

Too loose — the agent can win without doing the task:

4. Could a shortcut satisfy every assertion in the verifier while ignoring what
   the instruction actually asks — writing a stub file, echoing an expected
   string, creating an empty artifact? State the shortest command sequence that
   would pass, or say none exists. A verifier that only checks a file exists and
   is non-empty, for a task that asks for real work, is the canonical case.

Then give one verdict:

  solvable        an agent with the instruction and the container has everything
                  it needs, and passing requires actually doing the task; if it
                  fails, that is difficulty
  underspecified  the instruction omits something the verifier requires, and the
                  agent could do the job correctly and still fail
  unsolvable      the reference solution knows something no agent could learn
                  from what it is given
  hackable        a shortcut passes the verifier without doing the task

When both directions are broken, report the stricter one (unsolvable or
underspecified) and still fill in the shortcut field.

Return schema:
{{"verdict":"solvable|underspecified|unsolvable|hackable",
  "hardcoded":"quoted value or empty","mechanism":"quoted assertion or empty",
  "brittle_literal":"quoted literal or empty",
  "shortcut":"the command sequence that would pass, or empty",
  "why":"two sentences, concrete"}}

--- instruction.md ---
{instruction}

--- solution/solve.sh ---
{solution}

--- tests/test_state.py ---
{tests}

--- environment/Dockerfile ---
{dockerfile}

--- other files in environment/ ---
{env_files}"""


def audit_one(task: dict) -> dict:
    rec = {"task_id": task["task_id"], "t_start": time.time()}
    mark = dict(llm.USAGE)
    try:
        out = llm._parse_json(llm.chat([
            {"role": "system", "content": "Return ONLY valid JSON."},
            {"role": "user", "content": PROMPT.format(
                instruction=task["instruction"][:6000],
                solution=task["solution"][:8000],
                tests=task["tests"][:12000],
                dockerfile=task["dockerfile"][:4000],
                env_files=task["env_files"] or "(none)")}], max_tokens=3000))
    except Exception as e:  # noqa: BLE001
        return {**rec, "verdict": "error", "why": f"{type(e).__name__}: {e}"[:200]}
    rec.update({k: out.get(k, "") for k in
                ("verdict", "hardcoded", "mechanism", "brittle_literal",
                 "shortcut", "why")})
    rec["usage"] = llm.usage_since(mark)
    return rec


def load(tar_path: Path, ids: set[str]) -> list[dict]:
    got: dict[str, dict] = {}
    with tarfile.open(tar_path) as tf:
        for m in tf.getmembers():
            parts = m.name.split("/")
            if len(parts) < 3 or parts[1] not in ids or not m.isfile():
                continue
            tid, rel = parts[1], "/".join(parts[2:])
            d = got.setdefault(tid, {"task_id": tid, "env_files": "",
                                     "instruction": "", "solution": "",
                                     "tests": "", "dockerfile": ""})
            body = tf.extractfile(m).read().decode("utf-8", "replace")
            if rel == "instruction.md":
                d["instruction"] = body
            elif rel == "solution/solve.sh":
                d["solution"] = body
            elif rel == "tests/test_state.py":
                d["tests"] = body
            elif rel == "environment/Dockerfile":
                d["dockerfile"] = body
            elif rel.startswith("environment/"):
                d["env_files"] += f"\n--- {rel} ---\n{body[:2000]}"
    return list(got.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True)
    ap.add_argument("--tar", required=True, nargs="+")
    ap.add_argument("--results", required=True)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    ids = {t.strip() for t in Path(args.ids).read_text().split() if t.strip()}
    done = set()
    out_path = Path(args.results)
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["task_id"])

    tasks = []
    for tar in args.tar:
        tasks += [t for t in load(Path(tar), ids - done)
                  if t["task_id"] not in {x["task_id"] for x in tasks}]
    print(f"{len(ids)} ids, {len(done)} already audited, {len(tasks)} to read")

    with ThreadPoolExecutor(max_workers=args.workers) as ex, \
            open(out_path, "a") as fh:
        futs = [ex.submit(audit_one, t) for t in tasks]
        for n, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"[{n}/{len(tasks)}] {rec['task_id']} -> {rec.get('verdict')}"
                  f"  {str(rec.get('why'))[:90]}")


if __name__ == "__main__":
    main()
