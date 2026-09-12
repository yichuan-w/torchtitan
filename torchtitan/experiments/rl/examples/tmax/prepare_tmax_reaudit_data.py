# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a training JSONL from the ``Fzz1/Tmax-Tasks-Clean`` ``reaudit`` split.

Each preparation resolves ``--revision`` (default: main) to a commit once. All
three files come from that commit; a moving branch cannot mix releases::

    splits/reaudit.parquet           task membership, hooks and protected lists
    splits/reaudit_full.parquet      measured peaks for build_mix_v2
    data/tasks-reaudit-00000.tar     packages under tasks/<task_id>/

    tasks/<task_id>/instruction.md
    tasks/<task_id>/environment/Dockerfile   # a bare single FROM <ref> for every task
    tasks/<task_id>/tests/test.sh            # verifier: writes /logs/verifier/reward.txt
    tasks/<task_id>/tests/reference_pins.sha256   # where present
    tasks/<task_id>/solution/solve.sh
    tasks/<task_id>/setup.sh

This script is DELIBERATELY STANDALONE. It neither edits nor is plumbed through
``prepare_rts_data.py`` / ``prepare_tmax_data.py``: it reads our split and our tar and emits
the trainer's row shape one-to-one with ``prepare_rts_data._to_row`` (the same ``prompt`` /
``label`` / ``metadata`` keys, produced by the same helpers, imported unchanged), plus the
pre-verify hook fields ``grading.py`` reads out of ``metadata["tmax"]``.

THE HOOK, and the naming trap. The parquet carries ``pre_test_sh`` (a pre-test integrity
script grading.py runs as root before ``bash /tests/test.sh``; nonzero rc scores 0) and
``pre_test_env_identity`` (the environment the pins were captured against). grading.py does
NOT read those column names: it reads ``tmax["pre_test_sh"]``, ``tmax["pretest_env_identity"]``
and ``tmax["pretest_episode_env_identity"]`` -- no underscore in "pretest", and the third one
is COMPUTED here from the package's Dockerfile, not read from anywhere. A column-to-field copy
would produce rows whose identity fields are absent, and absent identities mean the check
SKIPS, which looks exactly like success. So the fields are produced by
``prepare_tmax_data._pretest_tmax_fields`` and never spelled here.

WHAT IS REFUSED, each before a row is written:
  * downloaded bytes differ from the resolved commit's Hub LFS digest;
  * missing required columns, incompatible types, or inconsistent split/peaks/package IDs;
  * a row whose ``pre_test_sh`` and ``pre_test_env_identity`` are not both set or both empty
    -- a hook without the environment it was stamped for cannot be run safely;
  * a split row with no package in the tar, or a package whose bytes do not reproduce the row's
    ``task_content_sha256`` (sha256 over the package's FILE members in sorted member-name order,
    each contributing relpath + NUL + content + NUL, relpath relative to the package prefix);
  * a tar member that is not a plain file under ``tasks/<task_id>/``;
  * a row count other than an explicitly supplied ``--expect-rows``, or lost rows during preparation;
  * 0 of the stamped rows matching their episode identity (the corpus-wide silent-skip guard),
    or any stamped row with no usable identity pair.
An EMPTY ``pre_test_sh`` is a task with no hook, never a failure. Extra columns are allowed.

Run (the token is read from a FILE at use and never printed; a public revision needs none)::

    python -m torchtitan.experiments.rl.examples.tmax.prepare_tmax_reaudit_data \
        --out mast_rl/swe_assets/reaudit_train.jsonl \
        [--token-file /path/to/hf.txt] [--limit N] [--seed 42] [--max-oracle-commands 64]

``<out stem>.manifest.json`` records the resolved commit and input/output hashes.
``--source-dir ROOT`` additionally preserves the verified packages and all three
inputs under ``ROOT/<commit>/data/sources/{tmax-clean,tmax-extract}``, for breeding.
Existing snapshots are never overwritten. Existing experiments keep their source links.

