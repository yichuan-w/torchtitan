# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""prepare_tmax_reaudit_data: the reaudit split -> trainer rows, offline, on a 3-row fixture.

One hooked task, one unhooked, one with a BROKEN hook pair (a script and no identity). Pure /
offline -- no network, no sandbox, no torch: the tmax modules are loaded from their files and
registered under their package names, exactly as test_pretest_drift_guard.py does, so the
script's own package-style imports resolve without the package ``__init__`` chain.
Run: ``python3 test_prepare_tmax_reaudit_data.py`` (needs pyarrow, as the script does).
"""

import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tarfile
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_PKG = "torchtitan.experiments.rl.examples.tmax"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.{name}", _HERE / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


PREP = _load("prepare_tmax_data")
RTS = _load("prepare_rts_data")
R = _load("prepare_tmax_reaudit_data")

_REF = "hamishi740/swerl-tmax-v3:0123456789ab"
_IDENTITY = "image:" + _REF
_DOCKERFILE = f"# build-nothing bundle\nFROM docker.io/{_REF}\n".encode()
_TEST_SH = b"#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n"
_PRE_TEST = b"#!/bin/bash\nsha256sum -c /tests/reference_pins.sha256 || exit 1\n"
_COLUMNS = [
    "task_id",
    "task_group_id",
    "task_content_sha256",
    "validation_status",
    "instruction",
    "solution",
    "dockerfile",
    "member_prefix",
    "file_count",
    "archive_entry_count",
    "uncompressed_bytes",
    "shard",
    "terminal_domain",
    "tw_source_type",
    "req_cpus",
    "req_memory_mb",
    "base_image",
    "est_disk_mb",
    "verdict_flipped",
    "reward_verdict",
    "reference_partial",
    "dockerfile_repaired",
    "pre_test_sh",
    "pre_test_env_identity",
]


def _package(tid: str, with_pins: bool) -> dict[str, bytes]:
    files = {
        "instruction.md": f"Fixture task {tid}: make the check pass.\n".encode(),
        "environment/Dockerfile": _DOCKERFILE,
        "tests/test.sh": _TEST_SH,
        "solution/solve.sh": b"#!/bin/bash\ntouch /app/done\n",
        "setup.sh": b"#!/bin/bash\n",
    }
    if with_pins:
        files["tests/reference_pins.sha256"] = b"deadbeef  /app/pinned\n"
    return files


def _content_sha(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + files[rel] + b"\0")
    return h.hexdigest()


def _fixture(
    rows_spec: list[tuple[str, str, str]],
    tamper: str | None = None,
    drop_pkg: str | None = None,
    binary_fixture: str | None = None,
    protected: dict[str, str] | None = None,
    protected_cmds: dict[str, str] | None = None,
):
    """``protected`` / ``protected_cmds`` = {task_id: the CELL text}; when given, that column is written for
    every row ("" where unspecified). When None the column is absent, as in the first published cut."""
    """(parquet_path, tar_path, workdir). rows_spec: (task_id, pre_test_sh, pre_test_env_identity)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = pathlib.Path(tempfile.mkdtemp(prefix="reaudit_fx_"))
    tar_path = d / "tasks-reaudit-00000.tar"
    rows = []
    with tarfile.open(tar_path, "w") as tf:
        for tid, sh, idn in rows_spec:
            files = _package(tid, with_pins=bool(sh))
            if binary_fixture == tid:
                files[
                    "tests/blob.bin"
                ] = b"\xff\xfe\x00binary"  # not UTF-8: prepare_rts_data refuses such a package
            sha = _content_sha(files)
            if tamper == tid:
                files["instruction.md"] += b"tampered after the sha was recorded\n"
            if drop_pkg != tid:
                for rel in sorted(files):
                    info = tarfile.TarInfo(f"tasks/{tid}/{rel}")
                    info.size = len(files[rel])
                    tf.addfile(info, io.BytesIO(files[rel]))
            rows.append(
                {
                    "task_id": tid,
                    "task_group_id": tid,
                    "task_content_sha256": sha,
                    "validation_status": "tmax_verified",
                    "instruction": files["instruction.md"].decode(),
                    "solution": files["solution/solve.sh"].decode(),
                    "dockerfile": _DOCKERFILE.decode(),
                    "member_prefix": f"tasks/{tid}",
                    "file_count": len(files),
                    "archive_entry_count": len(files),
                    "uncompressed_bytes": sum(len(v) for v in files.values()),
                    "shard": "data/tasks-reaudit-00000.tar",
                    "terminal_domain": "debugging",
                    "tw_source_type": "tmax_open_instruct",
                    "req_cpus": 1.0,
                    "req_memory_mb": 2048.0,
                    "base_image": _REF,
                    "est_disk_mb": 1024.0,
                    "verdict_flipped": False,
                    "reward_verdict": "pass",
                    "reference_partial": False,
                    "dockerfile_repaired": False,
                    "pre_test_sh": sh,
                    "pre_test_env_identity": idn,
                }
            )
    cols = list(_COLUMNS)
    if protected is not None:
        cols.append("protected_paths")
        for r in rows:
            r["protected_paths"] = protected.get(r["task_id"], "")
    if protected_cmds is not None:
        cols.append("protected_cmds")
        for r in rows:
            r["protected_cmds"] = protected_cmds.get(r["task_id"], "")
    table = pa.table({c: [r[c] for r in rows] for c in cols})
    pq_path = d / "reaudit.parquet"
    pq.write_table(table, pq_path)
    return str(pq_path), str(tar_path), str(d / "work")


