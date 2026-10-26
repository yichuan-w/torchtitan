# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""An easier rewrite whose grader fails what the parent's grader passed is
rejected (grader_replay.py, wired into evolve_ondella.handle)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
import grader_replay
import test_evolve_lineage as tl
from recorded_solution import ACTION_PATH, parse_actions
from torchtitan.experiments.rl.examples.tmax import rollout_record


def _grader(calls: list):
    """daytona_probe as a grader: a package whose tests/test.sh says "strict"
    passes only a solution that mentions "general"; any other passes all."""

    def probe(work, *, resources=None, pretest_file=None, **_):
        work = Path(work)
        strict = "strict" in (work / "tests" / "test.sh").read_text()
        solution = "".join(
            p.read_text() for p in sorted((work / "solution").rglob("*")) if p.is_file()
        )
        passed = not strict or "general" in solution
        calls.append({"strict": strict, "solution": solution, "passed": passed})
        return {"ok": passed, "stage": "daytona_oracle", "reward": float(passed)}

    return probe


def _final(root, *, group: int, idx: int = 0, reward: float = 1.0, rev: int = 0,
           keys: str = "echo narrow > /app/report.txt\n", validation: bool = False) -> Path:
    run = root.run(tl.RUN)
    path = (
        run.validation_rollouts / "tw_a" / f"g{group}-r{idx}.jsonl"
        if validation
        else run.rollout_record("tw_a", group, idx)
    )
    rollout_record.write_record(
        path,
        {"task": "tw_a", "rev": rev, "run": tl.RUN, "group": group, "rollout": idx,
         "reward": reward, "turns": 2},
        [
            {"turn": 1, "keystrokes": [keys], "output": ""},
            {"turn": 2, "keystrokes": [], "task_complete": True, "output": ""},
        ],
    )
    return path


def _easier(tmp_path, monkeypatch, *, test_sh: str, solve_sh: str | None = None):
    """A root at r0, an easier signal on it, process_one that accepts a rewrite
    writing ``test_sh`` (and ``solve_sh``), and the grading probe."""
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    monkeypatch.setattr(od, "EASIER_GRADER_REPLAY", True)
    tl._signal(root, direction="easier")
    tl._stub(monkeypatch)
    accept = od.fb.process_one

    def rewrite(rw, *args, **kwargs):
        rec = accept(rw, *args, **kwargs)
        (rw.package / "tests" / "test.sh").write_text(test_sh)
        if solve_sh is not None:
            (rw.package / "solution" / "solve.sh").write_text(solve_sh)
        return rec

    monkeypatch.setattr(od.fb, "process_one", rewrite)
    calls: list = []
    monkeypatch.setattr(od.fb, "daytona_probe", _grader(calls))
    return root, calls


def _handle(root):
    return od.handle(root, od._replay_signal(root, f"{tl.RUN}/tw_a--g7"), declared={})


def test_stricter_easier_rewrite_is_rejected(tmp_path, monkeypatch):
    root, calls = _easier(tmp_path, monkeypatch, test_sh="strict\necho 1\n")
    _final(root, group=3)
    # The readout is not an input, even a passing one.
    _final(root, group=-1, validation=True, keys="echo general\n")

    h = _handle(root)

    assert h["status"] == "rejected"
    assert h["meta"]["stage"] == "grader_stricter"
    replay = h["meta"]["grader_replay"]
    assert replay["ok"] is False and replay["finals_available"] == 1
    assert [r["verdict"] for r in replay["results"]] == ["stricter"]
    assert replay["results"][0]["name"] == f"{tl.RUN}/g3-r0.jsonl"
    # Failed under the rewrite, then passed in the parent it came from.
    assert [(c["strict"], c["passed"]) for c in calls] == [(True, False), (False, True)]
    assert "narrow" in calls[0]["solution"]
    assert not (h["rewrite"].path / "grader-replay").exists()


def test_easier_rewrite_that_keeps_the_grader_is_accepted(tmp_path, monkeypatch):
    root, calls = _easier(tmp_path, monkeypatch, test_sh="echo 1 > /app/reward.txt\n")
    _final(root, group=3)
    _final(root, group=4, reward=0.0)  # failed finals are not replayed

    h = _handle(root)

    assert h["status"] == "accepted"
    replay = h["meta"]["grader_replay"]
    assert replay["ok"] is True and replay["counts"] == {"pass": 1}
    assert replay["finals_available"] == 1 and len(calls) == 1


def test_without_passing_finals_the_reference_solutions_are_replayed(tmp_path, monkeypatch):
    # The rewrite swapped in its own reference, so the parent's (here r0's,
    # the original's) is replayed; the rewrite's own is the oracle's.
    root, calls = _easier(
        tmp_path, monkeypatch, test_sh="strict\necho 1\n", solve_sh="echo general\n"
    )

    h = _handle(root)

    replay = h["meta"]["grader_replay"]
    assert replay["finals_available"] == 0
    assert replay["references_replayed"] == ["parent_reference"]
    assert [r["verdict"] for r in replay["results"]] == ["stricter"]
    assert h["status"] == "rejected" and h["meta"]["stage"] == "grader_stricter"


