# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a Terminal-Bench-Hard JSONL in the tmax task schema.

``Zhongzhi1228/Terminal-Bench-Hard`` is 100 Harbor-format tasks. Same tree shape
and reward contract as TB-2.1 (``tasks/<id>/{task.toml,instruction.md,tests/}``,
``tests/test.sh`` -> ``/logs/verifier/reward.txt``), so ``grading.py`` applies
unchanged -- but the environment is a Dockerfile plus a local build context rather
than a published image, which is why this is its own builder and not a flag on
``prepare_tb2_1_data.py``:

  - 0 of 100 tasks declare ``[environment].docker_image``. Rows carry
    ``dockerfile`` + ``build_context`` ({relpath: base64}) and the sandbox backend
    builds them, the same path ``prepare_rts_data`` uses. ``TMaxSample`` takes
    image OR dockerfile (data.py).
  - No ``solution/``: there is no oracle to check the tree against.
  - ``[environment]`` states cpus and memory_mb but no storage_mb, so disk falls
    back to ``TT_DAYTONA_DISK_GB``. These images install toolchains (cargo, go,
    ...) at build time; set it to 10+ or builds run out of room.
  - Every task declares agent 600s / verifier 120s and no Dockerfile WORKDIR, so
    rows use /app (the fixtures land in /app/fixtures; /home/user also exists).

Tasks also carry a ``metadata/tasks.parquet`` with the instruction, task.toml and
Dockerfile text per task -- ``--verify-parquet`` cross-checks the emitted rows
against it.

    python -m torchtitan.experiments.rl.examples.tmax.prepare_tbh_data \\
        --out tbh_eval.jsonl [--tasks-root /path/to/Terminal-Bench-Hard]
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import sys
import tomllib

from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
    _build_context,
    _DAYTONA_CPU_FLOOR,
    _DAYTONA_MEM_GB_FLOOR,
)
from torchtitan.experiments.rl.examples.tmax.prepare_tmax_data import _REWARD_PATH

_HF_REPO = "Zhongzhi1228/Terminal-Bench-Hard"
_TASKS_SUBDIR = "tasks"
_FIXTURE_ROOT = "tests"
# No task sets a Dockerfile WORKDIR; /app is where the fixtures are COPY'd.
_DEFAULT_WORKDIR = "/app"
_EXPECTED_TASKS = 100
# Suffix carrying a base64 grading fixture (see _DECODE_PREAMBLE).
_B64_SUFFIX = ".b64"

# Prepended to test.sh only when a task ships a non-UTF-8 grading fixture: the tmax
# fixture channel is a str->str map, so binaries travel as base64 and are restored
# before the upstream verifier body runs.
_DECODE_PREAMBLE = """
# --- injected by prepare_tbh_data.py: restore binary grading fixtures ----------
find /tests -type f -name '*.b64' -print0 2>/dev/null | while IFS= read -r -d '' _tb_enc; do
  _tb_out="${_tb_enc%.b64}"
  if base64 -d "$_tb_enc" > "$_tb_out.tmp" 2>/dev/null; then
    mv -f "$_tb_out.tmp" "$_tb_out" && rm -f "$_tb_enc"
  else
    rm -f "$_tb_out.tmp"; echo "WARNING: could not decode $_tb_enc" >&2
  fi
done
# --- end injected block --------------------------------------------------------
"""


def _download() -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=_HF_REPO, repo_type="dataset")


def _is_task_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.exists(os.path.join(path, "task.toml"))


def _resolve_tasks_root(root: str) -> str:
    """Accept the repo root or the ``tasks/`` dir itself."""
    for cand in (os.path.join(root, _TASKS_SUBDIR), root):
        if os.path.isdir(cand) and any(
            _is_task_dir(os.path.join(cand, e)) for e in os.listdir(cand)
        ):
            return cand
    raise SystemExit(f"ERROR: no task dirs under {root!r}; is this a Harbor tree?")


def _load_toml(task_dir: str) -> dict:
    path = os.path.join(task_dir, "task.toml")
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _positive(val) -> float | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val) if val > 0 else None


