#!/usr/bin/env python3
"""Close a demonstrated reward hole, and prove it closed.

Input: tasks whose verifier a specific command sequence has actually fooled —
`verify_shortcuts.py` ran the claim and the verifier paid full reward. That
demonstration is the repair's whole advantage: instead of "make the checks
stronger" in the abstract, the model is shown the exact commands that won
without working, which is a failing test case for the verifier itself.

Per task, up to `--rounds` times:

  1. repair   rewrite tests/test_state.py with the shortcut as the finding
  2. oracle   the reference solution must still pass — a repair that breaks it
              costs the task everything
  3. re-prove run the same shortcut again; it must now score 0
  4. re-audit a fresh reading looks for a NEW shortcut; if it names one, run
              it, and a pass sends the task around again

A task leaves as `hardened` (original shortcut refuted, no new confirmed one),
or `still_hackable` / `oracle_broken` / `unproven` with the evidence attached.
Repaired packages are written to --out; sources are never modified.

Usage:
  repair_hackable.py --verify results/accepted_shortcut_verify.jsonl \\
      --tar data/all-accepted/tasks-00000.tar --out data/accepted-hardened \\
      --results results/repair_hackable.jsonl [--rounds 2] [--workers 4]
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

HARDEN = """Close a demonstrated hole in this task's verifier. \
Rewrite tests/test_state.py only.

This exact command sequence was executed in a fresh container with no solution \
staged, and the verifier scored it a full pass:

{shortcut}

What the verifier printed while passing it:

{evidence}

That is a failing test case for the verifier: the task's instruction asks for \
real work, and these commands do none of it. Strengthen the checks until this \
sequence scores 0. Aim at what the sequence exploits — a check satisfied by a \
fabricated file, a phrase match, an existence test — and replace it with one \
earned in behaviour: change an input the answer depends on and re-run the \
workflow the instruction describes, asserting the output followed and restoring \
what you changed; or recompute the expected answer inside the verifier from the \
current inputs; or require an intermediate artifact whose content must agree \
with the final one.

Two constraints:
- the reference solution in solution/solve.sh must still pass — verify that \
mentally before returning, a repair that breaks it costs the task everything;
- do not invoke solution/solve.sh from the verifier; it is absent when the \
agent runs. Invoke the workflow the way the instruction tells a user to.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{"tests/test_state.py":"..."}}}}

Task contract:
{contract}

