#!/usr/bin/env python3
"""Recover the tasks the loop threw away for being solved every time.

The loop's difficulty verdict is measured with GPT-5.6-sol, and GPT is there to
find *broken* tasks: a task the strongest available solver cannot do in k tries
is worth auditing, and that screen works. What the same measurement cannot do is
say a task carries no training signal, because the model being trained is Qwen,
not GPT. `too_easy` conflates the two — it reads "GPT solved it 4 of 4" as "no
gradient" and drops the task.

So this pulls those records back out. It writes task packages exactly as the loop
would have, plus a manifest, so they can be re-measured against whatever solver
is actually the training target. Nothing here decides they are good; it only
stops the decision from having already been made by the wrong model.

Task files come from the record when it carries them and from the work directory
otherwise. The record does not store that directory — the loop derives it from
`--out`, a round number and the task id, and only the first of those survives in
the results file — so `--task-root` takes the same template and rebuilds it.
Records whose files are in neither place are reported, not skipped silently: a
count of what could not be recovered is the point of the exercise.

Usage: recover_too_easy.py \\
           --results 'baseline-v19/synth_v19_p*.jsonl' \\
           --task-root 'data/synth-{version}/round_{round}/{task_id}' \\
           --out data/too-easy-recovered
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from pathlib import Path

FILES = {"instruction": "instruction.md",
         "dockerfile": "environment/Dockerfile",
         "solve_sh": "solution/solve.sh",
         "test_state_py": "tests/test_state.py"}


def files_from_record(rec: dict) -> dict[str, str] | None:
    """The record's own copy of the four files, if it kept one."""
    task = rec.get("task") or rec.get("final_task") or {}
    if all(task.get(k) for k in FILES):
        return {k: task[k] for k in FILES}
    return None


def files_from_disk(rec: dict, template: str, version: str) -> dict[str, str] | None:
    """The work directory the loop built the image from, if it still exists.

    The loop never writes the path down, so it is rebuilt from the pieces that
    do survive: the `--out` root (given here as part of the template), the round,
    and the task id.
    """
    work = rec.get("work") or rec.get("dir") or rec.get("work_dir")
    if not work:
        if not template:
            return None
        work = template.format(version=version, round=rec.get("round", 1),
                               task_id=rec.get("task_id", ""))
    d = Path(work)
    if not all((d / p).exists() for p in FILES.values()):
        return None
    return {k: (d / p).read_text(errors="replace") for k, p in FILES.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--out", default="data/too-easy-recovered")
    ap.add_argument("--status", default="too_easy")
    ap.add_argument("--task-root",
                    default="data/synth-{version}/round_{round}/{task_id}",
                    help="where the loop's --out put this run's task packages")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest, lost = [], []

    paths = [p for pat in args.results for p in sorted(glob.glob(pat))]
    for path in paths:
        version = re.search(r"v\d+", Path(path).name)
        version = version.group(0) if version else "?"
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("status") != args.status:
                    continue
                tid = rec.get("task_id") or rec.get("id") or f"t{len(manifest)}"
                files = (files_from_record(rec)
                         or files_from_disk(rec, args.task_root, version))
                if not files:
                    lost.append(tid)
                    continue
                d = out / tid
                for key, rel in FILES.items():
                    p = d / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(files[key])
                manifest.append({
                    "task_id": tid, "version": version, "source": path,
                    "gpt_pass_at_k": rec.get("pass_at_k"),
                    "seed_id": rec.get("seed_id"), "operator": rec.get("operator"),
                    "retune_rounds": len(rec.get("retune_history") or [])})

    (out / "manifest.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in manifest))
    print(f"recovered {len(manifest)} tasks to {out}")
    if lost:
        print(f"  {len(lost)} records carried neither files nor a live work dir; "
              f"they can only be recovered by re-running: {', '.join(lost[:5])}"
              + (" ..." if len(lost) > 5 else ""))


if __name__ == "__main__":
    main()
