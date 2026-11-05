# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""EVOLVE_GRADER_VALUE_LEAK, and the blind verifier's package kept outside
the rewrite with its output checked for escapes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec
import evolve_ondella as od
import grader_value_leak as gvl
import test_blind_verifier as tb
import test_evolve_lineage as tl
from torchtitan.experiments.rl.examples.tmax import layout

BIG = "340282263924335107687481629450382003363"


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.delenv("EVOLVE_GRADER_VALUE_LEAK", raising=False)


def _pkg(d: Path, instruction: str, tests: dict[str, str]) -> Path:
    (d / "tests").mkdir(parents=True)
    (d / "instruction.md").write_text(instruction)
    for name, text in tests.items():
        (d / "tests" / name).write_text(text)
    return d


def test_the_task_001320_shape_is_flagged_in_a_rust_test(tmp_path):
    """rev1 tested fixed inputs; rev2 stated the input and its answer."""
    parent = _pkg(tmp_path / "r1", "Write a program that counts factors.\n",
                  {"main.rs": "assert_eq!(run(1002), 2);\n"})
    new = _pkg(tmp_path / "r2", f"For input {BIG} the program must print 3.\n",
               {"main.rs": f'assert_eq!(run("{BIG}"), 3);\n'})
    verdict = gvl.check(parent, new)
    assert verdict["ok"] is False and verdict["tokens"] == [BIG]
    assert BIG in verdict["why"]


def test_values_the_task_already_stated_or_tested_are_not_flagged(tmp_path):
    parent = _pkg(tmp_path / "a", f"Input {BIG} is in data.csv.\n", {"t.py": "x = 1\n"})
    new = _pkg(tmp_path / "b", f"Input {BIG} is in data.csv.\n", {"t.py": f"x = {BIG}\n"})
    assert gvl.check(parent, new) == {"ok": True}
    parent2 = _pkg(tmp_path / "c", "Count factors.\n", {"t.py": f"x = {BIG}\n"})
    new2 = _pkg(tmp_path / "d", f"Count factors; e.g. {BIG}.\n", {"t.py": f"x = {BIG}\n"})
    assert gvl.check(parent2, new2) == {"ok": True}


def test_names_and_commands_the_instruction_must_state_are_not_flagged(tmp_path):
    parent = _pkg(tmp_path / "a", "Build it.\n", {"test.sh": "true\n"})
    new = _pkg(
        tmp_path / "b",
        "Run `vm-audit ./simple_vm tests/*.asm` and write /app/report_summary.json.\n",
        {"test.sh": 'run "vm-audit ./simple_vm tests/*.asm"\ncat "/app/report_summary.json"\n'},
    )
    assert gvl.check(parent, new) == {"ok": True}


def test_an_expected_output_string_is_flagged(tmp_path):
    parent = _pkg(tmp_path / "a", "Print the sequence.\n", {"t.py": "pass\n"})
    new = _pkg(tmp_path / "b", "It must print `0, 1, 1, 2, 3, 5, 8, 13`.\n",
               {"t.py": 'assert out == "0, 1, 1, 2, 3, 5, 8, 13"\n'})
    assert gvl.check(parent, new)["tokens"] == ["0, 1, 1, 2, 3, 5, 8, 13"]


@pytest.mark.parametrize("value,expected", [("", ""), ("0", ""), ("record", "record"),
                                            ("reject", "reject")])
def test_mode(monkeypatch, value, expected):
    monkeypatch.setenv("EVOLVE_GRADER_VALUE_LEAK", value)
    assert gvl.mode() == expected


def test_mode_rejects_a_typo(monkeypatch):
    monkeypatch.setenv("EVOLVE_GRADER_VALUE_LEAK", "rejct")
    with pytest.raises(ValueError, match="EVOLVE_GRADER_VALUE_LEAK"):
        gvl.mode()


# -- the loop ----------------------------------------------------------------


@pytest.mark.parametrize("mode,status", [("", "accepted"), ("record", "accepted"),
                                         ("reject", "rejected")])