Offline: ``--parquet PATH --tar PATH [--peaks PATH]`` uses local copies, records
their hashes, and validates package contents; no unverified HF revision is claimed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tarfile
import tempfile

from torchtitan.experiments.rl.examples.tmax.integrity_baseline import (
    tmax_protected_fields,
)
from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
    _build_context,
    _entrypoint_command,
    _grading_fixtures,
    _inject_agent_runtime,
    _join_continuations,
    _load_resource_map,
    _oracle_commands,
    _REJECT_PRIVILEGED,
    _strip_canary,
    _strip_comments,
    _workdir_from_dockerfile,
    OracleCommandsFormError,
)
from torchtitan.experiments.rl.examples.tmax.prepare_tmax_data import (
    _DEFAULT_IMAGE_PREFIX,
    _pretest_tmax_fields,
    _REWARD_PATH,
    selfcheck_env_identities,
)
from torchtitan.experiments.rl.examples.tmax.reaudit_snapshot import (
    fetch_snapshot,
    file_record,
    HF_PARQUET,
    HF_PEAKS,
    HF_REPO as HF_REPO,
    HF_TAR,
    publish_sources,
    RefuseError,
    validate_peaks,
)
from torchtitan.experiments.rl.examples.tmax.resource_sizing import load_allocations

HF_REVISION = "main"
MEMBER_ROOT = "tasks"

_HOOK_COLUMNS = ("pre_test_sh", "pre_test_env_identity")
# The integrity-baseline columns, part of the split's contract since the 26-column publish: JSON lists
# of the task's protected paths / protected command strings. An EMPTY cell means the row carries no
# baseline and grades exactly as before; an absent column is a different split and refuses.
_PROTECTED_COLUMN = "protected_paths"
_PROTECTED_CMDS_COLUMN = "protected_cmds"
_NEEDED_COLUMNS = (
    "task_id",
    "member_prefix",
    "task_content_sha256",
    "shard",
    *_HOOK_COLUMNS,
    _PROTECTED_COLUMN,
    _PROTECTED_CMDS_COLUMN,
)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_token(token_file: str | None) -> str | None:
    """The token TEXT, read from the file at use. Never logged, never placed in a URL; the hub
    client sends it as a header. ``None`` lets the client resolve its own (env / HfFolder), which
    is enough for a public revision."""
    if not token_file:
        return None
    with open(token_file, encoding="utf-8") as f:
        tok = f.read().strip()
    if not tok:
        raise RefuseError(f"token file {token_file} is empty")
    return tok


def fetch(
    *, revision: str, token_file: str | None, cache_dir: str | None
) -> tuple[str, str]:
    """Compatibility helper for callers needing only the split and tar paths."""
    snapshot = fetch_snapshot(
        revision=revision, token=_read_token(token_file), cache_dir=cache_dir
    )
    return snapshot["files"][HF_PARQUET]["path"], snapshot["files"][HF_TAR]["path"]


def assert_sha256(path: str, expected: str, what: str) -> None:
    got = _sha256_file(path)
    if got != expected:
        raise RefuseError(
            f"{what} at {path} has sha256 {got}, expected {expected}: not the published bytes"
        )


