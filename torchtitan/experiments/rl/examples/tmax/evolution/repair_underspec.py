#!/usr/bin/env python3
"""Fill in an underspecified task's instruction, and prove it filled.

An underspecified task's verifier requires something the instruction never
states, so an agent could do the job correctly and still fail. The fix is not to
weaken the verifier — it is right — but to add to instruction.md the requirement
it and the reference solution both already assume.

Per task:
  1. diagnose  read the four files; if the verdict is not underspecified, skip
  2. repair    SPEC_REPAIR adds the missing requirement to instruction.md only
  3. oracle    the reference solution must still pass (it never changed, so this
               is a check that nothing else moved)
  4. re-audit  a fresh reading must no longer call it underspecified — that is
               the proof the instruction now says what the verifier needs

Leaves as `spec_fixed`, `still_underspecified`, `oracle_broken`, or `unproven`.
Repaired packages go to --out; sources are never touched.

Usage:
  repair_underspec.py --audit results/seed_solvability_v2.jsonl \\
      --tar chunk000.tar --out data/seed-spec-fixed \\
      --results results/repair_underspec.jsonl [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import docker_validate as dv
import synth_client as llm
import synth_loop as sl

FILES = {"instruction": "instruction.md",
         "dockerfile": "environment/Dockerfile",
         "solve_sh": "solution/solve.sh",
         "test_state_py": "tests/test_state.py"}


def image_tag(tid: str) -> str:
    import re
    return "usp-" + (re.sub(r"[^a-z0-9_.-]", "", tid.lower()).strip("_.-") or "t")


def as_task(files: dict) -> dict:
    return {"instruction": files["instruction.md"],
            "solve_sh": files["solution/solve.sh"],
            "test_state_py": files["tests/test_state.py"],
            "dockerfile": files["environment/Dockerfile"]}


def repair_one(tar_path: Path, tid: str, out_root: Path) -> dict:
    rec: dict = {"task_id": tid, "t_start": time.time()}
    work = out_root / tid
    image = image_tag(tid)
    try:
        with tarfile.open(tar_path) as tf:
            dv.extract(tf, tid, work)
        files = {p: (work / p).read_text(errors="replace")
                 for p in FILES.values()}

        # What is missing, in the auditor's own words — the finding SPEC_REPAIR
        # fills. Re-read here rather than trust the stored verdict: the task on
        # disk is what gets repaired, so its own diagnosis is what should drive it.
        diag = llm.diagnose_unsolved(as_task(files))
        rec["verdict_before"] = diag.get("verdict")
        if diag.get("verdict") != "underspecified":
            return {**rec, "status": "not_underspecified",
                    "why": f"reads as {diag.get('verdict')}"}
        finding = (f"The verifier requires: {diag.get('mechanism') or diag.get('why')}. "
                   f"Evidence: {diag.get('evidence') or diag.get('why')}")

        out = llm._repair(llm.SPEC_REPAIR, {}, files, "{}", finding=finding[:1500])
        if out.get("instruction.md"):
            files["instruction.md"] = out["instruction.md"]
            (work / "instruction.md").write_text(files["instruction.md"])
        else:
            return {**rec, "status": "blocked",
                    "why": "SPEC_REPAIR returned no instruction"}

        ok, tail = sl.build_image(work, image)
        if not ok:
            return {**rec, "status": "unproven", "why": f"build: {tail[-200:]}"}
        oracle = sl.oracle_check(work, image, tid)
        rec["oracle_ok"] = oracle.get("ok")
        if not oracle.get("ok"):
            return {**rec, "status": "oracle_broken",
                    "test_tail": oracle.get("test_tail", "")[-200:]}

        # Proof: a fresh reading no longer calls it underspecified.
        after = llm.diagnose_unsolved(as_task(files))
        rec["verdict_after"] = after.get("verdict")
        if after.get("verdict") == "underspecified":
            return {**rec, "status": "still_underspecified"}
        return {**rec, "status": "spec_fixed", "out_dir": str(work)}
    except Exception as e:  # noqa: BLE001
        return {**rec, "status": "error", "why": f"{type(e).__name__}: {e}"[:200]}
    finally:
        rec["t_end"] = time.time()
        dv.sh(["docker", "rmi", "-f", image], 300)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--tar", required=True)
    ap.add_argument("--out", default="data/seed-spec-fixed")
    ap.add_argument("--results", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    todo = []
    for line in Path(args.audit).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("verdict") == "underspecified":
            todo.append(r["task_id"])
    done = set()
    out_path = Path(args.results)
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["task_id"])
    todo = [t for t in todo if t not in done]
    if args.limit:
        todo = todo[:args.limit]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"{len(todo)} underspecified tasks to repair, {len(done)} done")

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex, \
            open(out_path, "a") as fh:
        futs = [ex.submit(repair_one, Path(args.tar), t, out_root) for t in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            # Keep only proven packages; decided here on the returned record —
            # the worker's own rec never carries the final status, so a cleanup
            # inside repair_one's finally would delete every outcome.
            if rec.get("status") != "spec_fixed":
                work = out_root / rec["task_id"]
                if work.exists():
                    shutil.rmtree(work, ignore_errors=True)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            print(f"[{n}/{len(todo)}] {rec['task_id']} -> {rec['status']}  {counts}")


if __name__ == "__main__":
    main()
