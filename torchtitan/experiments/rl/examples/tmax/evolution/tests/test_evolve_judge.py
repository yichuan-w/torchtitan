"""evolve_judge.py on synthetic roots and evals: bundling, attempt lookup,
verdict validation and calibration scoring. No model call."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_judge as ej  # noqa: E402


def _rollout(path: Path, *, task: str, rev: int, reward: float, finish: str = "submit", run: str = "run-a",
             infra: bool = False, turns: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"task": task, "rev": rev, "run": run, "reward": reward, "finish_reason": finish,
              "infra_failed": infra, "turns": turns, "secs": 10.0,
              "verifier": {"output_tail": "1 failed" if reward < 1 else "1 passed"}}
    lines = [header] + [{"turn": i + 1, "keystrokes": [f"echo step{i}\n"], "output": "x" * 5000,
                         "analysis": "thinking", "plan": "do it"} for i in range(turns)]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


def _package(d: Path, instruction: str, test: str) -> None:
    (d / "tests").mkdir(parents=True)
    (d / "environment").mkdir()
    (d / "solution").mkdir()
    (d / "instruction.md").write_text(instruction)
    (d / "tests" / "test_outputs.py").write_text(test)
    (d / "environment" / "Dockerfile").write_text("FROM scratch\n")
    (d / "solution" / "solve.sh").write_text("true\n")


def _root(tmp: Path, task: str = "t1") -> Path:
    root = tmp / "root"
    td = root / "evolution" / "tasks" / task
    _package(td / "r0", "Do A.\n", "assert a\n")
    _package(td / "r1", "Do A. Also B.\n", "assert a\nassert b\n")
    rw = td / "rewrites" / "20261105-000000Z--harder"
    (rw / "notes").mkdir(parents=True)
    (rw / "notes" / "hardening.md").write_text(f"New judgement B. Run under {root}.\n")
    (rw / "rewrite.json").write_text(json.dumps({
        "status": "accepted", "job": "harder", "input_rev": 0, "result_rev": 1, "task": task,
        "signal": f"run-a/{task}--g-1"}))
    for r in range(3):
        _rollout(root / "runs" / "run-a" / "rollouts" / task / f"g-1-r{r}.jsonl", task=task, rev=0, reward=1.0)
    return root


def test_signal_and_run_attempts(tmp_path: Path) -> None:
    root = _root(tmp_path)
    rw = json.loads((root / "evolution/tasks/t1/rewrites/20261105-000000Z--harder/rewrite.json").read_text())
    assert [p.name for p in ej.signal_attempts(root, rw)] == ["g-1-r0.jsonl", "g-1-r1.jsonl", "g-1-r2.jsonl"]
    roll = root / "runs" / "{}" / "rollouts" / "t1"
    # group 7 on rev 1 is rerun after a restart: the later run's records win
    _rollout(Path(str(roll).format("run-a")) / "g7-r0.jsonl", task="t1", rev=1, reward=0.0, run="run-a")
    _rollout(Path(str(roll).format("run-b")) / "g7-r0.jsonl", task="t1", rev=1, reward=1.0, run="run-b")
    _rollout(Path(str(roll).format("run-b")) / "g9-r0.jsonl", task="t1", rev=1, reward=0.0, run="run-b")
    _rollout(Path(str(roll).format("run-b")) / "g8-r0.jsonl", task="t1", rev=2, reward=0.0, run="run-b")
    one = ej.run_attempts(root, "t1", 1, groups=1)
    assert len(one) == 1 and "run-b" in str(one[0]) and ej._read_header(one[0])["reward"] == 1.0
    assert [ej._group_of(p) for p in ej.run_attempts(root, "t1", 1, groups=5)] == [7, 9]


def test_eval_attempts_prefers_the_fuller_restart(tmp_path: Path) -> None:
    ev = tmp_path / "ev"
    for r in range(2):
        _rollout(ev / "validation_rollouts" / "A4__t1" / f"g-99-r{r}.jsonl", task="A4__t1", rev=1, reward=0.0,
                 infra=True)
    later = tmp_path / "ev--attempt-2"
    for r in range(3):
        _rollout(later / "validation_rollouts" / "A4__t1" / f"g-99-r{r}.jsonl", task="A4__t1", rev=1, reward=1.0)
    got = ej.eval_attempts([ev], "A4__t1")
    assert len(got) == 3 and all("attempt-2" in str(p) for p in got)
    assert ej.eval_attempts([ev], "missing") == []


def test_render_attempt_clips_output(tmp_path: Path) -> None:
    p = _rollout(tmp_path / "a.jsonl", task="t", rev=0, reward=0.0, finish="hit_time_budget")
    text = ej.render_attempt(p)
    assert "FAIL" in text and "hit_time_budget" in text and "echo step0" in text and "chars cut" in text
    assert "1 failed" in text


def test_bundle_is_blind_and_complete(tmp_path: Path) -> None:
    root = _root(tmp_path)
    ev = tmp_path / "ev"
    for r, reward in enumerate((1.0, 0.0, 0.0)):
        _rollout(ev / "validation_rollouts" / "t1" / f"g-99-r{r}.jsonl", task="t1", rev=1, reward=reward)
    _rollout(ev / "validation_rollouts" / "t1" / "g-99-r3.jsonl", task="t1", rev=1, reward=0.0, infra=True)
    items = ej.items_from_root(root, evals=[ev], label="{task}", groups=1, tasks=None, job=None)
    assert len(items) == 1
    out = tmp_path / "out"
    path = ej.run_judge(items, out=out, model="m", concurrency=1, dry_run=True)
    b = out / "bundles" / items[0]["id"]
    assert (b / "direction.txt").read_text() == "harder\n"
    assert "+Do A. Also B." in (b / "diff_instruction.txt").read_text()
    assert "+assert b" in (b / "diff_tests.txt").read_text()
    assert str(root) not in (b / "author" / "hardening.md").read_text()
    assert sorted(x.name for x in (b / "attempts_rewrite").iterdir()) == ["a01.md", "a02.md", "a03.md", "index.md"]
    assert "1 of 3 graded attempts passed; 1 infrastructure" in (b / "attempts_rewrite" / "index.md").read_text()
    row = json.loads(path.read_text())
    assert row["after"] == {"n": 3, "solved": 1, "infra": 1, "finish_failed": {"submit": 2}}
    assert row["parent"]["solved"] == 3 and row["verdict"] is None


def _verdict(**over) -> dict:
    v = {"worth": False, "category": "leak", "reason": "r", "help_kind": None,
         "evidence": [{"source": "diff_instruction.txt", "quote": "q"}]}
    v.update(over)
    return v


def test_validate() -> None:
    assert ej.validate(_verdict(), "harder") == []
    assert ej.validate(_verdict(worth=True), "harder")  # worth only with meaningful
    assert ej.validate(_verdict(category="meaningful"), "harder")
    assert ej.validate(_verdict(category="nope"), "harder")
    assert ej.validate(_verdict(), "easier")  # easier needs a help kind
    assert ej.validate(_verdict(help_kind="gives_method"), "easier") == []
    assert ej.validate(_verdict(evidence=[]), "harder")


def test_parse_reply_reads_a_fenced_result() -> None:
    env = {"result": "```json\n" + json.dumps(_verdict()) + "\n```", "total_cost_usd": 0.1}
    verdict, envelope = ej.parse_reply(json.dumps(env))
    assert verdict["category"] == "leak" and envelope["total_cost_usd"] == 0.1


def test_score_counts_misses() -> None:
    def row(name, expect_worth, cat, worth, got):
        return {"name": name, "expect": {"worth": expect_worth, "category": cat, "group": "g"},
                "verdict": {"worth": worth, "category": got}}
    rows = [row("a", True, "meaningful", True, "meaningful"), row("b", False, "leak", True, "meaningful"),
            row("c", False, None, False, "no_new_judgement"), row("d", False, "leak", False, "bypass"),
            {"name": "e", "expect": {"worth": False, "category": None}, "verdict": None}]
    s = ej.score(rows)
    assert s["agreement"] == {"agree": 3, "n": 4, "rate": 0.75}
    assert s["miss"]["judged_worth"] == 1 and s["miss"]["n"] == 3 and s["miss"]["items"] == ["b"]
    assert s["category"]["agree"] == 1 and s["category"]["n"] == 3
    assert s["items"] == 5 and s["judged"] == 4


def test_spec_aliases(tmp_path: Path) -> None:
    root = _root(tmp_path)
    spec = {"items": [{"name": "x", "group": "g", "root": "R", "task": "t1", "rewrite": "20261105-000000Z--harder",
                       "after": {"groups": 1}, "expect": {"worth": False, "category": "leak"}}]}
    items = ej.items_from_spec(spec, {"R": root})
    assert items[0]["expect"] == {"worth": False, "category": "leak", "group": "g"}
    assert items[0]["rewrite_attempts"] == []


def test_calibration_spec_is_well_formed() -> None:
    spec = json.loads((Path(ej.__file__).parent / "judge_calibration.json").read_text())
    names = [s["name"] for s in spec["items"]]
    assert len(names) == len(set(names)) == 32
    groups = {}
    for s in spec["items"]:
        groups[s["group"]] = groups.get(s["group"], 0) + 1
        assert s["expect"]["category"] in (None, *ej.CATEGORIES)
        assert s["expect"]["worth"] == (s["expect"]["category"] == "meaningful")
    assert groups == {"worth": 2, "leak": 2, "check_dropped": 1, "a4_time_limit": 3, "still_12of12": 24}