def load_split(parquet_path: str) -> list[dict]:
    """The split's rows as dicts (label columns only are ever printed by this script). Asserts the
    columns it consumes exist -- a missing column read through .get() is indistinguishable from an
    empty cell, and here an empty cell means "no hook"."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    missing = [c for c in _NEEDED_COLUMNS if c not in table.column_names]
    if missing:
        raise RefuseError(f"split {parquet_path} lacks column(s) {missing}")
    if len(table.column_names) != len(set(table.column_names)):
        raise RefuseError("split has duplicate column names")
    for name in _NEEDED_COLUMNS:
        kind = table.schema.field(name).type
        nullable = name in (*_HOOK_COLUMNS, _PROTECTED_COLUMN, _PROTECTED_CMDS_COLUMN)
        if not (
            pa.types.is_string(kind)
            or pa.types.is_large_string(kind)
            or (nullable and pa.types.is_null(kind))
        ):
            raise RefuseError(f"split column {name} has incompatible type {kind}")
    for name in ("req_cpus", "req_memory_mb", "est_disk_mb"):
        if name in table.column_names:
            kind = table.schema.field(name).type
            if not (
                pa.types.is_integer(kind)
                or pa.types.is_floating(kind)
                or pa.types.is_null(kind)
            ):
                raise RefuseError(f"split column {name} has incompatible type {kind}")
    rows = table.to_pylist()
    ids = [r["task_id"] for r in rows]
    if not ids or any(not isinstance(t, str) or not t.strip() for t in ids):
        raise RefuseError("split must contain non-empty task IDs")
    if len(set(ids)) != len(ids):
        raise RefuseError("split has duplicate task_id values")
    if any(r["shard"] != HF_TAR for r in rows):
        raise RefuseError(f"split must reference the supported shard {HF_TAR}")
    return rows


def _json_list_map(rows: list[dict], column: str) -> dict[str, list[str]]:
    """{task_id: [entries]} for every row whose ``column`` cell is a non-empty JSON list. An empty cell or an
    absent column contributes nothing -- the key is then ABSENT from the row, never an empty list. A cell
    that is present but not a JSON list of non-empty strings refuses by task id: a malformed list must not
    read as "none". The list is iterated AS A LIST and never joined and re-split -- five shipped paths
    contain spaces, and every shipped command contains a quote character."""
    out: dict[str, list[str]] = {}
    for r in rows:
        raw = r.get(column)
        if raw is None or not str(raw).strip():
            continue
        try:
            entries = json.loads(raw)
        except ValueError:
            raise RefuseError(f"{r['task_id']}: {column} is not JSON") from None
        if not isinstance(entries, list) or not all(
            isinstance(p, str) and p.strip() for p in entries
        ):
            raise RefuseError(
                f"{r['task_id']}: {column} must be a JSON list of non-empty strings"
            )
        if entries:
            out[r["task_id"]] = entries
    return out


def protected_paths_map(rows: list[dict]) -> dict[str, list[str]]:
    return _json_list_map(rows, _PROTECTED_COLUMN)


def protected_cmds_map(rows: list[dict]) -> dict[str, list[str]]:
    """Command entries additionally refuse a newline: the hook's manifest is line-based and no shipped
    entry carries one, so a newline can only be corruption."""
    out = _json_list_map(rows, _PROTECTED_CMDS_COLUMN)
    for tid, cmds in out.items():
        if any("\n" in c or "\r" in c for c in cmds):
            raise RefuseError(f"{tid}: a protected_cmds entry contains a newline")
    return out


def assert_hook_pairing(rows: list[dict]) -> int:
    """``pre_test_sh`` and ``pre_test_env_identity`` are set together or not at all. Returns the
    number of hooked rows. A script without the environment it was stamped for cannot be run
    safely, and an identity without a script is a row the builder should never have written."""
    bad = []
    hooked = 0
    for r in rows:
        sh = bool((r.get("pre_test_sh") or "").strip())
        idn = bool((r.get("pre_test_env_identity") or "").strip())
        if sh != idn:
            bad.append(r["task_id"])
        hooked += int(sh)
    if bad:
        raise RefuseError(
            f"{len(bad)} row(s) carry a pre_test_sh without an env identity or the reverse: "
            f"{bad[:5]}{'...' if len(bad) > 5 else ''}"
        )
    return hooked


def _package_members(tar: tarfile.TarFile) -> dict[str, list[tarfile.TarInfo]]:
    """{'tasks/<task_id>': [file members]}. Refuses anything that is not a plain file under the
    member root, and any member name that could escape the extraction dir."""
    groups: dict[str, list[tarfile.TarInfo]] = {}
    seen = set()
    for m in tar.getmembers():
        parts = m.name.split("/")
        if (
            m.name.startswith("/")
            or ".." in parts
            or len(parts) < 3
            or parts[0] != MEMBER_ROOT
        ):
            raise RefuseError(f"unexpected tar member name {m.name!r}")
        if not m.isfile():
            raise RefuseError(f"tar member {m.name!r} is not a plain file")
        if m.name in seen:
            raise RefuseError(f"duplicate tar member {m.name!r}")
        seen.add(m.name)
        groups.setdefault("/".join(parts[:2]), []).append(m)
    return groups


def _package_sha256(
    tar: tarfile.TarFile, prefix: str, members: list[tarfile.TarInfo]
) -> str:
    """The split builder's task_content_sha256: sorted file members, relpath + NUL + content + NUL,
    relpath relative to the PACKAGE prefix ('instruction.md'), never the tar root."""
    h = hashlib.sha256()
    for m in sorted(members, key=lambda member: member.name):
        rel = m.name[len(prefix) + 1 :]
        f = tar.extractfile(m)
        assert f is not None
        h.update(rel.encode() + b"\0" + f.read() + b"\0")
    return h.hexdigest()


def _preexisting(tasks_root: str, limit: int = 5) -> list[str]:
    """Paths already under the extraction target, relative to it, the first ``limit``."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(tasks_root):
        for name in sorted(filenames) + sorted(
            d for d in dirnames if not os.listdir(os.path.join(dirpath, d))
        ):
            found.append(os.path.relpath(os.path.join(dirpath, name), tasks_root))
            if len(found) >= limit:
                return found
    return found