Current task:
{task_context}"""


def run_shortcut(work: Path, image: str, tag: str, shortcut: str) -> dict:
    return sl.shortcut_check(work, image, f"{tag}", shortcut)


def repair_one(tar_path: Path, tid: str, shortcut: str, evidence: str,
               out_root: Path, rounds: int) -> dict:
    rec: dict = {"task_id": tid, "shortcut": shortcut[:400],
                 "t_start": time.time(), "rounds": []}
    work = out_root / tid
    image = f"hrd-{tid.lower()}"
    try:
        with tarfile.open(tar_path) as tf:
            dv.extract(tf, tid, work)
        files = {p: (work / p).read_text(errors="replace")
                 for p in FILES.values()}

        current_shortcut, current_evidence = shortcut, evidence
        for rnd in range(1, rounds + 1):
            round_rec: dict = {"round": rnd, "shortcut": current_shortcut[:200]}
            rec["rounds"].append(round_rec)

            files = llm._repair(HARDEN, {}, files, "{}",
                                shortcut=current_shortcut,
                                evidence=current_evidence[:1500])
            (work / "tests/test_state.py").write_text(
                files["tests/test_state.py"])

            ok, tail = sl.build_image(work, image)
            if not ok:
                return {**rec, "status": "unproven",
                        "why": f"build failed: {tail[-200:]}"}
            oracle = sl.oracle_check(work, image, tid)
            round_rec["oracle_ok"] = oracle.get("ok")
            if not oracle.get("ok"):
                # Tightening the verifier can leave the reference solution — often
                # a minimal, verifier-shaped one on a seed — no longer passing.
                # That is repairable rather than fatal: ORACLE_REPAIR reads the
                # new checks and brings the solution up to them. Only if it still
                # cannot is the task broken.
                files = llm._repair(llm.ORACLE_REPAIR, {}, files, "{}")
                for rel in ("solution/solve.sh", "tests/test_state.py"):
                    if files.get(rel):
                        (work / rel).write_text(files[rel])
                ok, tail = sl.build_image(work, image)
                if not ok:
                    return {**rec, "status": "unproven",
                            "why": f"oracle-repair build failed: {tail[-200:]}"}
                oracle = sl.oracle_check(work, image, tid)
                round_rec["oracle_ok"] = oracle.get("ok")
                round_rec["oracle_repaired"] = oracle.get("ok")
                if not oracle.get("ok"):
                    return {**rec, "status": "oracle_broken",
                            "test_tail": oracle.get("test_tail", "")[-300:]}

            proof = run_shortcut(work, image, tid, current_shortcut)
            round_rec["original_shortcut_passes"] = proof.get("passed")
            if proof.get("passed"):
                # The repair did not close the demonstrated hole; going around
                # again with the same evidence is the best move available.
                current_evidence = proof.get("test_tail", "")
                continue

            # Original hole closed. A hardened verifier can still have a
            # different one, so read the task fresh and run whatever the
            # reading names.
            diag = llm.diagnose_unsolved({
                "instruction": files["instruction.md"],
                "solve_sh": files["solution/solve.sh"],
                "test_state_py": files["tests/test_state.py"],
                "dockerfile": files["environment/Dockerfile"]})
            round_rec["reaudit"] = diag.get("verdict")
            new_shortcut = str(diag.get("shortcut") or "").strip()
            if not new_shortcut:
                return {**rec, "status": "hardened"}
            new_proof = run_shortcut(work, image, tid, new_shortcut)
            round_rec["new_shortcut_passes"] = new_proof.get("passed")
            if not new_proof.get("passed"):
                return {**rec, "status": "hardened",
                        "note": "fresh reading named a shortcut; it failed"}
            current_shortcut, current_evidence = \
                new_shortcut, new_proof.get("test_tail", "")

        return {**rec, "status": "still_hackable",
                "why": f"a shortcut still passes after {rounds} rounds"}
    except Exception as e:  # noqa: BLE001
        return {**rec, "status": "unproven",
                "why": f"{type(e).__name__}: {e}"[:200]}
    finally:
        rec["t_end"] = time.time()
        dv.sh(["docker", "rmi", "-f", image], 300)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", required=True,
                    help="verify_shortcuts results; only confirmed rows repair")
    ap.add_argument("--tar", required=True)
    ap.add_argument("--out", default="data/accepted-hardened")
    ap.add_argument("--results", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    todo = []
    for line in Path(args.verify).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("outcome") == "confirmed":
            todo.append((r["task_id"], r.get("shortcut", ""),
                         r.get("test_tail", "")))

    done = set()
    out_path = Path(args.results)
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["task_id"])
    todo = [t for t in todo if t[0] not in done]
    print(f"{len(todo)} confirmed-hackable tasks to repair, {len(done)} done")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex, \
            open(out_path, "a") as fh:
        futs = [ex.submit(repair_one, Path(args.tar), tid, sc, ev,
                          out_root, args.rounds)
                for tid, sc, ev in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            # The package stays on disk only for a clean pass: a still-hackable
            # or oracle-broken copy left beside the good ones would look
            # shippable later. Decided here on the returned record — the
            # worker's own rec never carries the final status, so a cleanup
            # inside repair_one's finally would delete every outcome.
            if rec.get("status") != "hardened":
                work = out_root / rec["task_id"]
                if work.exists():
                    shutil.rmtree(work, ignore_errors=True)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            print(f"[{n}/{len(todo)}] {rec['task_id']} -> {rec['status']}"
                  f"  {counts}")


if __name__ == "__main__":
    main()
