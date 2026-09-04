# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a Terminal-Bench 2.1 EVAL JSONL in the tmax task schema.

Corrected sibling of ``prepare_tb2_data.py`` (TB-2.0), which is left in place so
past TB-2.0 numbers stay reproducible. Fixes three defects in it:

  1. TB-2.1 nests task dirs under ``tasks/``; the 2.0 script globs the snapshot
     root and emits zero rows. Either layout is accepted here.
  2. Non-UTF-8 grading fixtures were dropped, so the 7 tasks whose verifier reads
     a binary out of /tests could never score. The fixture channel is str->str, so
     binaries ride as base64 at ``tests/<name>.b64`` and a decode preamble is
     prepended to ``test_sh``. grading.py is untouched; the other 82 tasks keep a
     byte-identical ``test_sh``.
  3. Per-task ``[environment] cpus/memory_mb/storage_mb`` now become
     daytona_cpu/mem_gb/disk_gb instead of the flat TT_DAYTONA_* defaults.

Also: real TOML parsing, ``newline=""`` on text reads (universal-newline
translation silently ate 310 bytes of sparql-university's CRLF .ttl), and the run
aborts unless the tree yields 89 tasks.

Rows are what ``TMaxDataset`` (data.py) consumes -- same keys as the 2.0 script
plus agent/verifier timeouts, the daytona_* trio, and tb_version/task_source.

Each row carries its declared ``verifier_timeout_sec`` and the rollouter grades on
it, floored at ``TMAX_EVAL_TIMEOUT_SEC``; there is nothing to set for a full-suite
eval. Output is ~26 MB; the base64 fixtures dominate.

    python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_1_data \
        --out tb2_1_eval.jsonl [--tasks-root /path/to/terminal-bench-2-1]
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys

from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
    _DAYTONA_CPU_FLOOR,
    _DAYTONA_DISK_GB_FLOOR,
    _DAYTONA_MEM_GB_FLOOR,
)
from torchtitan.experiments.rl.examples.tmax.prepare_tmax_data import (
    _DEFAULT_IMAGE_PREFIX,
    _REWARD_PATH,
)

# HF mirror of github.com/harbor-framework/terminal-bench-2-1 (same tree, same
# commit -- its registry.json records the mirrored GitHub SHA).
_HF_REPO = "harborframework/terminal-bench-2.1"

# TB-2.1 nests the task dirs under this subdir; TB-2.0 had them at the root.
_TASKS_SUBDIR = "tasks"

# Grade-time inputs live under ``tests/`` (test.sh is uploaded separately by
# grading.py). The task environment is baked into the published image, so -- unlike
# the tmax corpus -- there is no ``environment/seeds`` tree to seed into the workdir.
_FIXTURE_ROOT = "tests"

# Fallback workdir when a task Dockerfile declares no WORKDIR. TB-2.1: 86 of 89
# tasks use /app (the others /workspace, /app/dclm, /app/personal-site).
_DEFAULT_WORKDIR = "/app"

# TB-2.1 ships exactly this many tasks. Asserted so a layout change surfaces as an
# error rather than as a short eval file.
_EXPECTED_TASKS = 89

# Suffix carrying a base64-encoded binary grading fixture (see _BINARY_DECODE_PREAMBLE).
_B64_SUFFIX = ".b64"

# Prepended to test.sh ONLY for tasks that ship a non-UTF-8 grading fixture. Restores
# each ``/tests/<name>.b64`` to ``/tests/<name>`` before the upstream verifier body
# runs (train-fasttext, for one, untars /tests/private_test.tar.gz in its first lines).
# Decodes via base64(1), falling back to python3, and writes through a temp file so a
# partial decode never leaves a truncated fixture in place.
_BINARY_DECODE_PREAMBLE = """
# --- injected by prepare_tb2_1_data.py: restore binary grading fixtures ---------
# The tmax fixture channel is a str->str map, so non-UTF-8 verifier inputs (images,
# .pt tensors, tarballs) travel as base64 text at <path>.b64. Restore them here.
find /tests -type f -name '*.b64' -print0 2>/dev/null | while IFS= read -r -d '' _tb_enc; do
  _tb_out="${_tb_enc%.b64}"
  if base64 -d "$_tb_enc" > "$_tb_out.tmp" 2>/dev/null; then
    mv -f "$_tb_out.tmp" "$_tb_out" && rm -f "$_tb_enc"
  elif command -v python3 >/dev/null 2>&1 && python3 -c 'import base64,sys
open(sys.argv[2],"wb").write(base64.b64decode(open(sys.argv[1],"rb").read()))' "$_tb_enc" "$_tb_out.tmp"; then
    mv -f "$_tb_out.tmp" "$_tb_out" && rm -f "$_tb_enc"
  else
    rm -f "$_tb_out.tmp"
    echo "WARNING: could not decode $_tb_enc" >&2
  fi
done
# --- end injected block --------------------------------------------------------
"""


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def _download() -> str:
    """Download the full TB-2.1 tree from the HF mirror; return the snapshot dir."""
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=_HF_REPO, repo_type="dataset")


