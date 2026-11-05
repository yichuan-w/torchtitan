#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Score an EvolveBench evalset as a validation-only cluster job.

The job is the training launcher with 0 optimizer steps: the controller runs its
blocking pre-training validation pass over the evalset at the recipe's k and
returns (Controller._run). Validation is switched to training's behaviour by the
recipe's SWE_EVAL_* pins, so an attempt is a training rollout of the row. The
eval directory ends up in the layout tb2_eval_local.sh leaves, so `tally`,
`freeze --eval`, `salvage` and `compare` read it unchanged:

    <eval>/launch.json                                 recipe, checkpoint, GPUs, generators
    <eval>/validation_rollouts/<task>/g<G>-r<i>.jsonl  one record per attempt
    <eval>/trainer/validation_traces/step-0/index.json the controller's index

Two subcommands around the cluster submit:

    stage    an evalset -> <base>/evals/<name>/launch.json + job.env, and a copy
             of the evalset in the shared directory the job reads. The job's
             output directory (TRL_RUN_DIR) is <remote-root>/<name>, which is
             also the Daytona `run` label of every sandbox it creates.
    collect  the job's output directory -> the eval directory, checked against
             the controller's index (or, for a job stopped before writing it,
             every record so far, for `evolve_bench.py salvage`).

--local-root and --remote-root name one shared directory: as this host mounts
it and as the job's hosts do.

    python bench_cluster_eval.py stage --evalset <set>/original.jsonl \\
        --local-root /mnt/share/bench_cluster --remote-root /share/bench_cluster \\
        --checkpoint base-Qwen3.5-122B-A10B --num-generators 4 --gpu-name "NVIDIA H100 80GB HBM3"
    <cluster launcher> <eval>/job.env
    python bench_cluster_eval.py collect <eval>
