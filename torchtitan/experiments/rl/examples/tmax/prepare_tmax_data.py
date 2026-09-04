# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a tmax training JSONL from the ``allenai/tmax-15k-open-instruct`` dataset.

The AI2 tmax terminal-agent corpus ships as two artifacts on the Hub:

  - ``data/*.parquet`` -- one row per task with columns ``messages``,
    ``ground_truth`` (== task_id), ``dataset``, ``env_config`` (a struct with
    ``env_name`` / ``image`` / ``task_id``), and ``source``. ``env_config.image``
    is the PUBLIC dockerhub image the task runs in (setup.sh baked in).
  - ``task-data.tar.gz`` -- a per-task directory tree ``<task_id>/`` holding
    ``instruction.md``, ``setup.sh`` (ignored -- already baked into the image),
    and ``tests/test.sh`` (the verifier). Some tasks also carry
    ``environment/seeds/`` seed fixtures and extra ``tests/`` files.

Each output row is exactly what ``TMaxDataset`` (data.py) consumes -- the same
R2E-compatible schema ``swe_r2e/data.py`` expects, but with a ``tmax`` metadata
blob instead of ``r2e``::

    {
      "prompt": <instruction.md>,
      "label":  <task_id>,
      "metadata": {
        "instance_id", "image" (docker.io/...), "workdir",
        "problem_statement": <instruction.md>,
        "tmax": {
          "test_sh":     <contents of tests/test.sh>,
          "fixtures":    {relpath: content},   # environment/seeds/** + tests/** (no test.sh)
          "reward_path": "/logs/verifier/reward.txt"
        }
      }
    }

The tmax verifier contract: run ``bash /tests/test.sh`` INSIDE the task container;
it writes ``/logs/verifier/reward.txt`` containing ``0`` or ``1``. Reward = that
value (see grading.py).

Run with a python that has ``huggingface_hub`` + ``pyarrow`` (HF_TOKEN set)::

    python -m torchtitan.experiments.rl.examples.tmax.prepare_tmax_data \
        --out mast_rl/swe_assets/tmax_train.jsonl

``--limit N`` emits only the first N tasks (smoke). A tiny ``tmax_smoke.jsonl``
(5 tasks) is always written next to ``--out`` unless ``--no-smoke``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile

_HF_REPO = "allenai/tmax-15k-open-instruct"
_DEFAULT_IMAGE_PREFIX = "docker.io/"
_REWARD_PATH = "/logs/verifier/reward.txt"

# Fixture roots inside a task dir whose files are uploaded at grade time. Files
# under ``environment/seeds/`` land in <workdir>/ (seed the agent's workspace);
# files under ``tests/`` (except test.sh, which is uploaded to /tests/test.sh
# explicitly) land in /tests/ alongside test.sh. See grading.py::grade_tmax.
_FIXTURE_ROOTS = ("environment/seeds", "tests")

# Candidate working directories, in preference order when the instruction does not
# clearly anchor to one. tmax corpus instructions overwhelmingly reference
# /home/user and /app; the OSS default fallback is /workspace.
_WORKDIR_CANDIDATES = ("/home/user", "/app", "/workspace")
_DEFAULT_WORKDIR = "/workspace"

# PRE-VERIFY (reaudit decision-1): an optional per-task pre_test integrity check exported by
# tools/pretest_export.py (a JSONL of {task_id, pre_test_sh, ...}). When present it is carried into
# tmax["pre_test_sh"] and grading.py runs it once, as root, before test.sh (assert-refuse over the spec's pinned
# references). Absent file / unknown task ⇒ omitted ⇒ grading is a no-op, so this is safe on the whole corpus.
_PRETEST_EXPORT_PATH = os.environ.get(
    "TMAX_PRETEST_EXPORT",
    "/data/users/zekaili/fangzhou/tb_check/reaudit_398/repairs/pretest_export.jsonl",
)
_PRETEST_BY_ID: dict[str, dict] | None = None


def _pretest_for(task_id: str) -> dict:
    """Exported pre_test fields for a task: {} when there is no export / no entry, else
    {"pre_test_sh", "env_kind", "env_identity"} -- the captured environment identity for the drift guard."""
    global _PRETEST_BY_ID
    if _PRETEST_BY_ID is None:
        _PRETEST_BY_ID = {}
        try:
            with open(_PRETEST_EXPORT_PATH, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    row = json.loads(ln)
                    pt = row.get("pre_test_sh") or ""
                    if pt:
                        _PRETEST_BY_ID[row["task_id"]] = {
                            "pre_test_sh": pt,
                            "env_kind": row.get("env_kind") or "image",
                            "env_identity": row.get("env_identity") or "",
                        }
        except (OSError, ValueError):
            _PRETEST_BY_ID = {}
    return _PRETEST_BY_ID.get(task_id, {})


def _episode_env_identity(task_dir: str, image: str, image_prefix: str = "") -> str:
    """This episode's environment identity: "dockerfile:<sha256>" when the bundle carries a Dockerfile
    (environment/Dockerfile, then Dockerfile), else "image:<ref>" for the image the sample boots. Compared to
    the captured identity so a task whose environment drifted since capture skips the pin check. The image ref
    is normalised to the UNPREFIXED reference (a leading registry prefix such as "docker.io/" is stripped) so it
    matches env_stamp.env_identity, which is the image_map reference verbatim with no registry prefix."""
    for rel in ("environment/Dockerfile", "Dockerfile"):
        fp = os.path.join(task_dir, rel)
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                return "dockerfile:" + hashlib.sha256(f.read()).hexdigest()
    ref = image or ""
    if image_prefix and ref.startswith(image_prefix):
        ref = ref[len(image_prefix):]
    return "image:" + ref


def _pretest_tmax_fields(task_id: str, task_dir: str, image: str, image_prefix: str = "") -> dict:
    """tmax fields for the pre-verify hook: {} when the task has no exported pre_test. Otherwise the check plus
    the drift-guard identities grading.py compares -- the captured identity (from the export stamp) and this
    episode's identity (computed here) -- and task_id for the skip log. `image` must be the UNPREFIXED reference
    (the registry prefix is not part of env_stamp.env_identity)."""
    fields = _pretest_for(task_id)
    if not fields.get("pre_test_sh"):
        return {}
    stamped = f"{fields.get('env_kind') or 'image'}:{fields.get('env_identity') or ''}"
    return {
        "pre_test_sh": fields["pre_test_sh"],
        "task_id": task_id,
        "pretest_env_identity": stamped,
        "pretest_episode_env_identity": _episode_env_identity(task_dir, image, image_prefix),
    }


def _download() -> str:
    """Download the parquet + task-data.tar.gz via huggingface_hub; return the
    local snapshot dir. HF_TOKEN is read from the environment by the hub client."""
    from huggingface_hub import snapshot_download

    snap = snapshot_download(
        repo_id=_HF_REPO,
        repo_type="dataset",
        allow_patterns=["data/*.parquet", "task-data.tar.gz"],
    )
    return snap


def _extract_task_data(snap_dir: str) -> str:
    """Extract ``task-data.tar.gz`` (once) and return the extracted root dir."""
    tar_path = os.path.join(snap_dir, "task-data.tar.gz")
    if not os.path.exists(tar_path):
        raise FileNotFoundError(f"task-data.tar.gz not found under {snap_dir}")
    out_dir = tar_path + ".extracted"
    # Skip re-extraction if already populated (the HF cache extracts alongside).
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        return out_dir
    os.makedirs(out_dir, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(out_dir)  # noqa: S202 -- trusted AI2 dataset artifact
    return out_dir


def _find_task_dir(extracted_root: str, task_id: str) -> str | None:
    """Locate ``<task_id>/`` under the extracted tree.

    The tar lays tasks out either directly at the root (``<root>/<task_id>``) or
    one level down (``<root>/<something>/<task_id>``). Check both.
    """
    direct = os.path.join(extracted_root, task_id)
    if os.path.isdir(direct):
        return direct
    for entry in os.listdir(extracted_root):
        cand = os.path.join(extracted_root, entry, task_id)
        if os.path.isdir(cand):
            return cand
    return None


def _read_parquet_rows(snap_dir: str) -> list[dict]:
    """Read the parquet's task rows (env_config + ground_truth) with pyarrow."""
    import glob

    import pyarrow.parquet as pq

    paths = sorted(glob.glob(os.path.join(snap_dir, "data", "*.parquet")))
    if not paths:
        raise FileNotFoundError(f"no parquet found under {snap_dir}/data")
    rows: list[dict] = []
    for p in paths:
        tbl = pq.read_table(p, columns=["ground_truth", "env_config"])
        rows.extend(tbl.to_pylist())
    return rows


def _detect_workdir(instruction: str) -> str:
    """Best-guess the task's working directory from the instruction text.

    tmax tasks name absolute paths in the instruction; pick the candidate the
    instruction references most, defaulting to /workspace when none appears.
    """
    counts = {c: instruction.count(c) for c in _WORKDIR_CANDIDATES}
    best = max(_WORKDIR_CANDIDATES, key=lambda c: counts[c])
    return best if counts[best] > 0 else _DEFAULT_WORKDIR


def _collect_fixtures(task_dir: str) -> dict[str, str]:
    """Gather ``{relpath: content}`` for every file under the fixture roots,
    EXCEPT ``tests/test.sh`` (uploaded separately). Relpaths are relative to the
    task dir (e.g. ``environment/seeds/aa``, ``tests/expected_output.txt``)."""
    fixtures: dict[str, str] = {}
    for root in _FIXTURE_ROOTS:
        base = os.path.join(task_dir, root)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                abspath = os.path.join(dirpath, fn)
                rel = os.path.relpath(abspath, task_dir)
                if rel == os.path.join("tests", "test.sh"):
                    continue
                try:
                    with open(abspath, encoding="utf-8") as f:
                        fixtures[rel] = f.read()
                except (UnicodeDecodeError, OSError):
                    # Skip binary/unreadable fixtures; tmax fixtures are text.
                    continue
    return fixtures


def _to_row(task_id: str, image: str, task_dir: str, image_prefix: str) -> dict | None:
    """Build one output row from a task's parquet entry + extracted dir."""
    instr_path = os.path.join(task_dir, "instruction.md")
    test_path = os.path.join(task_dir, "tests", "test.sh")
    if not (os.path.exists(instr_path) and os.path.exists(test_path)):
        return None
    with open(instr_path, encoding="utf-8") as f:
        instruction = f.read()
    with open(test_path, encoding="utf-8") as f:
        test_sh = f.read()
    if not instruction.strip() or not test_sh.strip():
        return None

    raw_image = image                                   # the reference as read, before the registry prefix
    if image_prefix and "/" in image and not image.startswith(image_prefix):
        image = image_prefix + image
    workdir = _detect_workdir(instruction)
    fixtures = _collect_fixtures(task_dir)

    return {
        "prompt": instruction,
        "label": task_id,
        "metadata": {
            "instance_id": task_id,
            "image": image,
            "workdir": workdir,
            "problem_statement": instruction,
            "tmax": {
                "test_sh": test_sh,
                "fixtures": fixtures,
                "reward_path": _REWARD_PATH,
                # episode identity from the UNPREFIXED ref (env_stamp.env_identity carries no registry prefix);
                # image_prefix is passed so an already-prefixed source ref is normalised too.
                **_pretest_tmax_fields(task_id, task_dir, raw_image, image_prefix),
            },
        },
    }


def row_from_local_dir(
    task_dir: str, *, image_prefix: str = _DEFAULT_IMAGE_PREFIX
) -> dict | None:
    """Build a row from an already-extracted local task dir (not from the parquet).

    Used for standalone smoke fixtures (e.g. the openthoughts join task). The image
    is read from a sibling ``image.txt`` if present; the task_id is the dir name.
    """
    task_id = os.path.basename(os.path.normpath(task_dir))
    img_path = os.path.join(task_dir, "image.txt")
    if not os.path.exists(img_path):
        return None
    with open(img_path, encoding="utf-8") as f:
        image = f.read().strip()
    if not image:
        return None
    return _to_row(task_id, image, task_dir, image_prefix)


def build_rows(
    *, limit: int | None = None, image_prefix: str = _DEFAULT_IMAGE_PREFIX
) -> list[dict]:
    """Download + join the parquet and task-data tar into output rows."""
    snap = _download()
    extracted = _extract_task_data(snap)
    parquet_rows = _read_parquet_rows(snap)

    out: list[dict] = []
    for pr in parquet_rows:
        env = pr.get("env_config") or {}
        task_id = pr.get("ground_truth") or env.get("task_id")
        image = env.get("image")
        if not task_id or not image:
            continue
        task_dir = _find_task_dir(extracted, task_id)
        if task_dir is None:
            continue
        row = _to_row(task_id, image, task_dir, image_prefix)
        if row is not None:
            out.append(row)
        if limit is not None and len(out) >= limit:
            break
    return out


def selfcheck_env_identities(rows: list[dict]) -> tuple[int, int]:
    """Guard against a corpus-wide silent skip: among rows that carry a pre_test and both drift-guard
    identities, count how many MATCH (stamped == episode). Returns (matched, stamped_total). Raises when there
    is at least one stamped row and NONE match -- that means every task would skip the pin check from round 0
    (e.g. a registry-prefix mismatch), which must fail loudly at prep time rather than silently disable the hook."""
    matched = total = 0
    for r in rows:
        tm = (r.get("metadata") or {}).get("tmax") or {}
        if not tm.get("pre_test_sh"):
            continue
        st = tm.get("pretest_env_identity") or ""
        ep = tm.get("pretest_episode_env_identity") or ""
        if not (st and ep):
            continue
        total += 1
        if st == ep:
            matched += 1
    if total and matched == 0:
        raise RuntimeError(
            f"env-identity self-check FAILED: 0 of {total} stamped rows match their episode identity -- the "
            "pre-verify pin check would be skipped corpus-wide from round 0 (likely a registry-prefix mismatch "
            "between env_stamp.env_identity and the booted image ref). Refusing to write the dataset."
        )
    return matched, total


def _write_jsonl(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output tmax_train.jsonl path")
    ap.add_argument(
        "--limit", type=int, default=None, help="emit only the first N tasks (smoke)"
    )
    ap.add_argument("--image-prefix", default=_DEFAULT_IMAGE_PREFIX)
    ap.add_argument(
        "--smoke-size", type=int, default=5, help="rows in the sidecar tmax_smoke.jsonl"
    )
    ap.add_argument(
        "--no-smoke", action="store_true", help="skip writing tmax_smoke.jsonl"
    )
    ap.add_argument(
        "--include-local-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="extracted task dir (with image.txt) to prepend to the smoke file; "
        "repeatable. Used to seed a solvable smoke task (e.g. the join task).",
    )
    args = ap.parse_args()

    rows = build_rows(limit=args.limit, image_prefix=args.image_prefix)
    if not rows:
        print("ERROR: produced 0 rows", file=sys.stderr)
        sys.exit(1)
    _sc_matched, _sc_total = selfcheck_env_identities(rows)  # raises on a corpus-wide identity mismatch
    if _sc_total:
        print(f"env-identity self-check: {_sc_matched}/{_sc_total} stamped rows match their episode identity")
    _write_jsonl(rows, args.out)
    print(f"wrote {len(rows)} tmax tasks -> {args.out}")

    if not args.no_smoke:
        smoke_path = os.path.join(
            os.path.dirname(os.path.abspath(args.out)), "tmax_smoke.jsonl"
        )
        local_rows: list[dict] = []
        for d in args.include_local_dir:
            r = row_from_local_dir(d, image_prefix=args.image_prefix)
            if r is not None:
                local_rows.append(r)
            else:
                print(
                    f"  [warn] no image.txt/instruction in {d}, skipped",
                    file=sys.stderr,
                )
        smoke_rows = local_rows + rows[: max(0, args.smoke_size - len(local_rows))]
        _write_jsonl(smoke_rows, smoke_path)
        print(f"wrote {len(smoke_rows)} smoke tasks -> {smoke_path}")


if __name__ == "__main__":
    main()