def _is_task_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.exists(os.path.join(path, "task.toml"))


def _resolve_tasks_root(root: str) -> str:
    """Return the dir whose children are task dirs, accepting either published layout.

    TB-2.1 puts them under ``<root>/tasks``; TB-2.0 put them at ``<root>``. Passing a
    repo root, a snapshot dir, or the ``tasks/`` dir itself all work. Note that
    ``tasks/dataset.toml`` is a *file* named like a task, which is why the probe tests
    for a task.toml inside a directory rather than merely listing names.
    """
    for candidate in (os.path.join(root, _TASKS_SUBDIR), root):
        if not os.path.isdir(candidate):
            continue
        if any(_is_task_dir(os.path.join(candidate, e)) for e in os.listdir(candidate)):
            return candidate
    nested = os.path.join(root, _TASKS_SUBDIR)
    raise SystemExit(
        f"ERROR: no task dirs (a directory containing task.toml) under {root!r} "
        f"or {nested!r}; is this a Terminal-Bench task tree?"
    )


# --------------------------------------------------------------------------- #
# task.toml
# --------------------------------------------------------------------------- #
def _load_task_toml(task_dir: str) -> dict:
    """Parse a task.toml into a dict (empty when absent)."""
    path = os.path.join(task_dir, "task.toml")
    if not os.path.exists(path):
        return {}
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def _get(cfg: dict, *keys):
    """``cfg["a"]["b"]`` with a None for any missing/non-dict level."""
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _positive_number(val) -> float | None:
    """Coerce a declared timeout to a positive float, else None (use the default)."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val) if val > 0 else None


def _size_to_gb(mb_value, size_value) -> int | None:
    """GiB (rounded up) from either a megabyte number or a TB-2.0 size string.

    ``memory_mb = 8192`` -> 8. ``memory = "8G"`` -> 8, ``"512M"`` -> 1. Returns None
    when neither field states a usable positive size, so the caller can omit the key
    and let the TT_DAYTONA_* default apply.
    """
    if isinstance(mb_value, (int, float)) and not isinstance(mb_value, bool):
        if mb_value > 0:
            return math.ceil(float(mb_value) / 1024)
    if isinstance(size_value, (int, float)) and not isinstance(size_value, bool):
        # A bare number in the 2.0 field is already GiB.
        return math.ceil(float(size_value)) if size_value > 0 else None
    if isinstance(size_value, str):
        m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([KMGTkmgt])?[Bi]*\s*", size_value)
        if not m:
            return None
        val = float(m.group(1))
        mult = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}.get(
            (m.group(2) or "G").upper(), 1.0
        )
        gb = val * mult
        return math.ceil(gb) if gb > 0 else None
    return None


def _daytona_resources(cfg: dict) -> dict[str, int]:
    """Map ``[environment] cpus/memory_mb/storage_mb`` -> daytona_* metadata fields.

    Clamped to the same floors ``prepare_rts_data`` applies, for one reason: an RL
    agent explores far more than the oracle solution the declaration was sized for.
    A field the task does not declare is omitted so the sandbox falls back to that
    field's ``TT_DAYTONA_*`` env default rather than to a guess.
    """
    env = cfg.get("environment") if isinstance(cfg.get("environment"), dict) else {}
    out: dict[str, int] = {}
    cpus = env.get("cpus")
    if isinstance(cpus, (int, float)) and not isinstance(cpus, bool) and cpus > 0:
        out["daytona_cpu"] = max(_DAYTONA_CPU_FLOOR, int(round(float(cpus))))
    # TB-2.1 (schema 1.1) states megabytes in `memory_mb`/`storage_mb`; TB-2.0
    # (schema 1.0) stated a size string in `memory`/`storage` (e.g. "2G", "512M").
    # Read both so a 2.0-style tree is sized from its own declaration rather than
    # silently falling back to the TT_DAYTONA_* defaults.
    for mb_key, size_key, floor, out_key in (
        ("memory_mb", "memory", _DAYTONA_MEM_GB_FLOOR, "daytona_mem_gb"),
        ("storage_mb", "storage", _DAYTONA_DISK_GB_FLOOR, "daytona_disk_gb"),
    ):
        gb = _size_to_gb(env.get(mb_key), env.get(size_key))
        if gb is not None:
            out[out_key] = max(floor, gb)
    return out


def _workdir_from_dockerfile(task_dir: str) -> str:
    """Read the last ``WORKDIR`` from the task's environment Dockerfile.

    The published image lands the agent in its final WORKDIR; our harness cd's there
    per bash command, so it must match. Falls back to /app when absent.
    """
    dockerfile = os.path.join(task_dir, "environment", "Dockerfile")
    if not os.path.exists(dockerfile):
        return _DEFAULT_WORKDIR
    workdir = _DEFAULT_WORKDIR
    with open(dockerfile, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"\s*WORKDIR\s+(\S+)", line)
            if m:
                workdir = m.group(1)
    return workdir


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _collect_fixtures(
    task_dir: str, *, include_binary: bool = True
) -> tuple[dict[str, str], list[str]]:
    """Gather ``{relpath: content}`` for every file under ``tests/`` except ``test.sh``.

    Relpaths are relative to the task dir (``tests/test_outputs.py``,
    ``tests/test/01-factorial.scm``); grading.py maps ``tests/*`` -> ``/tests/*``,
    preserving subdirectories.

    Files that are not valid UTF-8 are base64-encoded under ``<relpath>.b64`` and
    their original relpath is returned in the second element, so the caller knows to
    prepend the decode preamble. This is the fix for the seven TB-2.1 verifiers that
    read a binary out of /tests -- the 2.0 script dropped those files outright.
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
                continue  # uploaded explicitly by grading.py, never as a fixture
            try:
                # newline="" disables universal-newline translation. Without it a
                # CRLF fixture silently loses every \r on the way into the JSONL --
                # sparql-university's university_graph_test.ttl is 310 bytes shorter
                # that way. Graded inputs must reach /tests byte-identical.
                with open(abspath, encoding="utf-8", newline="") as f:
                    fixtures[rel] = f.read()
                continue
            except UnicodeDecodeError:
                pass
            except OSError as e:
                print(f"WARNING: unreadable fixture {rel}: {e}", file=sys.stderr)
                continue
            if not include_binary:
                continue
            with open(abspath, "rb") as fb:
                fixtures[rel + _B64_SUFFIX] = base64.b64encode(fb.read()).decode()
            encoded.append(rel)
    return fixtures, encoded


def _wrap_test_sh(test_sh: str) -> str:
    """Insert the base64-decode preamble into ``test_sh``, after any shebang.

    grading.py invokes ``bash /tests/test.sh`` explicitly, so the shebang is inert --
    it is preserved anyway so the emitted script still reads like the upstream one.
    """
    lines = test_sh.split("\n")
    if lines and lines[0].startswith("#!"):
        return lines[0] + "\n" + _BINARY_DECODE_PREAMBLE + "\n".join(lines[1:])
    return _BINARY_DECODE_PREAMBLE + test_sh


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #
def _tb_version(cfg: dict) -> str:
    """Benchmark version implied by the task schema: 1.1 -> 2.1, 1.0 -> 2.0."""
    schema = str(cfg.get("schema_version") or cfg.get("version") or "").strip()
    return {"1.1": "2.1", "1.0": "2.0"}.get(schema, schema or "unknown")


def _to_row(
    task_id: str,
    task_dir: str,
    *,
    image_prefix: str,
    include_binary: bool = True,
    source: str = _HF_REPO,
) -> tuple[dict | None, str]:
    """Build one row from a TB-2.1 task dir, or ``(None, reason)`` when unusable."""
    instr_path = os.path.join(task_dir, "instruction.md")
    test_path = os.path.join(task_dir, _FIXTURE_ROOT, "test.sh")
    cfg = _load_task_toml(task_dir)
    image = _get(cfg, "environment", "docker_image")

    if not os.path.exists(instr_path):
        return None, "no instruction.md"
    if not os.path.exists(test_path):
        return None, "no tests/test.sh"
    if not image:
        return None, "no [environment].docker_image"
    # newline="" here too: the prompt and the verifier script ship verbatim.
    with open(instr_path, encoding="utf-8", newline="") as f:
        instruction = f.read()
    with open(test_path, encoding="utf-8", newline="") as f:
        test_sh = f.read()
    if not instruction.strip():
        return None, "empty instruction.md"
    if not test_sh.strip():
        return None, "empty tests/test.sh"

    if image_prefix and "/" in image and not image.startswith(image_prefix):
        image = image_prefix + image

    fixtures, encoded = _collect_fixtures(task_dir, include_binary=include_binary)
    if encoded:
        test_sh = _wrap_test_sh(test_sh)

    metadata = {
        "instance_id": task_id,
        "image": image,
        "workdir": _workdir_from_dockerfile(task_dir),
        "problem_statement": instruction,
        # Harbor states the agent budget per task, not per benchmark; the rollouter
        # falls back to its configured default when this is None.
        "agent_timeout_sec": _positive_number(_get(cfg, "agent", "timeout_sec")),
        # Read per task by the rollouter (_verifier_budget_sec), with
        # TMAX_EVAL_TIMEOUT_SEC as the floor. TB-2.1 declares 360s to 12000s.
        "verifier_timeout_sec": _positive_number(_get(cfg, "verifier", "timeout_sec")),
        # Provenance must describe what was actually read, not what this script is
        # named for: pointed at a 2.0-style tree it must not stamp rows as 2.1.
        "tb_version": _tb_version(cfg),
        "task_source": source,
        "tmax": {
            "test_sh": test_sh,
            "fixtures": fixtures,
            "reward_path": _REWARD_PATH,
        },
    }
    metadata.update(_daytona_resources(cfg))
    return {"prompt": instruction, "label": task_id, "metadata": metadata}, "ok"


def build_rows(
    *,
    tasks_root: str | None = None,
    limit: int | None = None,
    image_prefix: str = _DEFAULT_IMAGE_PREFIX,
    include_binary: bool = True,
) -> tuple[list[dict], dict[str, str]]:
    """Convert every TB-2.1 task dir to a row. Returns ``(rows, {task_id: reason})``.

    ``tasks_root`` is a local checkout (repo root, ``tasks/`` dir, or a 2.0-style flat
    root); when None the HF mirror is downloaded.
    """
    root = _resolve_tasks_root(tasks_root or _download())
    rows: list[dict] = []
    skipped: dict[str, str] = {}
    for entry in sorted(os.listdir(root)):
        task_dir = os.path.join(root, entry)
        if not _is_task_dir(task_dir):
            continue
        row, reason = _to_row(
            entry,
            task_dir,
            image_prefix=image_prefix,
            include_binary=include_binary,
            source=tasks_root or _HF_REPO,
        )
        if row is None:
            skipped[entry] = reason
            continue
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows, skipped


def _write_jsonl(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a Terminal-Bench 2.1 eval JSONL in the tmax schema."
    )
    ap.add_argument("--out", required=True, help="output tb2_1_eval.jsonl path")
    ap.add_argument(
        "--tasks-root",
        default=None,
        metavar="PATH",
        help="local Terminal-Bench 2.1 checkout (repo root or its tasks/ dir). "
        f"Default: download {_HF_REPO} from the Hub.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="emit only the first N tasks (smoke); relaxes the task-count check",
    )
    ap.add_argument("--image-prefix", default=_DEFAULT_IMAGE_PREFIX)
    ap.add_argument(
        "--no-binary-fixtures",
        action="store_true",
        help="drop non-UTF-8 grading fixtures instead of shipping them base64 "
        "(smaller JSONL, but the 7 tasks that read a binary out of /tests can then "
        "never score -- this is the TB-2.0 script's behaviour)",
    )
    ap.add_argument(
        "--expect-tasks",
        type=int,
        default=_EXPECTED_TASKS,
        help="fail unless exactly this many rows are produced (0 disables)",
    )
    args = ap.parse_args()

    rows, skipped = build_rows(
        tasks_root=args.tasks_root,
        limit=args.limit,
        image_prefix=args.image_prefix,
        include_binary=not args.no_binary_fixtures,
    )
    if skipped:
        for tid, reason in sorted(skipped.items()):
            print(f"WARNING: skipped {tid}: {reason}", file=sys.stderr)
    if not rows:
        print("ERROR: produced 0 rows", file=sys.stderr)
        sys.exit(1)
    if args.limit is None and args.expect_tasks and len(rows) != args.expect_tasks:
        print(
            f"ERROR: produced {len(rows)} rows, expected {args.expect_tasks}. "
            "The published task tree changed shape -- check --tasks-root and the "
            "skip warnings above before using this file (pass --expect-tasks 0 to "
            "override).",
            file=sys.stderr,
        )
        sys.exit(1)

    _write_jsonl(rows, args.out)
    print(f"wrote {len(rows)} tasks -> {args.out}")


if __name__ == "__main__":
    main()