"""

from __future__ import annotations

import argparse
import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

_EVO = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVO))
import evolve_bench as eb  # noqa: E402
import layout  # noqa: E402

RECIPE = "train-111c-122b"
# One generator is one host's engine group. 111e ran 1152 rollouts over 10 such
# generators (~115 each) without KV preemption; 128 is the bench's per-generator
# cap (evolve_bench.ROLLOUT_CONCURRENCY_PER_GENERATOR).
CONCURRENCY_PER_GENERATOR = 128
JOB_ENV = "job.env"


def _tt_commit() -> str:
    tt = _EVO.parents[5]
    head = subprocess.run(["git", "-C", str(tt), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(tt), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, check=True).stdout.strip()
    return f"{head}-dirty" if dirty else head


def _checked_evalset(evalset: Path, harness: str) -> None:
    """The evalset still matches what pinned it, and its set measured the
    harness being run (evolve_bench.eval_env's checks)."""
    release = eb._release_of(evalset)
    if release is not None:
        want = release["files"]["mix.jsonl"]
    else:
        manifest = json.load(open(evalset.with_name(evalset.stem + ".manifest.json")))
        want = manifest["sha256"]
        set_dir = Path(manifest["set_dir"]) if manifest.get("set_dir") else None
        set_manifest = (json.load(open(set_dir / "manifest.json"))
                        if set_dir and (set_dir / "manifest.json").exists() else {})
        set_harness = manifest.get("student_harness") or set_manifest.get("student_harness")
        if set_harness and harness != set_harness:
            raise SystemExit(f"the set's signals were measured under {set_harness}, not {harness}")
    if layout.sha256_file(evalset) != want:
        raise SystemExit(f"{evalset} no longer matches the sha256 its manifest pinned")


def _run_and_step(checkpoint: str) -> tuple[str, int]:
    """tb2_eval_local.sh's naming: a base model is <name>-step0, a DCP step
    directory is <run>-step<N> with the run read off its path."""
    p = Path(checkpoint)
    if not p.name.startswith("step-"):
        return p.name, 0
    run = p.parent
    while run.name in ("checkpoints", "checkpoint", "rl", "outputs", "weights"):
        run = run.parent
    return run.name, int(p.name.removeprefix("step-"))


def stage(a: argparse.Namespace) -> Path:
    evalset = a.evalset.resolve()
    base = a.base
    if base is None:
        if eb._release_of(evalset) is not None:
            raise SystemExit("a release's mix is scored into --base <dir>/evals/, never into the release")
        base = evalset.parent.parent.parent if evalset.parent.name == "evalsets" else evalset.parent
    if a.shard:
        evalset = eb.shard_evalset(evalset, a.shard, base)
    _checked_evalset(evalset, a.harness)
    rows = eb._rows(evalset)
    recipe = dict(eb.STUDENT_RECIPES[a.recipe])
    k = a.k or int(recipe["SWE_TB2_VAL_K"])
    if a.k and a.k != int(recipe["SWE_TB2_VAL_K"]):
        print(f"WARNING: k={a.k} overrides the {a.recipe} recipe's {recipe['SWE_TB2_VAL_K']}; "
              "compare will refuse to pair this eval with a recipe eval", file=sys.stderr)
    recipe["SWE_TB2_VAL_K"] = str(k)
    if not 1 <= a.concurrency_per_generator <= CONCURRENCY_PER_GENERATOR:
        raise SystemExit(f"--concurrency-per-generator: 1 to {CONCURRENCY_PER_GENERATOR}")
    concurrency = min(a.concurrency_per_generator * a.num_generators, len(rows) * k)
    run, step = _run_and_step(a.checkpoint)
    started = layout.stamp()
    name = f"{started}--{run}-step{step}-{evalset.stem}"
    ev = base / "evals" / name
    local_out = a.local_root.resolve() / name
    remote_out = f"{a.remote_root.rstrip('/')}/{name}"
    if ev.exists() or local_out.exists():
        raise SystemExit(f"{ev} or {local_out} exists")
    local_out.mkdir(parents=True)
    shutil.copy2(evalset, local_out / "evalset.jsonl")
    if layout.sha256_file(local_out / "evalset.jsonl") != layout.sha256_file(evalset):
        raise SystemExit(f"the copy at {local_out}/evalset.jsonl does not match {evalset}")
    pins = {**recipe, "SWE_BENCH_RECIPE": a.recipe, "TMAX_AGENT": a.harness,
            "SWE_ROLLOUT_CONCURRENCY": str(concurrency)}
    job_env = {
        **pins,
        # The pass: every row once, k attempts each, nothing trained.
        "SWE_TB2_VAL_DATA": f"{remote_out}/evalset.jsonl",
        "SWE_PROMPT_DATA": f"{remote_out}/evalset.jsonl",
        "SWE_HOLDOUT_N": "0",
        "SWE_VAL_SAMPLES": str(len(rows)),
        "SWE_TRAIN_STEPS": "0",
        "SWE_NUM_EVAL_GENERATORS": "0",
        "SWE_VAL_ASYNC": "0",
        "SWE_VAL_TRACES": "1",
        # The trace report is the index: written before the job returns.
        "SWE_VAL_TRACE_BACKGROUND": "0",
        "SWE_NUM_GENERATORS": str(a.num_generators),
        "TRL_RUN_DIR": remote_out,
        "SWE_DUMP_DIR": f"{remote_out}/trainer",
    }
    if not a.checkpoint.startswith("base-"):
        job_env["SWE_INITIAL_DCP_CHECKPOINT"] = a.checkpoint
    ev.mkdir(parents=True)
    with open(ev / JOB_ENV, "w") as f:
        for key, val in job_env.items():
            f.write(f"export {key}='{val}'\n")
    layout.write_json_atomic(ev / "launch.json", {
        "eval": name,
        "started": started,
        "profile": None,
        "tt": str(_EVO.parents[5]),
        "tt_commit": _tt_commit(),
        "checkpoint": a.checkpoint,
        "run": run,
        "step": step,
        "host": "cluster",
        "gpus": None,
        "gpu_names": a.gpu_name,
        "num_generators": a.num_generators,
        "evalset_sha256": layout.sha256_file(evalset),
        "env": {**pins, "SWE_TB2_DATA": str(evalset)},
        "cluster": {"output": remote_out, "local_output": str(local_out), "job": None,
                    "job_env": {k_: v for k_, v in job_env.items() if k_ not in pins}},
    })
    print(f"{ev}\n  {len(rows)} tasks x k={k}, concurrency {concurrency} over {a.num_generators} generator(s)\n"
          f"  job env: {ev / JOB_ENV}\n  Daytona run label: {name}")
    return ev


def note_job(a: argparse.Namespace) -> None:
    path = a.eval_dir / "launch.json"
    launch = json.load(open(path))
    launch["cluster"]["job"] = a.job
    layout.write_json_atomic(path, launch)


def _records(out: Path) -> list[Path]:
    return sorted(p for p in (out / "validation_rollouts").glob("*/*.jsonl") if eb.RECORD_NAME.fullmatch(p.name))


def collect(a: argparse.Namespace) -> None:
    ev = a.eval_dir.resolve()
    launch = json.load(open(ev / "launch.json"))
    out = Path(launch["cluster"]["local_output"])
    index_src = out / "trainer" / eb.INDEX.relative_to("trainer")
    records = _records(out)
    for src in records:
        dst = ev / src.relative_to(out)
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    if index_src.exists():
        step_dir = ev / eb.INDEX.parent
        step_dir.mkdir(parents=True, exist_ok=True)
        for f in ("index.json", "summary.json", "INDEX.md"):
            if (index_src.parent / f).exists():
                shutil.copy2(index_src.parent / f, step_dir / f)
        missing = [e["record"] for e in json.load(open(ev / eb.INDEX))
                   if not e.get("record") or not (ev / e["record"]).exists()]
        if missing:
            raise SystemExit(f"{len(missing)} index entries name no record, e.g. {missing[:3]}")
    print(f"{ev}: {len(records)} records copied; index {'present' if index_src.exists() else 'not written yet'}")
    secs = collections.defaultdict(list)
    for src in records:
        with open(src) as fh:
            h = json.loads(fh.readline())
        if isinstance(h.get("secs"), (int, float)):
            secs[h["task"]].append(h["secs"])
    if index_src.exists():
        e = eb.read_eval(ev)
        bands = collections.Counter(t["bucket"] for t in e["tasks"].values())
        print(f"  bands {dict(bands)}")
        for tid, t in sorted(e["tasks"].items()):
            wall = f" wall max {max(secs[tid]):.0f}s" if secs.get(tid) else ""
            print(f"  {tid}: {t['pass']}/{t['graded']} infra {t['infra']} {t['finish']}{wall}")
    else:
        print("  no index: the job has not finished, or stopped early (evolve_bench.py salvage keeps whole tasks)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stage", help="evalset -> eval directory, job env, shared copy")
    s.add_argument("--evalset", type=Path, required=True)
    s.add_argument("--local-root", type=Path, required=True, help="the shared directory, as this host sees it")
    s.add_argument("--remote-root", required=True, help="the same directory, as the job's hosts see it")
    s.add_argument("--checkpoint", required=True,
                   help="base-<model> for the released weights, else a DCP step directory the job can read")
    s.add_argument("--num-generators", type=int, required=True)
    s.add_argument("--gpu-name", required=True, help="the generators' GPU type, as nvidia-smi names it")
    s.add_argument("--base", type=Path, help="the eval goes to <base>/evals/ (default: the evalset's root or set)")
    s.add_argument("--recipe", default=RECIPE, choices=sorted(eb.STUDENT_RECIPES))
    s.add_argument("--harness", default="terminus", choices=eb.HARNESSES)
    s.add_argument("--k", type=int, help="attempts per task (default: the recipe's; anything else is a smoke)")
    s.add_argument("--shard", help="i/n: score rows i, i+n, ... (evolve_bench.shard_evalset)")
    s.add_argument("--concurrency-per-generator", type=int, default=CONCURRENCY_PER_GENERATOR)
    j = sub.add_parser("note-job", help="record the cluster job's name in launch.json")
    j.add_argument("eval_dir", type=Path)
    j.add_argument("job")
    c = sub.add_parser("collect", help="job output -> eval directory")
    c.add_argument("eval_dir", type=Path)
    a = p.parse_args()
    {"stage": stage, "note-job": note_job, "collect": collect}[a.cmd](a)


if __name__ == "__main__":
    main()