def test_nothing_to_replay_accepts(tmp_path, monkeypatch):
    root, calls = _easier(tmp_path, monkeypatch, test_sh="strict\necho 1\n")

    h = _handle(root)

    assert h["status"] == "accepted"
    assert h["meta"]["grader_replay"]["note"] == "nothing to replay"
    assert calls == []


def test_off_by_default_in_the_loop(tmp_path, monkeypatch):
    root, calls = _easier(tmp_path, monkeypatch, test_sh="strict\necho 1\n")
    monkeypatch.setattr(od, "EASIER_GRADER_REPLAY", False)
    _final(root, group=3)

    h = _handle(root)

    assert h["status"] == "accepted" and "grader_replay" not in h["meta"]
    assert calls == []
    monkeypatch.delenv(grader_replay.ENV, raising=False)
    assert grader_replay.enabled() is False
    assert grader_replay.enabled(default="1") is True


def _pkg(base: Path, test_sh: str, solve_sh: str) -> Path:
    for rel, text in {"tests/test.sh": test_sh, "solution/solve.sh": solve_sh,
                      "instruction.md": "do it\n", "traces/attempt-01.jsonl": "{}\n"}.items():
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(text)
    return base


@pytest.mark.parametrize(
    "home_passes,infra,verdict",
    [(True, False, "stricter"), (False, False, "unreplayable"), (True, True, "error")],
)
def test_only_a_solution_its_home_still_passes_counts(tmp_path, home_passes, infra, verdict):
    parent = _pkg(tmp_path / "parent", "parent\n", "echo ref\n")
    rewrite = _pkg(tmp_path / "rewrite", "rewrite\n", "echo ref\n")

    def probe(work, **_):
        work = Path(work)
        assert not (work / "traces").exists()
        if (work / "tests/test.sh").read_text() == "parent\n":
            return {"ok": home_passes, "stage": "daytona_oracle"}
        if infra:
            return {"ok": False, "stage": "daytona_error", "why": "BadGateway"}
        return {"ok": False, "stage": "daytona_oracle", "reward": 0.0}

    final = _final_record(tmp_path)
    out = grader_replay.check(rewrite, parent=parent, original=None, finals=[final],
                              probe=probe, scratch=tmp_path / "scratch")
    assert [r["verdict"] for r in out["results"]] == [verdict]
    assert out["ok"] is (verdict != "stricter")
    assert not (tmp_path / "scratch").exists()


def _final_record(tmp_path: Path) -> Path:
    path = tmp_path / "runs" / tl.RUN / "rollouts" / "tw_a" / "g3-r0.jsonl"
    rollout_record.write_record(
        path,
        {"task": "tw_a", "rev": 0, "group": 3, "rollout": 0, "reward": 1.0},
        [
            {"turn": 1, "keystrokes": ["cd /app\n", "cat > f <<'EOF'\nx\nEOF\n"], "output": ""},
            {"turn": 2, "raw": "unparsed", "output": ""},
            {"turn": 3, "keystrokes": [], "task_complete": True, "output": ""},
        ],
    )
    return path


def test_a_record_becomes_a_valid_recorded_solution(tmp_path):
    actions = grader_replay.actions_from_record(_final_record(tmp_path))
    parse_actions(json.dumps(actions))  # the format daytona_revalidate replays
    assert [(a["kind"], a["step_id"], a["keystrokes"]) for a in actions] == [
        ("terminal_action", 1, "cd /app\n"),
        ("terminal_action", 1, "cat > f <<'EOF'\nx\nEOF\n"),
        ("completion_marker", 2, ""),
        ("completion_marker", 3, ""),
    ]
    staged = grader_replay.stage_package(
        _pkg(tmp_path / "pkg", "t\n", "echo ref\n"), tmp_path / "staged", actions=actions
    )
    assert sorted(p.name for p in (staged / "solution").iterdir()) == [Path(ACTION_PATH).name]


def test_a_vanillux_record_replays_its_bash_commands(tmp_path):
    """vanillux turns carry no keystrokes: each command that ran is typed as
    one eval of its base64; the call that ran nothing and the bare submit
    echo are not replayed."""
    import base64
    import re

    import test_evolve_codex_traces as tt

    path = tmp_path / "runs" / tl.RUN / "rollouts" / "tw_a" / "g3-r0.jsonl"
    rollout_record.write_record(path, tt.VANILLUX_HEADER, tt.VANILLUX_TURNS)
    actions = grader_replay.actions_from_record(path)
    parse_actions(json.dumps(actions))
    typed = [a for a in actions if a["kind"] == "terminal_action"]
    assert [a["step_id"] for a in typed] == [1, 2, 4]
    decoded = []
    for a in typed:
        m = re.fullmatch(r'eval "\$\(printf %s ([A-Za-z0-9+/=]+) \| base64 -d\)"\n', a["keystrokes"])
        assert m, a["keystrokes"]
        decoded.append(base64.b64decode(m.group(1)).decode())
    assert decoded == [tt.VANILLUX_COMMANDS[i] for i in (0, 1, 3)]
    assert [(a["kind"], a["step_id"]) for a in actions[len(typed):]] == [
        ("completion_marker", 5),
        ("completion_marker", 6),
    ]