PROTECTED = [
    "/app/pinned",
    "/app/data dir/model.bin",
    "tests",
]  # absolute, absolute WITH A SPACE, relative
PROTECTED_CMDS = [
    "sqlite3 /app/db \"select count(*) from t where n='x'\""
]  # both quote characters, verbatim
HOOKED = ("task_000001_aaaaaaaa", _PRE_TEST.decode(), _IDENTITY)
UNHOOKED = ("task_000002_bbbbbbbb", "", "")
BROKEN = ("task_000003_cccccccc", _PRE_TEST.decode(), "")  # a script with no identity


def _prepare(spec, **kw):
    """(summary, rows, work_dir) for a fixture built from ``spec``; fixture kwargs are tamper/drop_pkg."""
    _fx = ("tamper", "drop_pkg", "binary_fixture", "protected", "protected_cmds")
    pq_path, tar_path, work = _fixture(
        spec, **{k: v for k, v in kw.items() if k in _fx}
    )
    out = pathlib.Path(work).parent / "out.jsonl"
    summary = R.prepare(
        parquet_path=pq_path,
        tar_path=tar_path,
        out=str(out),
        work_dir=work,
        expect_rows=len(spec),
        **{k: v for k, v in kw.items() if k not in _fx},
    )
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    return summary, rows, work


def test_hooked_and_unhooked_rows_carry_exactly_what_grading_reads():
    summary, rows, _work = _prepare([HOOKED, UNHOOKED])
    assert summary["rows"] == 2 and summary["hooked"] == 1, summary
    assert summary["stamped_matched"] == 1 and summary["stamped_total"] == 1, summary
    by = {r["label"]: r for r in rows}
    tm = by[HOOKED[0]]["metadata"]["tmax"]
    # the three fields grading.py reads, spelled the way it reads them -- not the parquet's column names
    assert tm["pre_test_sh"] == HOOKED[1]
    assert (
        tm["pretest_env_identity"] == _IDENTITY
    )  # stamped, from the dataset, verbatim
    assert (
        tm["pretest_episode_env_identity"] == _IDENTITY
    )  # computed from the bare-FROM Dockerfile, UNPREFIXED
    assert (
        "pre_test_env_identity" not in tm
    )  # the column name must not leak into the row
    assert tm["task_id"] == HOOKED[0]
    # an unhooked task carries NONE of the hook keys: absent, not empty, so the grading block is a no-op
    tm2 = by[UNHOOKED[0]]["metadata"]["tmax"]
    assert not any(k.startswith("pre") for k in tm2), tm2
    assert set(tm2) == {"test_sh", "fixtures", "reward_path"}
    # the fixture file rides along as a grading fixture for the hooked task
    assert "tests/reference_pins.sha256" in tm["fixtures"]
    # the split's resource columns became the sandbox sizing, clamped to the floors
    md = by[HOOKED[0]]["metadata"]
    assert (
        md["daytona_cpu"] == 1
        and md["daytona_mem_gb"] == 2
        and md["daytona_disk_gb"] == 10
    ), md
    assert (
        md["instance_id"] == HOOKED[0]
        and md["image"] == ""
        and md["dockerfile"] == _DOCKERFILE.decode()
    )


