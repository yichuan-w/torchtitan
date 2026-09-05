# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a training JSONL from the ``Fzz1/Tmax-Tasks-Clean`` ``reaudit`` split.

The reaudit split is the tmax corpus after the 2026-09 re-audit: 456 tasks published as a
seeds-layout package tar plus a 24-column parquet, at a PINNED dataset revision::

    splits/reaudit.parquet            456 x 24   (the split; hook columns 22-23)
    data/tasks-reaudit-00000.tar      456 packages, 2366 members, all files, under tasks/<task_id>/

    tasks/<task_id>/instruction.md
    tasks/<task_id>/environment/Dockerfile   # a bare single FROM <ref> for every task
    tasks/<task_id>/tests/test.sh            # verifier: writes /logs/verifier/reward.txt
    tasks/<task_id>/tests/reference_pins.sha256   (86 tasks)
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
  * the fetched parquet / tar bytes differ from the published sha256 (pinned by default);
  * a row whose ``pre_test_sh`` and ``pre_test_env_identity`` are not both set or both empty
    -- a hook without the environment it was stamped for cannot be run safely;
  * a split row with no package in the tar, or a package whose bytes do not reproduce the row's
    ``task_content_sha256`` (sha256 over the package's FILE members in sorted member-name order,
    each contributing relpath + NUL + content + NUL, relpath relative to the package prefix);
  * a tar member that is not a plain file under ``tasks/<task_id>/``;
  * a row count other than ``--expect-rows`` (456) unless ``--limit`` is given;
  * 0 of the stamped rows matching their episode identity (the corpus-wide silent-skip guard),
    or any stamped row with no usable identity pair.
An EMPTY ``pre_test_sh`` is a task with no hook (293 of 456), never a failure.

Run (the token is read from a FILE at use and never printed; a public revision needs none)::

    python -m torchtitan.experiments.rl.examples.tmax.prepare_tmax_reaudit_data \
        --out mast_rl/swe_assets/reaudit_train.jsonl \
        [--token-file /path/to/hf.txt] [--limit N] [--seed 42] [--max-oracle-commands 64]

Offline / tests: ``--parquet PATH --tar PATH`` skip the fetch and read local copies.
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

from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
    _AGENT_RUNTIME_BLOCK,
    _build_context,
    _entrypoint_command,
    _grading_fixtures,
    _join_continuations,
    _load_resource_map,
    _oracle_commands,
    _REJECT_PRIVILEGED,
    _strip_canary,
    _strip_comments,
    _workdir_from_dockerfile,
)
from torchtitan.experiments.rl.examples.tmax.prepare_tmax_data import (
    _DEFAULT_IMAGE_PREFIX,
    _pretest_tmax_fields,
    _REWARD_PATH,
    selfcheck_env_identities,
)

HF_REPO = "Fzz1/Tmax-Tasks-Clean"
HF_REVISION = (
    "7eb7c31a3d1eb644284b5871af0d1120ab7361a3"  # main moves; the split does not
)
HF_PARQUET = "splits/reaudit.parquet"
HF_TAR = "data/tasks-reaudit-00000.tar"
# sha256 of the published bytes at HF_REVISION (the split builder's own publish record).
PARQUET_SHA256 = "70ebe801ffd38c17ee2faefa18295e37f8d2e244a9884a5c68eeea39ea5fa641"
TAR_SHA256 = "6e0375659a6c569343df1df2f1c5fe7d003f2cbb716480563830d1c8a2a67620"
EXPECT_ROWS = 456
MEMBER_ROOT = "tasks"

_HOOK_COLUMNS = ("pre_test_sh", "pre_test_env_identity")
_NEEDED_COLUMNS = (
    "task_id",
    "member_prefix",
    "task_content_sha256",
    "shard",
    *_HOOK_COLUMNS,
)


