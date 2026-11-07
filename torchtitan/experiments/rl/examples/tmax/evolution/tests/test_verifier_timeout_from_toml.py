# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""verifier_timeout_sec carried from a task's task.toml ``[verifier] timeout_sec``.

The four cases the resolver (``data_release.verifier_timeout_sec``) must get right:

  row value set   an explicit ``validated_test_timeout_s`` wins, uncapped
  toml only       no row value, the task.toml ``[verifier] timeout_sec`` is carried
  neither         nothing is set, so the rollouter applies its eval-timeout floor
  toml above cap  a toml value above the cap is clamped to the cap

The row-column path (``validated_test_timeout_s`` -> ``verifier_timeout_sec``) is
also covered end-to-end by ``test_data_release`` 's offline/grader-budget test; the
end-to-end tests here build a ``tmax_reaudit`` mix to prove task.toml actually reaches
``build()`` so the carry-over fires, and that a package without one carries nothing.
"""
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

EVOLUTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVOLUTION))
spec = importlib.util.spec_from_file_location(
    "data_release", EVOLUTION / "data_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def _task(tmp_path, name, toml_body=None):
    task = tmp_path / name
    task.mkdir()
    if toml_body is not None:
        (task / "task.toml").write_text(toml_body)
    return task


# ---------------------------------------------------------------------------
# unit: the resolver (verifier_timeout_sec) over the four cases + precedence
# ---------------------------------------------------------------------------


def test_row_value_wins_and_is_not_capped(tmp_path):
    # An explicit row value beats both the package value and task.toml, and the
    # cap that bounds the carried-over value never touches it.
    task = _task(tmp_path, "t", "[verifier]\ntimeout_sec = 1200\n")
    got = release.verifier_timeout_sec(
        {"validated_test_timeout_s": 9000}, {"verifier_timeout_sec": 777}, task
    )
    assert got == 9000


def test_toml_only_is_carried(tmp_path):
    task = _task(tmp_path, "t", "[verifier]\ntimeout_sec = 1200\n")
    assert release.verifier_timeout_sec({}, {}, task) == 1200


def test_neither_leaves_it_unset(tmp_path):
    # No row value, no package value, no task.toml -> None (rollouter floor).
    assert release.verifier_timeout_sec({}, {}, _task(tmp_path, "t")) is None


def test_toml_above_cap_is_clamped(tmp_path):
    task = _task(tmp_path, "t", "[verifier]\ntimeout_sec = 12000\n")
    assert (
        release.verifier_timeout_sec({}, {}, task)
        == release.VERIFIER_TIMEOUT_FROM_TOML_CAP_SEC
        == 1800
    )


def test_package_value_is_kept_and_toml_ignored(tmp_path):
    # A Harbor separate verifier sets md["verifier_timeout_sec"] in pack.to_row;
    # that is the task's own declared value and must be kept, not re-capped.
    task = _task(tmp_path, "t", "[verifier]\ntimeout_sec = 300\n")
    got = release.verifier_timeout_sec({}, {"verifier_timeout_sec": 9000}, task)
    assert got == 9000


# ---------------------------------------------------------------------------
# unit: the task.toml reader (toml_verifier_timeout_sec) edge cases
# ---------------------------------------------------------------------------


def test_toml_reader_reads_positive_int_and_float(tmp_path):
    read = release.toml_verifier_timeout_sec
    assert read(_task(tmp_path, "i", "[verifier]\ntimeout_sec = 900\n")) == 900
    assert read(_task(tmp_path, "f", "[verifier]\ntimeout_sec = 900.0\n")) == 900


def test_toml_reader_rejects_missing_nonpositive_and_malformed(tmp_path):
    read = release.toml_verifier_timeout_sec
    assert read(tmp_path / "does-not-exist") is None
    assert read(_task(tmp_path, "zero", "[verifier]\ntimeout_sec = 0\n")) is None
    assert read(_task(tmp_path, "neg", "[verifier]\ntimeout_sec = -5\n")) is None
    assert read(_task(tmp_path, "str", '[verifier]\ntimeout_sec = "x"\n')) is None
    assert read(_task(tmp_path, "bool", "[verifier]\ntimeout_sec = true\n")) is None
    assert read(_task(tmp_path, "nosection", "[environment]\ncpus = 2\n")) is None
    assert read(_task(tmp_path, "broken", "[verifier\ntimeout_sec = 1\n")) is None


# ---------------------------------------------------------------------------
# end-to-end: task.toml reaches build() through the tmax_reaudit adapter
# ---------------------------------------------------------------------------

_ACTIONS = json.dumps(
    [
        {
            "step_id": 1,
            "offset_sec": 0.0,
            "duration": 1.0,
            "kind": "terminal_action",
            "keystrokes": "touch /app/result\n",
        },
        {
            "step_id": 2,
            "offset_sec": 1.0,
            "duration": 0.0,
            "kind": "completion_marker",
            "keystrokes": "",
        },
        {
            "step_id": 3,
            "offset_sec": 1.0,
            "duration": 0.0,
            "kind": "completion_marker",
            "keystrokes": "",
        },
    ]
)


def _reaudit_fixture(tmp_path, monkeypatch, *, toml_body, timeout_column=None):
    members = {
        "instruction.md": "Create /app/result.\n",
        "environment/Dockerfile": "FROM ubuntu:24.04\nWORKDIR /app\n",
        "tests/test.sh": "echo 1 > /logs/verifier/reward.txt\n",
    }
    if toml_body is not None:
        members["task.toml"] = toml_body
    archive = tmp_path / "tasks.tar"
    with tarfile.open(archive, "w") as bundle:
        for tid in ("task_a", "task_b"):
            for name, content in members.items():
                member = tarfile.TarInfo(f"tasks/{tid}/{name}")
                member.size = len(content.encode())
                bundle.addfile(member, io.BytesIO(content.encode()))
    columns = {
        "task_id": ["task_a", "task_b"],
        "solution": [_ACTIONS, _ACTIONS],
        "solution_format": ["terminus_tmux_actions_v1", "terminus_tmux_actions_v1"],
        "req_cpus": [1, 1],
        "req_memory_mb": [2048, 2048],
        "est_disk_mb": [2048, 2048],
    }
    if timeout_column is not None:
        columns["validated_test_timeout_s"] = [timeout_column, timeout_column]
    metadata = tmp_path / "split.parquet"
    pq.write_table(pa.table(columns), metadata)
    monkeypatch.setattr(
        release,
        "fetch_source",
        lambda *_: {
            "split.parquet": {"path": metadata, "sha256": release.digest(metadata)},
            "tasks.tar": {"path": archive, "sha256": release.digest(archive)},
        },
    )
    monkeypatch.setattr(
        release.subprocess,
        "check_output",
        lambda args, **_: "" if "status" in args else "b" * 40,
    )
    source = {
        "name": "agentpick",
        "repo": "example/tasks",
        "revision": "a" * 40,
        "adapter": "tmax_reaudit",
        "metadata": "split.parquet",
        "archives": ["tasks.tar"],
        "count": None,
        "skip_unsolved": True,
    }
    return {"seed": 7, "sources": [source]}


def _row(output, label):
    for line in (output / "mix.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["label"] == label:
            return row
    raise AssertionError(f"row {label} not in mix")


def test_build_carries_toml_verifier_timeout(tmp_path, monkeypatch):
    config = _reaudit_fixture(
        tmp_path, monkeypatch, toml_body="[verifier]\ntimeout_sec = 1500\n"
    )
    output = release.build(config, tmp_path / "out")
    assert _row(output, "task_a")["metadata"]["verifier_timeout_sec"] == 1500


def test_build_clamps_toml_verifier_timeout(tmp_path, monkeypatch):
    config = _reaudit_fixture(
        tmp_path, monkeypatch, toml_body="[verifier]\ntimeout_sec = 12000\n"
    )
    output = release.build(config, tmp_path / "out")
    assert _row(output, "task_a")["metadata"]["verifier_timeout_sec"] == 1800


def test_build_sets_nothing_without_toml_or_column(tmp_path, monkeypatch):
    config = _reaudit_fixture(tmp_path, monkeypatch, toml_body=None)
    output = release.build(config, tmp_path / "out")
    assert "verifier_timeout_sec" not in _row(output, "task_a")["metadata"]


def test_build_row_column_beats_toml(tmp_path, monkeypatch):
    config = _reaudit_fixture(
        tmp_path,
        monkeypatch,
        toml_body="[verifier]\ntimeout_sec = 1500\n",
        timeout_column=2700,
    )
    output = release.build(config, tmp_path / "out")
    assert _row(output, "task_a")["metadata"]["verifier_timeout_sec"] == 2700