def verify_and_extract(tar_path: str, rows: list[dict], out_root: str) -> str:
    """Verify every split row's package against the tar and extract it; return the tasks root.

    The target must be EMPTY. The row builder enumerates the extracted directory (every file
    under tests/ becomes a grading fixture), so anything already there -- a previous run's
    leftovers, a stray file -- would reach the trainer rows while the parquet and tar shas
    still pass. A non-empty target refuses by name rather than extracting over it."""
    tasks_root = os.path.join(out_root, MEMBER_ROOT)
    os.makedirs(tasks_root, exist_ok=True)
    stray = _preexisting(tasks_root)
    if stray:
        raise RefuseError(
            f"extraction target {tasks_root} is not empty (e.g. {stray}): refusing to extract "
            "over pre-existing files; use a fresh --work-dir"
        )
    with tarfile.open(tar_path) as tar:
        groups = _package_members(tar)
        missing = [r["task_id"] for r in rows if r["member_prefix"] not in groups]
        if missing:
            raise RefuseError(
                f"{len(missing)} split row(s) have no package in the tar: "
                f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
            )
        extra = set(groups) - {r["member_prefix"] for r in rows}
        if extra:
            raise RefuseError(
                f"tar contains packages outside the split: {sorted(extra)[:5]}"
            )
        bad_sha = []
        for r in rows:
            prefix = r["member_prefix"]
            if prefix != f"{MEMBER_ROOT}/{r['task_id']}":
                raise RefuseError(
                    f"{r['task_id']}: member_prefix {prefix!r} does not name the task"
                )
            members = groups[prefix]
            if _package_sha256(tar, prefix, members) != r["task_content_sha256"]:
                bad_sha.append(r["task_id"])
                continue
            for m in members:
                dest = os.path.join(out_root, m.name)
                real_root = os.path.realpath(out_root)
                if os.path.commonpath([real_root, os.path.realpath(dest)]) != real_root:
                    raise RefuseError(
                        f"tar member {m.name!r} escapes the extraction dir"
                    )
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                src = tar.extractfile(m)
                assert src is not None
                with open(dest, "wb") as f:
                    shutil.copyfileobj(src, f)
        if bad_sha:
            raise RefuseError(
                f"{len(bad_sha)} package(s) do not reproduce their task_content_sha256: "
                f"{bad_sha[:5]}{'...' if len(bad_sha) > 5 else ''}"
            )
    return tasks_root