class RefuseError(RuntimeError):
    """A precondition the trainer must never see violated; the message names ids, never content."""


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
    """Download the split parquet and the package tar at the pinned revision; return local paths."""
    from huggingface_hub import hf_hub_download  # local import: not needed offline

    tok = _read_token(token_file)
    paths = []
    for name in (HF_PARQUET, HF_TAR):
        paths.append(
            hf_hub_download(
                HF_REPO,
                name,
                repo_type="dataset",
                revision=revision,
                token=tok,
                cache_dir=cache_dir,
            )
        )
    return paths[0], paths[1]


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
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    missing = [c for c in _NEEDED_COLUMNS if c not in table.column_names]
    if missing:
        raise RefuseError(f"split {parquet_path} lacks column(s) {missing}")
    rows = table.to_pylist()
    ids = [r["task_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise RefuseError("split has duplicate task_id values")
    return rows


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
        groups.setdefault("/".join(parts[:2]), []).append(m)
    return groups


def _package_sha256(
    tar: tarfile.TarFile, prefix: str, members: list[tarfile.TarInfo]
) -> str:
    """The split builder's task_content_sha256: sorted file members, relpath + NUL + content + NUL,
    relpath relative to the PACKAGE prefix ('instruction.md'), never the tar root."""
    h = hashlib.sha256()
    for m in sorted(members, key=lambda m: m.name):
        rel = m.name[len(prefix) + 1 :]
        f = tar.extractfile(m)
        assert f is not None
        h.update(rel.encode() + b"\0" + f.read() + b"\0")
    return h.hexdigest()


def verify_and_extract(tar_path: str, rows: list[dict], out_root: str) -> str:
    """Verify every split row's package against the tar and extract it; return the tasks root."""
    tasks_root = os.path.join(out_root, MEMBER_ROOT)
    os.makedirs(tasks_root, exist_ok=True)
    with tarfile.open(tar_path) as tar:
        groups = _package_members(tar)
        missing = [r["task_id"] for r in rows if r["member_prefix"] not in groups]
        if missing:
            raise RefuseError(
                f"{len(missing)} split row(s) have no package in the tar: "
                f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
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
    inject_agent_runtime: bool = False,
    resources: dict[str, int] | None = None,
    pretest: tuple[str, str] | None = None,
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
        dockerfile = dockerfile.rstrip("\n") + "\n" + _AGENT_RUNTIME_BLOCK

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
            oracle_commands = _oracle_commands(f.read())

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
    inject_agent_runtime: bool = False,
) -> tuple[list[dict], dict[str, int]]:
    """Every split row to a trainer row, applying prepare_rts_data's filters. Same shuffle rule:
    task order is shuffled with ``seed`` before the ``limit`` cut."""
    pretest_map = {
        r["task_id"]: (r["pre_test_sh"], r["pre_test_env_identity"])
        for r in rows
        if (r.get("pre_test_sh") or "").strip()
    }
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
    expect_rows: int | None = EXPECT_ROWS,
    limit: int | None = None,
    seed: int = 42,
    max_oracle_commands: int | None = None,
    inject_agent_runtime: bool = False,
    smoke_size: int = 0,
) -> dict:
    """The whole pipeline on local files; returns the counts the CLI prints. Raises RefuseError."""
    rows = load_split(parquet_path)
    if expect_rows is not None and limit is None and len(rows) != expect_rows:
        raise RefuseError(f"split has {len(rows)} rows, expected {expect_rows}")
    hooked = assert_hook_pairing(rows)
    tasks_root = verify_and_extract(tar_path, rows, work_dir)
    resource_map = _load_resource_map(parquet_path)
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
    if expect_rows is not None and limit is None and len(built) != expect_rows:
        raise RefuseError(
            f"built {len(built)} rows of {expect_rows} expected; filters: {reasons}"
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
        "stamped_matched": matched,
        "stamped_total": stamped,
        "reasons": reasons,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--revision", default=HF_REVISION, help="dataset revision (pinned)")
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
        "--no-sha-pin",
        action="store_true",
        help="do not assert the published sha256 of the parquet/tar (only for a NEW revision)",
    )
    ap.add_argument(
        "--expect-rows", type=int, default=EXPECT_ROWS, help="refuse on any other count"
    )
    ap.add_argument(
        "--work-dir",
        default=None,
        help="where packages are extracted (default: a tempdir)",
    )
    ap.add_argument("--limit", type=int, default=None, help="emit at most N tasks")
    ap.add_argument("--seed", type=int, default=42, help="task-order shuffle seed")
    ap.add_argument("--max-oracle-commands", type=int, default=None, metavar="N")
    ap.add_argument("--inject-agent-runtime", action="store_true")
    ap.add_argument(
        "--smoke-size",
        type=int,
        default=0,
        help="also write reaudit_smoke.jsonl with N rows",
    )
    args = ap.parse_args()

    if (args.parquet is None) != (args.tar is None):
        ap.error("--parquet and --tar go together")
    if args.parquet is None:
        parquet_path, tar_path = fetch(
            revision=args.revision, token_file=args.token_file, cache_dir=args.cache_dir
        )
    else:
        parquet_path, tar_path = args.parquet, args.tar
    if not args.no_sha_pin:
        assert_sha256(parquet_path, PARQUET_SHA256, "split parquet")
        assert_sha256(tar_path, TAR_SHA256, "package tar")

    work = args.work_dir or tempfile.mkdtemp(prefix="tmax_reaudit_")
    try:
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
        )
    except RefuseError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        if args.work_dir is None:
            shutil.rmtree(work, ignore_errors=True)
    print(
        f"wrote {summary['rows']} reaudit tasks -> {args.out}  "
        f"(hooked {summary['hooked']}, env-identity self-check "
        f"{summary['stamped_matched']}/{summary['stamped_total']} stamped rows match)"
    )
    for reason, n in sorted(summary["reasons"].items(), key=lambda kv: -kv[1]):
        print(f"  {reason:32s} {n}")


if __name__ == "__main__":
    main()