def test_loop_records_or_rejects(tmp_path, monkeypatch, mode, status):
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    if mode:
        monkeypatch.setenv("EVOLVE_GRADER_VALUE_LEAK", mode)
    tl._stub(monkeypatch)
    stub = od.fb.process_one

    def leaking(rewrite, signal, **kw):
        rec = stub(rewrite, signal, **kw)
        (rewrite.package / "instruction.md").write_text(f"For {BIG} print 3.\n")
        (rewrite.package / "tests").mkdir(exist_ok=True)
        (rewrite.package / "tests" / "test_state.py").write_text(f"assert f({BIG}) == 3\n")
        return rec

    monkeypatch.setattr(od.fb, "process_one", leaking)
    tl._signal(root, group=7, rev=0)
    h = od.handle(root, od._replay_signal(root, f"{tl.RUN}/tw_a--g7"), declared={})
    assert h["status"] == status
    meta = json.loads(h["rewrite"].meta.read_text())
    if not mode:
        assert "grader_value_leak" not in meta
    else:
        assert meta["grader_value_leak"]["tokens"] == [BIG]
    if mode == "reject":
        assert meta["stage"] == "grader_value_leak"


def _harder_prompt(tmp_path, monkeypatch) -> list[dict]:
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    try:
        ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    except Exception:  # noqa: BLE001 -- only the prompts are under test
        pass
    return sessions


def test_harder_prompt_note_only_under_the_flag(tmp_path, monkeypatch):
    off = _harder_prompt(tmp_path / "off", monkeypatch)
    assert gvl.PROMPT_NOTE.strip() not in off[0]["prompt"]
    monkeypatch.setenv("EVOLVE_GRADER_VALUE_LEAK", "record")
    on = _harder_prompt(tmp_path / "on", monkeypatch)
    assert gvl.PROMPT_NOTE.strip() in on[0]["prompt"]
    assert all(gvl.PROMPT_NOTE.strip() not in s["prompt"] for s in on if s["role"] == "verifier")


# -- the blind verifier ------------------------------------------------------


def _session(tmp_path) -> tuple[layout.RewriteDir, layout.SessionDir]:
    rw = layout.Root(tmp_path).evolution.task("tw_1").rewrite("harder")
    sd = rw.session("verifier")
    sd.path.mkdir(parents=True)
    sd.prompt.write_text("Never open ../package or solution/ (prompt text is not output).\n")
    return rw, sd


@pytest.mark.parametrize("line", ["cat ../../../package/solution/solve.sh",
                                  "ls {rw}/package/solution",
                                  "find /x/evolution/tasks/tw_2/rewrites"])
def test_require_blind_fails_on_an_escape(tmp_path, line):
    rw, sd = _session(tmp_path)
    sd.stdout.write_text("exec\n" + line.format(rw=rw.path) + "\n")
    with pytest.raises(ec.BlindEscape):
        ec._require_blind(sd, rw)


def test_require_blind_ignores_the_echoed_prompt_and_its_own_package(tmp_path):
    rw, sd = _session(tmp_path)
    sd.stderr.write_text(sd.prompt.read_text() + "cat /tmp/blind-package-x/tests/test.sh\n")
    ec._require_blind(sd, rw)


def test_outside_package_is_linked_and_copied_back(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_WORK_ROOT", str(tmp_path / "work"))
    rw, sd = _session(tmp_path / "root")
    real = ec._outside_package(sd)
    assert sd.package.is_symlink() and sd.package.resolve() == real
    assert rw.path.resolve() not in real.parents
    assert real.parent == (tmp_path / "work" / "blind").resolve()
    (sd.package / "tests").mkdir()
    (sd.package / "tests" / "test.sh").write_text("ok\n")
    ec.materialize_outside_packages(rw)
    assert not sd.package.is_symlink()
    assert (sd.package / "tests" / "test.sh").read_text() == "ok\n"
    assert not real.exists()