def _resources(cfg: dict) -> dict[str, int]:
    """``[environment] cpus/memory_mb`` -> the daytona_* fields data.py reads.

    storage_mb is absent throughout TBH, so daytona_disk_gb is deliberately not
    emitted and the TT_DAYTONA_DISK_GB default applies.
    """
    env = cfg.get("environment") if isinstance(cfg.get("environment"), dict) else {}
    out: dict[str, int] = {}
    cpus = env.get("cpus")
    if isinstance(cpus, (int, float)) and not isinstance(cpus, bool) and cpus > 0:
        out["daytona_cpu"] = max(_DAYTONA_CPU_FLOOR, int(round(float(cpus))))
    mem = env.get("memory_mb")
    if isinstance(mem, (int, float)) and not isinstance(mem, bool) and mem > 0:
        out["daytona_mem_gb"] = max(_DAYTONA_MEM_GB_FLOOR, math.ceil(float(mem) / 1024))
    return out


def _collect_fixtures(task_dir: str) -> tuple[dict[str, str], list[str]]:
    """``{relpath: content}`` for everything under ``tests/`` except test.sh.

    Non-UTF-8 files ride as base64 under ``<relpath>.b64`` and are returned in the
    second element so the caller knows to inject the decode preamble.
    """
    fixtures: dict[str, str] = {}
    encoded: list[str] = []
    base = os.path.join(task_dir, _FIXTURE_ROOT)
    if not os.path.isdir(base):
        return fixtures, encoded
    for dirpath, _dirs, files in sorted(os.walk(base)):
        for fn in sorted(files):
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, task_dir)
            if rel == os.path.join(_FIXTURE_ROOT, "test.sh"):
                continue
            try:
                # newline="" so a CRLF fixture reaches /tests byte-identical.
                with open(abspath, encoding="utf-8", newline="") as f:
                    fixtures[rel] = f.read()
                continue
            except UnicodeDecodeError:
                pass
            except OSError as e:
                print(f"WARNING: unreadable fixture {rel}: {e}", file=sys.stderr)
                continue
            with open(abspath, "rb") as fb:
                fixtures[rel + _B64_SUFFIX] = base64.b64encode(fb.read()).decode()
            encoded.append(rel)
    return fixtures, encoded


def _wrap_test_sh(test_sh: str) -> str:
    lines = test_sh.split("\n")
    if lines and lines[0].startswith("#!"):
        return lines[0] + "\n" + _DECODE_PREAMBLE + "\n".join(lines[1:])
    return _DECODE_PREAMBLE + test_sh


def _to_row(task_id: str, task_dir: str, *, source: str) -> tuple[dict | None, str]:
    instr_path = os.path.join(task_dir, "instruction.md")
    test_path = os.path.join(task_dir, _FIXTURE_ROOT, "test.sh")
    df_path = os.path.join(task_dir, "environment", "Dockerfile")
    if not os.path.exists(instr_path):
        return None, "no instruction.md"
    if not os.path.exists(test_path):
        return None, "no tests/test.sh"
    if not os.path.exists(df_path):
        return None, "no environment/Dockerfile"

    with open(instr_path, encoding="utf-8", newline="") as f:
        instruction = f.read()
    with open(test_path, encoding="utf-8", newline="") as f:
        test_sh = f.read()
    with open(df_path, encoding="utf-8", newline="") as f:
        dockerfile = f.read()
    if not instruction.strip():
        return None, "empty instruction.md"
    if not test_sh.strip():
        return None, "empty tests/test.sh"

    try:
        build_context = _build_context(os.path.dirname(df_path), dockerfile)
    except FileNotFoundError as e:
        return None, f"build context missing {e}"
    except ValueError as e:
        return None, f"build context too large: {e}"

    cfg = _load_toml(task_dir)
    fixtures, encoded = _collect_fixtures(task_dir)
    if encoded:
        test_sh = _wrap_test_sh(test_sh)

    metadata = {
        "instance_id": task_id,
        # Empty: the backend builds `dockerfile` with `build_context` instead.
        "image": "",
        "dockerfile": dockerfile,
        "build_context": build_context,
        "workdir": _DEFAULT_WORKDIR,
        "problem_statement": instruction,
        "agent_timeout_sec": _positive((cfg.get("agent") or {}).get("timeout_sec")),
        "verifier_timeout_sec": _positive(
            (cfg.get("verifier") or {}).get("timeout_sec")
        ),
        "tb_version": "hard",
        "task_source": source,
        "tmax": {
            "test_sh": test_sh,
            "fixtures": fixtures,
            "reward_path": _REWARD_PATH,
        },
    }
    metadata.update(_resources(cfg))
    return {"prompt": instruction, "label": task_id, "metadata": metadata}, "ok"