def test_rows_are_one_to_one_with_prepare_rts_data_outside_the_hook():
    """The reference oracle: the same extracted package through prepare_rts_data's own row builder
    (unmodified) must give the same row. This is what pins the one-to-one claim instead of asserting it.

    EXACTLY NINE keys are excluded from the equality, and each is then asserted on its own against a
    fixture-derived expectation, so the exclusion narrows the oracle without leaving anything unchecked:
      tmax.pre_test_sh, tmax.pretest_env_identity, tmax.pretest_episode_env_identity, tmax.task_id
          -- the four fields _pretest_tmax_fields adds for a HOOKED task; the pre-8 _to_row never calls
             it, so the reference row cannot carry them (and for an unhooked task neither row does);
      metadata.daytona_cpu, metadata.daytona_mem_gb, metadata.daytona_disk_gb
          -- sizing from the split's req_cpus / req_memory_mb / est_disk_mb via _load_resource_map; the
             reference _to_row is called with no resources, so it emits none of them;
      tmax.protected_paths, tmax.protected_cmds
          -- the integrity-baseline entries from the split's protected_paths / protected_cmds columns;
             prepare_rts_data has no such input, so the reference row never carries them.
    Everything else -- prompt, label, instance_id, image, dockerfile, workdir, problem_statement,
    oracle_commands, tmax.test_sh, tmax.fixtures, tmax.reward_path, and any build_context / entrypoint /
    toml-derived key -- must be byte-equal."""
    _HOOK_KEYS = (
        "pre_test_sh",
        "pretest_env_identity",
        "pretest_episode_env_identity",
        "task_id",
    )
    _PROTECTED_KEY = "protected_paths"  # the eighth excluded key; asserted below
    _CMDS_KEY = "protected_cmds"  # the ninth; likewise
    _SIZING_KEYS = ("daytona_cpu", "daytona_mem_gb", "daytona_disk_gb")
    # the fixture's split columns (see _fixture) through prepare_rts_data's own clamping rule
    import math

    _expect_sizing = {
        "daytona_cpu": max(RTS._DAYTONA_CPU_FLOOR, int(round(1.0))),
        "daytona_mem_gb": max(RTS._DAYTONA_MEM_GB_FLOOR, math.ceil(2048.0 / 1024)),
        "daytona_disk_gb": max(RTS._DAYTONA_DISK_GB_FLOOR, math.ceil(1024.0 / 1024)),
    }
    summary, rows, work = _prepare(
        [HOOKED, UNHOOKED],
        seed=7,
        protected={HOOKED[0]: json.dumps(PROTECTED)},
        protected_cmds={HOOKED[0]: json.dumps(PROTECTED_CMDS)},
    )
    work = pathlib.Path(work)
    hooked_ids = {HOOKED[0]}
    for r in rows:
        tid = r["label"]
        ref, reason = RTS._to_row(str(work / "tasks" / tid))
        assert reason == "ok", reason
        ours = json.loads(json.dumps(r))
        popped_hook = {
            k: ours["metadata"]["tmax"].pop(k)
            for k in _HOOK_KEYS
            if k in ours["metadata"]["tmax"]
        }
        popped_protected = ours["metadata"]["tmax"].pop(_PROTECTED_KEY, None)
        popped_cmds = ours["metadata"]["tmax"].pop(_CMDS_KEY, None)
        popped_sizing = {
            k: ours["metadata"].pop(k) for k in _SIZING_KEYS if k in ours["metadata"]
        }
        assert ours == ref, tid
        # the excluded keys, each against what the fixture says it must be
        assert not any(
            k in ref["metadata"]["tmax"] for k in _HOOK_KEYS
        ), tid  # the oracle never carries them
        assert not any(
            k in ref["metadata"] for k in _SIZING_KEYS
        ), tid  # nor the sizing, called without resources
        if tid in hooked_ids:
            assert set(popped_hook) == set(_HOOK_KEYS), popped_hook
            assert popped_hook["task_id"] == tid
            assert popped_hook["pre_test_sh"] == HOOKED[1]
            assert (
                popped_hook["pretest_env_identity"]
                == popped_hook["pretest_episode_env_identity"]
                == _IDENTITY
            )
        else:
            assert (
                popped_hook == {}
            ), popped_hook  # an unhooked task has none of the four
        assert popped_sizing == _expect_sizing, (tid, popped_sizing)
        assert (
            _PROTECTED_KEY not in ref["metadata"]["tmax"]
        ), tid  # the oracle never carries it
        assert _CMDS_KEY not in ref["metadata"]["tmax"], tid
        if tid in hooked_ids:
            assert (
                popped_protected == PROTECTED
            ), popped_protected  # the fixture's list, verbatim
            assert (
                popped_cmds == PROTECTED_CMDS
            ), popped_cmds  # never re-split: quotes and spaces intact
        else:
            assert (
                popped_protected is None and popped_cmds is None
            )  # absent, not an empty list


