#!/usr/bin/env python3
"""Run the audit's claimed shortcuts and count how many actually pass.

The v2 solvability audit judges three quarters of the seed corpus `hackable`:
it names, per task, a short command sequence it believes satisfies the verifier
without doing the task. That is a reading, and this session already retired one
audit verdict (`environment`) for having been a guess that measurement
contradicted. So before "76% of seed verifiers can be fooled" is repeated
anywhere, this runs the claims: build the task's image, run the claimed
commands in a fresh container — no solution staged — and grade.

Three outcomes per task, and the difference matters:

  confirmed   the verifier passed on the shortcut — a demonstrated reward hole
  refuted     the verifier failed it — the auditor's claim was wrong
  unproven    build failed or the container was lost, so no verdict

`--sample N` checks a deterministic random subset first; the confirmed rate on a
sample decides whether the full corpus is worth the builds.

Usage:
  verify_shortcuts.py --audit results/seed_solvability_v2.jsonl \\
      --tar chunk000.tar --results results/shortcut_verify.jsonl \\
      [--sample 30] [--workers 6]
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import tarfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import docker_validate as dv
import synth_loop as sl

SHORTCUT_TIMEOUT = 120


def verify_one(tar_path: Path, tid: str, shortcut: str, work_root: Path) -> dict:
    rec: dict = {"task_id": tid, "shortcut": shortcut[:400],
                 "t_start": time.time()}
    work = work_root / tid
    image = f"hck-{tid.lower()}"
    container = f"hck-{tid.lower()}"
    try:
        with tarfile.open(tar_path) as tf:
            dv.extract(tf, tid, work)
        env_dir = work / "environment"
        if not (env_dir / "Dockerfile").exists():
            return {**rec, "outcome": "unproven", "why": "no Dockerfile"}
        fixed, n = dv.repair_heredoc_spacing(
            (env_dir / "Dockerfile").read_text(encoding="utf-8",
                                               errors="surrogateescape"))
        if n:
            (env_dir / "Dockerfile").write_text(fixed, encoding="utf-8",
                                                errors="surrogateescape")
        rc, out, _ = dv.build_with_retry(env_dir, image, 2, dv.BUILD_TIMEOUT)
        if rc != 0:
            return {**rec, "outcome": "unproven",
                    "why": f"build failed: {dv.tail_signal(out, 200)}"}
        if not sl.start_container(image, container):
            return {**rec, "outcome": "unproven",
                    "why": "container would not start"}
        rc, out = sl.sh(["docker", "exec", container, "bash", "-lc",
                         f"cd /app 2>/dev/null || cd /; {shortcut}"],
                        SHORTCUT_TIMEOUT)
        rec["shortcut_exit"] = rc
        rec["shortcut_tail"] = out[-300:]
        reward, tail = sl.grade(container, work)
        rec["reward"] = reward
        rec["test_tail"] = tail[-300:]
        rec["outcome"] = ("confirmed" if reward == "1"
                          else "refuted" if reward == "0" else "unproven")
        return rec
    except Exception as e:  # noqa: BLE001
        return {**rec, "outcome": "unproven",
                "why": f"{type(e).__name__}: {e}"[:200]}
    finally:
        rec["t_end"] = time.time()
        sl.sh(["docker", "rm", "-f", container], 120)
        dv.sh(["docker", "rmi", "-f", image], 300)
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", required=True)
    ap.add_argument("--tar", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--sample", type=int, default=0,
                    help="verify only N tasks, chosen with a fixed seed")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    claims = []
    for line in Path(args.audit).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        s = str(r.get("shortcut") or "").strip()
        if r.get("verdict") == "hackable" and s:
            claims.append((r["task_id"], s))

    done = set()
    out_path = Path(args.results)
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["task_id"])
    claims = [c for c in claims if c[0] not in done]

    if args.sample:
        claims = random.Random(0).sample(claims, min(args.sample, len(claims)))
    print(f"{len(claims)} claims to run, {len(done)} already done")

    work_root = Path("data/shortcut-verify")
    work_root.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex, \
            open(out_path, "a") as fh:
        futs = [ex.submit(verify_one, Path(args.tar), tid, s, work_root)
                for tid, s in claims]
        for n, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            counts[rec["outcome"]] = counts.get(rec["outcome"], 0) + 1
            print(f"[{n}/{len(claims)}] {rec['task_id']} -> {rec['outcome']}"
                  f"  {counts}")


if __name__ == "__main__":
    main()