def to_row(
    task_dir: str,
    *,
    inject_agent_runtime: bool = True,
    resources: dict[str, int] | None = None,
    pretest: tuple[str, str] | None = None,
    protected_paths: list[str] | None = None,
    protected_cmds: list[str] | None = None,
) -> tuple[dict | None, str]:
    """One trainer row, or ``(None, reason)`` when filtered. Same helpers, same keys, same order
    and same conditions as ``prepare_rts_data._to_row`` AS THE TRAINER RUNS IT (the branch this
    ships on, not an older cut); the only addition is the hook spread into ``tmax``, which is ``{}``
    for a task with no hook so those rows are byte-identical. The test suite holds this to the real
    ``_to_row`` with a reference-oracle equality, so a helper whose contract drifts fails there."""
    task_id = os.path.basename(task_dir.rstrip("/"))
    paths = {
        "instruction": os.path.join(task_dir, "instruction.md"),
        "test_sh": os.path.join(task_dir, "tests", "test.sh"),
        "dockerfile": os.path.join(task_dir, "environment", "Dockerfile"),
    }
    for name, p in paths.items():
        if not os.path.exists(p):
            return None, f"missing_{name}"

    with open(paths["dockerfile"], encoding="utf-8") as f:
        dockerfile = f.read()
    if _REJECT_PRIVILEGED.search(_strip_comments(_join_continuations(dockerfile))):
        return None, "needs_privileged"

    env_dir = os.path.join(task_dir, "environment")
    try:
        build_context = _build_context(env_dir, dockerfile)
    except FileNotFoundError:
        return None, "copy_source_missing"
    except ValueError:
        return None, "build_context_too_large"
    if inject_agent_runtime:
        dockerfile = _inject_agent_runtime(dockerfile)

    with open(paths["instruction"], encoding="utf-8") as f:
        instruction = _strip_canary(f.read())
    with open(paths["test_sh"], encoding="utf-8") as f:
        test_sh = f.read()
    if not instruction.strip() or not test_sh.strip():
        return None, "empty_instruction_or_verifier"
    if "reward.txt" not in test_sh and "reward.json" not in test_sh:
        return None, "verifier_writes_no_reward"

    daytona_mem_gb = daytona_cpu = agent_timeout_sec = None
    toml_path = os.path.join(task_dir, "task.toml")
    if os.path.exists(toml_path):
        try:
            import tomllib

            with open(toml_path, "rb") as f:
                _toml = tomllib.load(f)
            env = _toml.get("environment", {})
            _ts = (_toml.get("agent") or {}).get("timeout_sec")
            if isinstance(_ts, (int, float)) and _ts > 0:
                agent_timeout_sec = float(_ts)
            mb = env.get("memory_mb")
            if isinstance(mb, (int, float)) and mb > 4096:
                daytona_mem_gb = -(-int(mb) // 1024)
            cp = env.get("cpus")
            if isinstance(cp, (int, float)) and cp > 2:
                daytona_cpu = int(cp)
        except Exception:
            pass  # sizing is an optimization; a bad toml never blocks the row

    solve_path = os.path.join(task_dir, "solution", "solve.sh")
    oracle_commands = 0
    if os.path.exists(solve_path):
        with open(solve_path, encoding="utf-8", errors="replace") as f:
            try:
                oracle_commands = _oracle_commands(f.read())
            except OracleCommandsFormError as e:
                # The shipped solution IS the replay for these rows, so a `_gr` line the counter cannot read
                # means the artefact drifted from its writer. Refuse the run BY NAME here rather than filter
                # the row: a silently missing task would surface only as the row-count guard, one step away
                # from the file that caused it.
                raise RefuseError(f"{task_id}: {e}") from None

    # The fixtures travel inside the row as text, so a tests/ file that is not UTF-8 or a set that exceeds
    # the context cap REFUSES the package by name rather than being skipped (prepare_rts_data's rule; the
    # helper returns the reason alongside the dict).
    fixtures, reason = _grading_fixtures(task_dir)
    if reason:
        return None, reason

    metadata = {
        "instance_id": task_id,
        "image": "",
        "dockerfile": dockerfile,
        "workdir": _workdir_from_dockerfile(dockerfile),
        "problem_statement": instruction,
        "oracle_commands": oracle_commands,
        "tmax": {
            "test_sh": test_sh,
            "fixtures": fixtures,
            "reward_path": _REWARD_PATH,
            # The pre-verify hook. The stamped identity comes FROM THE DATASET; the episode
            # identity is computed from this package's Dockerfile -- a bare single FROM resolves
            # to the UNPREFIXED "image:<ref>", which is what the stamp was captured as.
            **_pretest_tmax_fields(
                task_id,
                task_dir,
                "",
                _DEFAULT_IMAGE_PREFIX,
                pre_test_sh=(pretest or ("", ""))[0],
                stamped_identity=(pretest or ("", ""))[1],
            ),
        },
    }
    # INTEGRITY BASELINE: the paths the harness digests after setup and re-checks before the verifier,
    # and the commands whose OUTPUT is protected. The keys' shape (present only when non-empty) comes
    # from the one helper every row producer uses, so a loop-folded row and this row agree.
    metadata["tmax"].update(tmax_protected_fields(protected_paths, protected_cmds))
    if build_context:
        metadata["build_context"] = build_context
    if daytona_mem_gb:
        metadata["daytona_mem_gb"] = daytona_mem_gb
    if daytona_cpu:
        metadata["daytona_cpu"] = daytona_cpu
    if agent_timeout_sec and os.environ.get("SWE_EMIT_AGENT_TIMEOUT", "0") == "1":
        metadata["agent_timeout_sec"] = agent_timeout_sec
    entrypoint = _entrypoint_command(dockerfile)
    if entrypoint:
        metadata["entrypoint"] = entrypoint
    if resources:
        metadata.update(resources)
    return {"prompt": instruction, "label": task_id, "metadata": metadata}, "ok"


def build_rows(
    tasks_root: str,
    rows: list[dict],
    *,
    resource_map: dict[str, dict[str, int]],
    limit: int | None = None,
    seed: int = 42,
    max_oracle_commands: int | None = None,
    inject_agent_runtime: bool = True,
) -> tuple[list[dict], dict[str, int]]:
    """Every split row to a trainer row, applying prepare_rts_data's filters. Same shuffle rule:
    task order is shuffled with ``seed`` before the ``limit`` cut."""
    pretest_map = {
        r["task_id"]: (r["pre_test_sh"], r["pre_test_env_identity"])
        for r in rows
        if (r.get("pre_test_sh") or "").strip()
    }
    protected = protected_paths_map(rows)
    protected_cmds = protected_cmds_map(rows)
    ids = sorted(r["task_id"] for r in rows)
    random.Random(seed).shuffle(ids)
    out: list[dict] = []
    reasons: dict[str, int] = {}
    for tid in ids:
        row, reason = to_row(
            os.path.join(tasks_root, tid),
            inject_agent_runtime=inject_agent_runtime,
            resources=resource_map.get(tid),
            pretest=pretest_map.get(tid),
            protected_paths=protected.get(tid),
            protected_cmds=protected_cmds.get(tid),
        )
        if (
            row is not None
            and max_oracle_commands is not None
            and row["metadata"]["oracle_commands"] > max_oracle_commands
        ):
            row, reason = None, "oracle_over_turn_budget"
        reasons[reason] = reasons.get(reason, 0) + 1
        if row is not None:
            out.append(row)
            if limit is not None and len(out) >= limit:
                break
    return out, reasons


def write_jsonl(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def prepare(
    *,
    parquet_path: str,
    tar_path: str,
    out: str,
    work_dir: str,
    expect_rows: int | None = None,
    limit: int | None = None,
    seed: int = 42,
    max_oracle_commands: int | None = None,
    inject_agent_runtime: bool = True,
    smoke_size: int = 0,
    peaks_path: str | None = None,
) -> dict:
    """The whole pipeline on local files; returns the counts the CLI prints. Raises RefuseError."""
    rows = load_split(parquet_path)
    if expect_rows is not None and limit is None and len(rows) != expect_rows:
        raise RefuseError(f"split has {len(rows)} rows, expected {expect_rows}")
    hooked = assert_hook_pairing(rows)
    tasks_root = verify_and_extract(tar_path, rows, work_dir)
    resource_map = _load_resource_map(parquet_path)
    if peaks_path is not None:
        latest = load_allocations(peaks_path)
        if set(latest) != {r["task_id"] for r in rows}:
            raise RefuseError("peaks and split must have identical task IDs")
        resource_map = {
            tid: {
                "daytona_cpu": r["cpu"],
                "daytona_mem_gb": r["mem_gb"],
                "daytona_disk_gb": r["disk_gb"],
            }
            for tid, r in latest.items()
        }
    built, reasons = build_rows(
        tasks_root,
        rows,
        resource_map=resource_map,
        limit=limit,
        seed=seed,
        max_oracle_commands=max_oracle_commands,
        inject_agent_runtime=inject_agent_runtime,
    )
    if not built:
        raise RefuseError(f"produced 0 rows (filters: {reasons})")
    if limit is None and len(built) != len(rows):
        raise RefuseError(
            f"built {len(built)} rows of {len(rows)} expected; filters: {reasons}"
        )
    matched, stamped, unstamped = selfcheck_env_identities(built)  # raises on 0 matches
    if unstamped:
        raise RefuseError(
            f"{unstamped} row(s) carry pre_test_sh with no usable env identity pair"
        )
    write_jsonl(built, out)
    if smoke_size > 0:
        smoke = os.path.join(
            os.path.dirname(os.path.abspath(out)), "reaudit_smoke.jsonl"
        )
        write_jsonl(built[:smoke_size], smoke)
    return {
        "rows": len(built),
        "hooked": hooked,
        "protected": sum(
            1 for r in built if r["metadata"]["tmax"].get("protected_paths")
        ),
        "protected_cmds": sum(
            1 for r in built if r["metadata"]["tmax"].get("protected_cmds")
        ),
        "stamped_matched": matched,
        "stamped_total": stamped,
        "reasons": reasons,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument(
        "--revision",
        default=HF_REVISION,
        help=f"{HF_REPO} ref to resolve once (default: main)",
    )
    ap.add_argument(
        "--token-file",
        default=os.environ.get("TMAX_HF_TOKEN_FILE"),
        help="file holding the HF token (read at use, never printed); default $TMAX_HF_TOKEN_FILE; "
        "a public revision needs none",
    )
    ap.add_argument("--cache-dir", default=None, help="huggingface_hub cache dir")
    ap.add_argument(
        "--parquet", default=None, help="local splits/reaudit.parquet (skips the fetch)"
    )
    ap.add_argument(
        "--tar",
        default=None,
        help="local data/tasks-reaudit-00000.tar (skips the fetch)",
    )
    ap.add_argument(
        "--peaks", default=None, help="local reaudit_full.parquet, with --parquet/--tar"
    )
    ap.add_argument(
        "--source-dir",
        default=None,
        help="preserve Hub sources under this directory/<commit>/ for breeding",
    )
    ap.add_argument(
        "--no-sha-pin",
        action="store_true",
        help="deprecated compatibility flag; release-specific SHA pins no longer exist",
    )
    ap.add_argument(
        "--expect-rows",
        type=int,
        default=None,
        help="optional assertion on the split row count",
    )
    ap.add_argument(
        "--work-dir",
        default=None,
        help="where packages are extracted (default: a tempdir)",
    )
    ap.add_argument("--limit", type=int, default=None, help="emit at most N tasks")
    ap.add_argument("--seed", type=int, default=42, help="task-order shuffle seed")
    ap.add_argument("--max-oracle-commands", type=int, default=None, metavar="N")
    ap.add_argument(
        "--inject-agent-runtime", action=argparse.BooleanOptionalAction, default=True
    )
    ap.add_argument(
        "--smoke-size",
        type=int,
        default=0,
        help="also write reaudit_smoke.jsonl with N rows",
    )
    args = ap.parse_args()

    if (args.parquet is None) != (args.tar is None):
        ap.error("--parquet and --tar go together")
    if args.peaks is not None and args.parquet is None:
        ap.error("--peaks requires local --parquet and --tar")
    if args.source_dir is not None and args.parquet is not None:
        ap.error(
            "--source-dir requires a Hub-resolved revision; local inputs have no verified HF identity"
        )
    if args.no_sha_pin:
        print(
            "--no-sha-pin is obsolete; Hub digests and package consistency are still checked",
            file=sys.stderr,
        )

    work = None
    try:
        if args.parquet is None:
            snapshot = fetch_snapshot(
                revision=args.revision,
                token=_read_token(args.token_file),
                cache_dir=args.cache_dir,
            )
        else:
            files = {
                HF_PARQUET: file_record(args.parquet),
                HF_TAR: file_record(args.tar),
            }
            if args.peaks is not None:
                files[HF_PEAKS] = file_record(args.peaks)
            snapshot = {
                "repo": None,
                "requested_revision": None,
                "revision": None,
                "files": files,
            }
        files = snapshot["files"]
        parquet_path, tar_path = files[HF_PARQUET]["path"], files[HF_TAR]["path"]
        if HF_PEAKS in files:
            validate_peaks(
                files[HF_PEAKS]["path"],
                {r["task_id"] for r in load_split(parquet_path)},
            )
        if args.source_dir and os.path.exists(
            os.path.join(args.source_dir, snapshot["revision"])
        ):
            raise RefuseError(
                "source snapshot already exists; reuse it without overwriting"
            )
        work = args.work_dir or tempfile.mkdtemp(prefix="tmax_reaudit_")
        summary = prepare(
            parquet_path=parquet_path,
            tar_path=tar_path,
            out=args.out,
            work_dir=work,
            expect_rows=args.expect_rows,
            limit=args.limit,
            seed=args.seed,
            max_oracle_commands=args.max_oracle_commands,
            inject_agent_runtime=args.inject_agent_runtime,
            smoke_size=args.smoke_size,
            peaks_path=files[HF_PEAKS]["path"] if HF_PEAKS in files else None,
        )
        sources = (
            publish_sources(snapshot, os.path.join(work, MEMBER_ROOT), args.source_dir)
            if args.source_dir
            else None
        )
        manifest_path = os.path.splitext(args.out)[0] + ".manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(
                {
                    **snapshot,
                    "sources": sources,
                    "output": file_record(args.out),
                    "preparation": summary,
                },
                f,
                indent=2,
            )
            f.write("\n")
    except RefuseError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        if args.work_dir is None and work is not None:
            shutil.rmtree(work, ignore_errors=True)
    print(
        f"dataset revision: {snapshot['revision'] or 'local inputs'}; manifest: {manifest_path}"
    )
    if sources:
        print(f"TMax sources: {os.path.dirname(sources['tmax-clean'])}")
    print(
        f"wrote {summary['rows']} reaudit tasks -> {args.out}  "
        f"(hooked {summary['hooked']}, env-identity self-check "
        f"{summary['stamped_matched']}/{summary['stamped_total']} stamped rows match; "
        f"protected paths on {summary['protected']} row(s), "
        f"protected commands on {summary['protected_cmds']} row(s))"
    )
    for reason, n in sorted(summary["reasons"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:32s} {n}")


if __name__ == "__main__":
    main()