def test_a_broken_hook_pair_refuses_the_whole_run():
    """A script without the environment it was stamped for cannot be run safely; and it is a
    refusal, not a skip, because a skipped row looks like a task with no hook."""
    try:
        _prepare([HOOKED, UNHOOKED, BROKEN])
    except R.RefuseError as e:
        assert BROKEN[0] in str(e) and "env identity" in str(e), e
    else:
        raise AssertionError("a broken pair must refuse")
    # the reverse direction too: an identity with no script
    try:
        _prepare([HOOKED, UNHOOKED, ("task_000004_dddddddd", "", _IDENTITY)])
    except R.RefuseError as e:
        assert "task_000004_dddddddd" in str(e), e
    else:
        raise AssertionError("an identity without a script must refuse")


def test_tampered_or_missing_package_refuses_and_names_the_id():
    try:
        _prepare([HOOKED, UNHOOKED], tamper=UNHOOKED[0])
    except R.RefuseError as e:
        assert UNHOOKED[0] in str(e) and "task_content_sha256" in str(e), e
    else:
        raise AssertionError("a package that does not reproduce its sha must refuse")
    try:
        _prepare([HOOKED, UNHOOKED], drop_pkg=HOOKED[0])
    except R.RefuseError as e:
        assert HOOKED[0] in str(e) and "no package" in str(e), e
    else:
        raise AssertionError("a split row with no package must refuse")


def test_a_non_utf8_fixture_refuses_the_package_by_name_as_prepare_rts_data_does():
    """prepare_rts_data's rule, mirrored: fixtures travel inside the row as text, so a tests/ file that is
    not UTF-8 refuses the package (reason names the file) instead of being skipped -- skipped, the verifier
    found it missing at grade time and the failure surfaced two steps away. With --expect-rows the run then
    refuses on the count, and the filter reason is in the message so the reader sees WHY a row is missing."""
    try:
        _prepare([HOOKED, UNHOOKED], binary_fixture=UNHOOKED[0])
    except R.RefuseError as e:
        assert "built 1 rows of 2" in str(e) and "tests_fixture_binary" in str(e), e
    else:
        raise AssertionError(
            "a package with a non-UTF-8 fixture must be filtered, and the count guard must refuse"
        )
    # and prepare_rts_data's own _to_row refuses the same package with the same reason -- the oracle for this rule
    pq_path, tar_path, work = _fixture([UNHOOKED], binary_fixture=UNHOOKED[0])
    R.verify_and_extract(tar_path, R.load_split(pq_path), work)
    row, reason = RTS._to_row(str(pathlib.Path(work) / "tasks" / UNHOOKED[0]))
    assert row is None and reason.startswith("tests_fixture_binary"), reason