def build_rows(
    *, tasks_root: str | None = None, limit: int | None = None
) -> tuple[list[dict], dict[str, str]]:
    """Convert every TBH task dir to a row. Returns ``(rows, {task_id: reason})``."""
    root = _resolve_tasks_root(tasks_root or _download())
    rows: list[dict] = []
    skipped: dict[str, str] = {}
    for entry in sorted(os.listdir(root)):
        task_dir = os.path.join(root, entry)
        if not _is_task_dir(task_dir):
            continue
        row, reason = _to_row(entry, task_dir, source=tasks_root or _HF_REPO)
        if row is None:
            skipped[entry] = reason
            continue
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows, skipped


def _verify_parquet(rows: list[dict], parquet_path: str) -> int:
    """Cross-check rows against the dataset's own metadata/tasks.parquet.

    It carries the instruction, task.toml and Dockerfile text per task, so a
    mismatch means the tree was read wrong (or is not the published one).
    """
    import pandas as pd

    df = pd.read_parquet(parquet_path)
    by = {r["label"]: r for r in rows}
    bad = 0
    for _, rec in df.iterrows():
        row = by.get(rec["task_id"])
        if row is None:
            print(f"MISMATCH {rec['task_id']}: not in output", file=sys.stderr)
            bad += 1
            continue
        for got, want, what in (
            (row["prompt"], rec["instruction"], "instruction"),
            (row["metadata"]["dockerfile"], rec["dockerfile"], "dockerfile"),
        ):
            if got != want:
                print(f"MISMATCH {rec['task_id']}: {what}", file=sys.stderr)
                bad += 1
    print(f"parquet cross-check: {len(df)} tasks, {bad} mismatches")
    return bad


def _write_jsonl(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a Terminal-Bench-Hard JSONL in the tmax schema."
    )
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--tasks-root",
        default=None,
        metavar="PATH",
        help=f"local checkout (repo root or its tasks/ dir). Default: download {_HF_REPO}.",
    )
    ap.add_argument("--limit", type=int, default=None, help="first N tasks (smoke)")
    ap.add_argument(
        "--expect-tasks",
        type=int,
        default=_EXPECTED_TASKS,
        help="fail unless exactly this many rows are produced (0 disables)",
    )
    ap.add_argument(
        "--verify-parquet",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="cross-check rows against metadata/tasks.parquet (default: alongside "
        "--tasks-root); exits non-zero on any mismatch",
    )
    args = ap.parse_args()

    rows, skipped = build_rows(tasks_root=args.tasks_root, limit=args.limit)
    for tid, reason in sorted(skipped.items()):
        print(f"WARNING: skipped {tid}: {reason}", file=sys.stderr)
    if not rows:
        print("ERROR: produced 0 rows", file=sys.stderr)
        sys.exit(1)
    if args.limit is None and args.expect_tasks and len(rows) != args.expect_tasks:
        print(
            f"ERROR: produced {len(rows)} rows, expected {args.expect_tasks}; "
            "the task tree changed shape (pass --expect-tasks 0 to override)",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.verify_parquet is not None:
        path = args.verify_parquet or os.path.join(
            args.tasks_root or "", "metadata", "tasks.parquet"
        )
        if _verify_parquet(rows, path):
            sys.exit(1)

    _write_jsonl(rows, args.out)
    print(f"wrote {len(rows)} tasks -> {args.out}")


if __name__ == "__main__":
    main()
