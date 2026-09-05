# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""layout.py and rollout_record.py against LAYOUT.md: the names, the atomic
writes, the mix directory's hardlink versioning, and the one rollout file
format -- plus the dataset reading a mix the way the contract says it is served."""

from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

_STAMP = re.compile(r"^\d{8}-\d{6}Z$")


# --- names ---


def test_stamp_is_utc_second_resolution_and_sorts_by_time() -> None:
    assert layout.stamp(0) == "19700101-000000Z"
    assert layout.stamp(86400 + 3661) == "19700102-010101Z"
    assert _STAMP.match(layout.stamp())
    assert layout.stamp(1) < layout.stamp(2) < layout.stamp(10**9)


def test_safe_keeps_an_id_as_one_path_segment() -> None:
    assert layout.safe("tw_380466") == "tw_380466"
    assert layout.safe("org/task:1 x") == "org_task_1_x"
    assert layout.safe("a.b-c_d+e=f@g") == "a.b-c_d+e=f@g"


def test_signal_id_is_run_then_safe_task_and_group() -> None:
    assert (
        layout.signal_id("tmax-9b--20260904-181500Z", "tw/1", 713)
        == "tmax-9b--20260904-181500Z/tw_1--g713"
    )


# --- files ---


def test_append_jsonl_and_read_jsonl_round_trip(tmp_path) -> None:
    path = tmp_path / "a" / "b.jsonl"
    assert layout.read_jsonl(path) == []
    layout.append_jsonl(path, {"n": 1, "s": "é"})
    layout.append_jsonl(path, {"n": 2})
    # ensure_ascii=False: the file holds the character, not an escape.
    assert path.read_text(encoding="utf-8").splitlines() == [
        '{"n": 1, "s": "é"}',
        '{"n": 2}',
    ]
    assert layout.read_jsonl(path) == [{"n": 1, "s": "é"}, {"n": 2}]


def test_write_json_atomic_leaves_no_incoming_file(tmp_path) -> None:
    path = tmp_path / "x" / "status.json"
    layout.write_json_atomic(path, {"b": 1, "a": [1, 2]})
    assert json.loads(path.read_text()) == {"a": [1, 2], "b": 1}
    assert list(path.parent.iterdir()) == [path]


# --- the mix directory ---


def _rows(*ids: str) -> list[str]:
    return [json.dumps({"metadata": {"instance_id": i}}) for i in ids]


def test_mix_publish_hardlinks_live_to_the_new_version(tmp_path) -> None:
    mix = layout.MixDir(tmp_path / "mix")
    assert mix.versions() == []
    assert mix.live_version() is None

    v1, p1 = mix.publish(_rows("a", "b"), t=0)
    assert v1 == 1
    assert p1 == mix.history / "v0001--19700101-000000Z.jsonl"
    assert p1.read_text().splitlines() == _rows("a", "b")
    assert json.loads(layout.MixDir.manifest_of(p1).read_text()) == {
        "version": 1,
        "parent_version": None,
        "stamp": "19700101-000000Z",
        "sha256": layout.sha256_file(p1),
        "rows": 2,
    }
    # live.jsonl IS the version file: same inode, no second copy.
    assert mix.live.stat().st_ino == p1.stat().st_ino
    assert mix.live_version() == (1, p1)

    v2, p2 = mix.publish(_rows("a", "b", "c"), t=60)
    assert (v2, p2.name) == (2, "v0002--19700101-000100Z.jsonl")
    assert json.loads(layout.MixDir.manifest_of(p2).read_text())["parent_version"] == 1
    assert mix.versions() == [(1, p1), (2, p2)]
    assert mix.live.stat().st_ino == p2.stat().st_ino
    assert p2.stat().st_ino != p1.stat().st_ino
    assert mix.live_version() == (2, p2)
    assert not list(mix.path.glob("*.incoming"))
    assert not list(mix.history.glob("*.incoming"))


def test_mix_live_version_falls_back_to_content_when_live_is_a_copy(tmp_path) -> None:
    mix = layout.MixDir(tmp_path / "mix")
    _, p1 = mix.publish(_rows("a"), t=0)
    copy = mix.live.with_name("copy")
    copy.write_bytes(mix.live.read_bytes())
    os.replace(copy, mix.live)
    assert mix.live.stat().st_ino != p1.stat().st_ino
    assert mix.live_version() == (1, p1)


# --- the root and a run ---


def test_root_from_env_requires_trl_base(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TRL_BASE", raising=False)
    with pytest.raises(RuntimeError):
        layout.Root.from_env()
    monkeypatch.setenv("TRL_BASE", str(tmp_path))
    root = layout.Root.from_env()
    assert root.mix.path == tmp_path / "data" / "mix"
    assert root.evolution.status == tmp_path / "evolution" / "status.json"
    assert root.run("tmax-9b--x").path == tmp_path / "runs" / "tmax-9b--x"
    assert root.new_run_name(t=0) == "tmax-9b--19700101-000000Z"


def test_root_run_dirs_skip_the_latest_link_and_stray_files(tmp_path) -> None:
    root = layout.Root(tmp_path)
    older = root.runs / "tmax-9b--20260901-000000Z"
    newer = root.runs / "tmax-9b--20260902-000000Z"
    newer.mkdir(parents=True)
    older.mkdir()
    (root.runs / "notes.txt").write_text("")
    root.latest.symlink_to(newer.name)
    assert [r.name for r in root.run_dirs()] == [older.name, newer.name]


def test_run_paths_follow_the_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TRL_RUN_DIR", raising=False)
    assert layout.Run.from_env() is None
    run_dir = tmp_path / "runs" / "tmax-9b--20260904-181500Z"
    monkeypatch.setenv("TRL_RUN_DIR", str(run_dir))
    run = layout.Run.from_env()
    assert run is not None
    assert run.name == "tmax-9b--20260904-181500Z"
    assert (
        run.rollout_record("tw/380466", 713, 13)
        == run_dir / "rollouts" / "tw_380466" / "g713-r13.jsonl"
    )
    assert run.signal("tw_380466", 713) == run_dir / "signals" / "tw_380466--g713.json"
    assert run.advisory("no_tmux") == run_dir / "advisories" / "no_tmux.jsonl"
    assert run.mix_versions == run_dir / "trainer" / "mix_versions.jsonl"
    assert run.inputs_mix == run_dir / "inputs" / "mix.jsonl"
    assert run.launch_json == run_dir / "launch.json"
    assert run.signal_files() == []
    layout.write_json_atomic(run.signal("b", 2), {})
    layout.write_json_atomic(run.signal("a", 1), {})
    # A signal still being written does not match the consumer's glob.
    (run.signals / "c--g3.json.incoming").write_text("")
    assert [p.name for p in run.signal_files()] == ["a--g1.json", "b--g2.json"]


def test_task_dir_revs_are_the_r_directories_in_numeric_order(tmp_path) -> None:
    task = layout.Evolution(tmp_path / "evolution").task("tw/1")
    assert task.path == tmp_path / "evolution" / "tasks" / "tw_1"
    assert task.revs() == []
    assert task.latest_rev() is None
    for name in ("r0", "r2", "r10", "rewrites", "rx", "r1.bak"):
        (task.path / name).mkdir(parents=True)
    (task.path / "r3").write_text("")  # a file, not a revision
    assert task.revs() == [0, 2, 10]
    assert task.latest_rev() == 10
    assert task.rev(10) == task.path / "r10"
    rewrite = task.rewrite("harder", "20260904-183300Z")
    assert rewrite.path == task.rewrites / "20260904-183300Z--harder"
    assert rewrite.traces == rewrite.path / "package" / "traces"
    session = rewrite.session("agent", "20260904-183301Z")
    assert session.codex_home == rewrite.sessions / "20260904-183301Z--agent" / "codex"


# --- the rollout record ---


def test_parse_completion_splits_terminus_xml_into_fields() -> None:
    completion = (
        "<think>look first</think>\n<response>\n<analysis>Empty dir.</analysis>\n"
        "<plan>List, then run.</plan>\n<commands>\n"
        "<keystrokes>ls -la /app\n</keystrokes>\n"
        '<keystrokes duration="2.0">python3 app.py\n</keystrokes>\n'
        "</commands>\n</response><|im_end|>\n"
    )
    assert rollout_record.parse_completion(completion) == {
        "keystrokes": ["ls -la /app\n", "python3 app.py\n"],
        "analysis": "Empty dir.",
        "plan": "List, then run.",
        "think": "look first",
    }


def test_parse_completion_closing_turn_keeps_an_empty_keystrokes_list() -> None:
    # The template opens <think> for the model, so a completion starts inside it.
    completion = (
        "done</think><response><analysis>All tests pass.</analysis>"
        "<plan>Nothing left.</plan><commands></commands>"
        "<task_complete>true</task_complete></response>"
    )
    parsed = rollout_record.parse_completion(completion)
    assert parsed["keystrokes"] == []
    assert parsed["task_complete"] is True
    assert parsed["think"] == "done"
    assert "raw" not in parsed


def test_parse_completion_without_a_response_keeps_raw() -> None:
    parsed = rollout_record.parse_completion(
        "<think>hm</think>I am not sure what to do."
    )
    assert parsed == {"raw": "I am not sure what to do.", "think": "hm"}
    assert rollout_record.parse_completion("") == {"raw": ""}


def test_strip_markers_drops_the_chat_template_around_a_turn() -> None:
    reply = (
        "<|im_end|>\n<|im_start|>user\nNew Terminal Output:\nroot@box:/app# ls\n"
        "<|im_end|>\n<|im_start|>assistant\n<think>\n\n"
    )
    assert (
        rollout_record.strip_markers(reply) == "New Terminal Output:\nroot@box:/app# ls"
    )


def test_turn_line_keys_are_in_reading_order() -> None:
    line = rollout_record.turn_line(
        3,
        "<think>t</think><response><analysis>a</analysis><plan>p</plan>"
        "<commands><keystrokes>ls\n</keystrokes></commands>"
        "<task_complete>true</task_complete></response>",
        "<|im_end|>\n<|im_start|>user\nout<|im_end|>\n<|im_start|>assistant\n<think>\n",
    )
    assert list(line) == [
        "turn",
        "keystrokes",
        "task_complete",
        "output",
        "analysis",
        "plan",
        "think",
    ]
    assert line["keystrokes"] == ["ls\n"]
    assert line["output"] == "out"
    raw = rollout_record.turn_line(1, "just prose", "")
    assert list(raw) == ["turn", "raw", "output"]
    assert raw["raw"] == "just prose"


def _ids(text: str) -> list[int]:
    return [ord(c) for c in text]


def _decode(ids: list[int]) -> str:
    return "".join(chr(i) for i in ids)


def test_turns_from_tokens_recovers_each_reply_from_the_next_prompt() -> None:
    prompt_1 = _ids(
        "<|im_start|>system\nYou are Terminus.<|im_end|>\n"
        "<|im_start|>user\nTask<|im_end|>\n<|im_start|>assistant\n<think>\n"
    )
    completion_1 = _ids(
        "first</think><response><commands><keystrokes>ls\n</keystrokes>"
        "</commands></response><|im_end|>"
    )
    reply_1 = _ids(
        "\n<|im_start|>user\nNew Terminal Output:\nfoo bar<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n"
    )
    completion_2 = _ids(
        "done</think><response><commands></commands>"
        "<task_complete>true</task_complete></response><|im_end|>"
    )
    turns = [
        SimpleNamespace(prompt_token_ids=prompt_1, completion_token_ids=completion_1),
        SimpleNamespace(
            prompt_token_ids=prompt_1 + completion_1 + reply_1,
            completion_token_ids=completion_2,
        ),
    ]
    assert rollout_record.turns_from_tokens(turns, _decode) == [
        {
            "turn": 1,
            "keystrokes": ["ls\n"],
            "output": "New Terminal Output:\nfoo bar",
            "think": "first",
        },
        {
            "turn": 2,
            "keystrokes": [],
            "task_complete": True,
            "output": "",
            "think": "done",
        },
    ]


def test_turns_from_tokens_gives_a_re_rendered_turn_no_reply() -> None:
    prompt_1 = _ids("A<think>\n")
    completion_1 = _ids(
        "x</think><response><commands><keystrokes>ls\n</keystrokes></commands></response>"
    )
    turns = [
        SimpleNamespace(prompt_token_ids=prompt_1, completion_token_ids=completion_1),
        # A prompt that does not extend the previous prompt+completion: nothing
        # can be read as the reply to turn 1.
        SimpleNamespace(
            prompt_token_ids=_ids("B: fresh render"),
            completion_token_ids=_ids("y</think>nothing"),
        ),
        # An over-budget turn: captured with no completion at all.
        SimpleNamespace(prompt_token_ids=[], completion_token_ids=[]),
    ]
    lines = rollout_record.turns_from_tokens(turns, _decode)
    assert lines[0]["output"] == ""
    assert lines[1] == {"turn": 2, "raw": "nothing", "output": "", "think": "y"}
    assert lines[2] == {"turn": 3, "raw": "", "output": ""}


def test_write_record_and_read_record_round_trip(tmp_path) -> None:
    path = tmp_path / "rollouts" / "tw_1" / "g7-r0.jsonl"
    header = {
        "task": "tw_1",
        "rev": 0,
        "run": "tmax-9b--20260904-181500Z",
        "group": 7,
        "rollout": 0,
        "reward": 1.0,
        "exec": [{"t": 1.0, "secs": 0.4, "exit": 0, "cmd": "tmux send-keys"}],
    }
    turns = [{"turn": 1, "keystrokes": ["ls\n"], "output": "é"}]
    rollout_record.write_record(path, header, turns)
    assert rollout_record.read_record(path) == (header, turns)
    assert list(path.parent.iterdir()) == [path]
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") == 2
    assert "é" in text
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    with pytest.raises(ValueError):
        rollout_record.read_record(empty)


# --- the dataset reading a mix the way it is served ---


def _mix_row(i: int, *, rev: int = 0, disk: int = 4) -> str:
    return json.dumps(
        {
            "metadata": {
                "instance_id": f"tw_{i}",
                "image": "example/image",
                "tmax": {"test_sh": "true"},
                "rev": rev,
                "daytona_disk_gb": disk,
            }
        }
    )


def test_dataset_hot_reloads_a_republished_mix_and_records_its_versions(
    tmp_path, monkeypatch
) -> None:
    """A version is published by renaming a new hardlink over live.jsonl, so the
    inode moves and the name does not; the dataset watches the inode and reads
    the version off the mix directory, and the run's mix_versions.jsonl gets one
    line at boot and one per reload."""
    from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset

    root = layout.Root(tmp_path)
    mix = root.mix
    _, v1_path = mix.publish([_mix_row(1), _mix_row(2)], t=0)
    run = root.run("tmax-9b--20260904-181500Z")
    monkeypatch.setenv("TRL_RUN_DIR", str(run.path))
    monkeypatch.setenv("SWE_DATA_HOT_RELOAD", "1")
    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(mix.live), shuffle=False))

    first = next(dataset)
    assert (first.instance_id, first.rev, first.daytona_disk_gb) == ("tw_1", 0, 4)

    _, v2_path = mix.publish([_mix_row(1, rev=1, disk=8), _mix_row(3)], t=60)
    dataset._maybe_reload(min_interval_sec=0)
    (event,) = dataset.drain_lineage_events()
    assert event["event"] == "hot_reload"
    assert event["source"] == "live.jsonl"
    assert event["source_version"] == 2
    assert (event["replaced"], event["appended"], event["retired"]) == (1, 1, 1)
    assert {c["change"] for c in event["changes"]} == {
        "replaced",
        "appended",
        "retired",
    }
    # tw_2 left the rotation in place; tw_3 joined its tail; tw_1 is now rev 1.
    drawn = [next(dataset) for _ in range(2)]
    assert [(s.instance_id, s.rev) for s in drawn] == [("tw_3", 0), ("tw_1", 1)]
    assert drawn[1].daytona_disk_gb == 8

    lines = layout.read_jsonl(run.mix_versions)
    assert [
        (l["event"], l["version"], l["replaced"], l["appended"], l["retired"])
        for l in lines
    ] == [("boot", 1, 0, 0, 0), ("hot_reload", 2, 1, 1, 1)]
    assert lines[0]["sha256"] == layout.sha256_file(v1_path)
    assert lines[1]["sha256"] == layout.sha256_file(v2_path)
    assert all(_STAMP.match(l["stamp"]) for l in lines)


def test_dataset_records_mix_versions_only_for_the_train_split_of_a_mix_file(
    tmp_path, monkeypatch
) -> None:
    from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset

    root = layout.Root(tmp_path)
    root.mix.publish([_mix_row(1), _mix_row(2)], t=0)
    run = root.run("tmax-9b--20260904-181500Z")
    monkeypatch.setenv("TRL_RUN_DIR", str(run.path))
    # The holdout slice reads the same live file and is not the trained mix.
    validation = TMaxDataset(
        TMaxDataset.Config(
            data_path=str(root.mix.live), shuffle=False, holdout_n=1, split="validation"
        )
    )
    next(validation)
    # A file outside a mix directory (a benchmark, a smoke test) has no version.
    plain = tmp_path / "tb2.jsonl"
    plain.write_text(_mix_row(9) + "\n")
    other = TMaxDataset(TMaxDataset.Config(data_path=str(plain), shuffle=False))
    next(other)
    assert not run.mix_versions.exists()

    train = TMaxDataset(
        TMaxDataset.Config(
            data_path=str(root.mix.live), shuffle=False, holdout_n=1, split="train"
        )
    )
    # Built but never drawn from (every rollout worker does this): no line yet.
    assert not run.mix_versions.exists()
    next(train)
    (line,) = layout.read_jsonl(run.mix_versions)
    assert (line["event"], line["version"]) == ("boot", 1)


def test_dataset_skips_the_tasks_a_signals_directory_names(tmp_path) -> None:
    from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset

    path = tmp_path / "mix.jsonl"
    path.write_text("".join(_mix_row(i) + "\n" for i in range(4)))
    run = layout.Run(tmp_path / "runs" / "tmax-9b--x")
    layout.write_json_atomic(run.signal("tw_1", 5), {"task": "tw_1"})
    layout.write_json_atomic(run.signal("tw_3", 9), {"task": "tw_3"})
    (run.signals / "tw_2--g7.json.incoming").write_text("")
    dataset = TMaxDataset(
        TMaxDataset.Config(
            data_path=str(path), shuffle=False, skip_ids_path=str(run.signals)
        )
    )
    assert [next(dataset).instance_id for _ in range(4)] == [
        "tw_0",
        "tw_2",
        "tw_0",
        "tw_2",
    ]