def test_protected_paths_pass_through_on_a_three_row_fixture():
    """hooked with paths -> the list lands in tmax; unhooked -> no key; an EMPTY cell -> no key (absent, never an
    empty list). And with the column absent altogether -- today's published cut -- no row carries the key."""
    third = ("task_000005_eeeeeeee", "", "")
    summary, rows, _w = _prepare(
        [HOOKED, UNHOOKED, third],
        protected={HOOKED[0]: json.dumps(PROTECTED), third[0]: ""},
        protected_cmds={HOOKED[0]: json.dumps(PROTECTED_CMDS), third[0]: ""},
    )
    by = {r["label"]: r["metadata"]["tmax"] for r in rows}
    assert (
        by[HOOKED[0]]["protected_paths"] == PROTECTED
    )  # 3 entries, one with a space, as a LIST
    assert (
        by[HOOKED[0]]["protected_cmds"] == PROTECTED_CMDS
    )  # the quotes survive verbatim
    for k in ("protected_paths", "protected_cmds"):
        assert k not in by[UNHOOKED[0]] and k not in by[third[0]], k
    assert summary["protected"] == 1 and summary["protected_column_present"] is True
    assert (
        summary["protected_cmds"] == 1
        and summary["protected_cmds_column_present"] is True
    )
    # a command entry with a newline refuses by id (the hook's manifest is line-based)
    try:
        _prepare(
            [HOOKED, UNHOOKED],
            protected_cmds={HOOKED[0]: json.dumps(["echo 1\necho 2"])},
        )
    except R.RefuseError as e:
        assert HOOKED[0] in str(e) and "newline" in str(e), e
    else:
        raise AssertionError("a newline in a protected_cmds entry must refuse")
    summary, rows, _w = _prepare([HOOKED, UNHOOKED])  # both columns absent
    assert not any(
        k in r["metadata"]["tmax"]
        for r in rows
        for k in ("protected_paths", "protected_cmds")
    )
    assert summary["protected"] == 0 and summary["protected_column_present"] is False
    assert (
        summary["protected_cmds"] == 0
        and summary["protected_cmds_column_present"] is False
    )
    # a cell that is present but not a JSON list of non-empty strings refuses by id
    for bad in ('"not-a-list"', '["ok", ""]', "{oops"):
        try:
            _prepare([HOOKED, UNHOOKED], protected={UNHOOKED[0]: bad})
        except R.RefuseError as e:
            assert UNHOOKED[0] in str(e), e
        else:
            raise AssertionError(f"malformed protected_paths must refuse: {bad!r}")


def test_row_count_guard_and_limit():
    pq_path, tar_path, work = _fixture([HOOKED, UNHOOKED])
    out = str(pathlib.Path(work).parent / "o.jsonl")
    try:
        R.prepare(
            parquet_path=pq_path,
            tar_path=tar_path,
            out=out,
            work_dir=work,
            expect_rows=3,
        )
    except R.RefuseError as e:
        assert "expected 3" in str(e), e
    else:
        raise AssertionError("a row count other than --expect-rows must refuse")
    s = R.prepare(
        parquet_path=pq_path,
        tar_path=tar_path,
        out=out,
        work_dir=work + "2",
        expect_rows=3,
        limit=1,
    )
    assert s["rows"] == 1  # --limit lifts the count guard, as in prepare_rts_data


def test_package_sha_rule_matches_the_split_builder():
    """relpath + NUL + content + NUL over sorted FILE members, relpath relative to the package prefix."""
    files = _package(
        "task_000009_99999999", with_pins=False
    )  # an unhooked fixture package has 5 files
    pq_path, tar_path, _w = _fixture([("task_000009_99999999", "", "")])
    with tarfile.open(tar_path) as tf:
        groups = R._package_members(tf)
        assert list(groups) == ["tasks/task_000009_99999999"]
        assert R._package_sha256(
            tf, "tasks/task_000009_99999999", groups["tasks/task_000009_99999999"]
        ) == _content_sha(files)


def test_a_tar_member_outside_the_task_root_refuses():
    d = pathlib.Path(tempfile.mkdtemp())
    p = d / "bad.tar"
    with tarfile.open(p, "w") as tf:
        info = tarfile.TarInfo("../escape.sh")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    with tarfile.open(p) as tf:
        try:
            R._package_members(tf)
        except R.RefuseError as e:
            assert "unexpected tar member" in str(e), e
        else:
            raise AssertionError("a member outside tasks/ must refuse")


if __name__ == "__main__":
    import re

    _tests = [f for n, f in list(globals().items()) if n.startswith("test_")]
    _declared = len(re.findall(r"^def test_", pathlib.Path(__file__).read_text(), re.M))
    assert (
        len(_tests) == _declared
    ), f"{_declared} declared, {len(_tests)} visible to the runner"
    for f in _tests:
        f()
    print(f"ok {len(_tests)}")
