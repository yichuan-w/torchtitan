# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""#306 items 1, 2 and 6: rewrite targets, rewriter context and the harder
slot share, each behind a flag that leaves the prompts and the scheduler as
they were when unset."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import simplify_operators as so

FLAGS = (
    "EVOLVE_EASIER_TARGET",
    "EVOLVE_REWRITE_CONTEXT",
    "EVOLVE_HARDER_TARGET",
    "EVOLVE_HARDER_SLOT_FRAC",
)


@pytest.fixture(autouse=True)
def _flags_unset(monkeypatch):
    for name in FLAGS:
        monkeypatch.delenv(name, raising=False)


# -- item 1: easier target ---------------------------------------------------


@pytest.mark.parametrize("hint", ["none", "vague", "specific"])
def test_easier_prompt_without_the_flag_has_no_target(hint) -> None:
    text = so.prompt(hint, 12)
    assert text == so.prompt(hint) == so.prompt(hint, 8)
    assert "Aim for" not in text
    assert "size\nthe change to what the attempts show.\n\nThe student works" in text


def test_easier_target_names_the_band_and_the_bias(monkeypatch) -> None:
    monkeypatch.setenv("EVOLVE_EASIER_TARGET", "6-7")
    text = so.prompt("vague", 12)
    assert "Aim for 6 to 7 of 12 solved in that next group" in text
    assert "predicted about 2\nmore solves in 12 than the next group produced" in text
    # The target sits in the goal, before the budget paragraph.
    assert text.index("Aim for 6 to 7") < text.index("The student works within")


@pytest.mark.parametrize(
    "spec,n,band",
    [("6-7", 12, (6, 7)), ("6-7", 8, (4, 5)), ("6-7", 16, (8, 9)), ("3-5", 12, (3, 5)),
     ("3-5", 4, (1, 2)), ("1-11", 2, (1, 1))],
)
def test_target_band_scales_to_the_group(monkeypatch, spec, n, band) -> None:
    monkeypatch.setenv("EVOLVE_EASIER_TARGET", spec)
    assert so.target_band("EVOLVE_EASIER_TARGET", n) == band


@pytest.mark.parametrize("spec", ["6", "7-6", "0-3", "6-12", "half"])
def test_target_band_rejects_a_malformed_band(monkeypatch, spec) -> None:
    monkeypatch.setenv("EVOLVE_EASIER_TARGET", spec)
    with pytest.raises(ValueError, match="EVOLVE_EASIER_TARGET"):
        so.prompt("vague", 12)


# -- item 2: rewriter context ------------------------------------------------

import json  # noqa: E402

import evolve_codex as ec  # noqa: E402
import evolve_ondella as od  # noqa: E402
import test_blind_verifier as tb  # noqa: E402
import test_evolve_lineage as tl  # noqa: E402
from torchtitan.experiments.rl.examples.tmax import rollout_record  # noqa: E402


def _group(root, group: int, rev: int, rewards) -> None:
    r = root.run(tl.RUN)
    for i, reward in enumerate(rewards):
        rollout_record.write_record(
            r.rollout_record("tw_a", group, i),
            {"task": "tw_a", "rev": rev, "run": tl.RUN, "group": group, "rollout": i,
             "reward": reward},
            [{"turn": 1, "keystrokes": ["ls\n"], "output": ""}],
        )


def _capture_context(monkeypatch) -> list:
    """What each process_one call found under traces/: context.json and the
    last-mixed instruction, or None."""
    seen = []
    stub = od.fb.process_one

    def wrapped(rewrite, signal, **kw):
        ctx = rewrite.traces / "context.json"
        mixed = rewrite.traces / "last-mixed" / "instruction.md"
        seen.append({
            "context": json.loads(ctx.read_text()) if ctx.exists() else None,
            "last_mixed": mixed.read_text() if mixed.exists() else None,
        })
        return stub(rewrite, signal, **kw)

    monkeypatch.setattr(od.fb, "process_one", wrapped)
    return seen


def _handle(root, group: int, rev: int, *, fold: bool = True) -> dict:
    tl._signal(root, group=group, rev=rev)
    h = od.handle(root, od._replay_signal(root, f"{tl.RUN}/tw_a--g{group}"), declared={})
    assert h["status"] == "accepted"
    if fold:
        od.fold(root, [h])
    return h


@pytest.mark.parametrize("flag", [False, True])
def test_context_names_the_last_mixed_revision_and_the_prediction(tmp_path, monkeypatch, flag):
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    if flag:
        monkeypatch.setenv("EVOLVE_REWRITE_CONTEXT", "1")
    tl._stub(monkeypatch, simplify={"expected": "1 of 2"})
    seen = _capture_context(monkeypatch)
    _group(root, 6, 0, [1.0, 0.0])  # r0 came out mixed once
    _handle(root, 7, 0)  # r1, predicted 1 of 2
    _handle(root, 8, 1, fold=False)  # r1's first group: 2 of 2
    task = root.evolution.task("tw_a")
    if not flag:
        assert seen == [{"context": None, "last_mixed": None}] * 2
        return
    # r0 is the input of the first rewrite: it is the current version there.
    first, second = seen
    assert first == {"context": None, "last_mixed": None}
    assert second["last_mixed"] == (task.rev(0) / "instruction.md").read_text()
    assert second["context"] == {
        "last_mixed": {"rev": 0, "run": tl.RUN, "group": 6, "solved": 1, "total": 2,
                       "instruction": "traces/last-mixed/instruction.md"},
        "prediction": {"rev": 1, "expected": "1 of 2", "run": tl.RUN, "group": 8,
                       "solved": 2, "total": 2},
    }


def test_context_without_a_mixed_group_or_a_prediction_is_not_written(tmp_path, monkeypatch):
    root = tl._root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_POLICY_CANARY_K", "0")
    monkeypatch.setenv("EVOLVE_REWRITE_CONTEXT", "1")
    tl._stub(monkeypatch)  # no simplify.json, no hardening.md: no prediction
    seen = _capture_context(monkeypatch)
    _handle(root, 7, 0)
    _handle(root, 8, 1, fold=False)
    assert seen == [{"context": None, "last_mixed": None}] * 2


def test_prediction_reads_a_harder_expected_line_and_skips_free_text(tmp_path) -> None:
    from torchtitan.experiments.rl.examples.tmax import layout

    task = layout.Root(tmp_path).evolution.task("tw_a")
    rw = task.rewrite("harder", "20261031-000000Z")
    rw.notes.mkdir(parents=True)
    rw.meta.write_text(json.dumps({"status": "accepted", "result_rev": 3}))
    (rw.notes / "hardening.md").write_text("I expect 5 of 8 to pass.\n")
    assert od._prediction_for(task, 3) is None
    (rw.notes / "hardening.md").write_text('# Plan\n\nexpected: "4 of 12"\n')
    assert od._prediction_for(task, 3) == (4, 12)
    assert od._prediction_for(task, 2) is None


def _author_prompt(tmp_path, monkeypatch, job, context=None) -> str:
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    monkeypatch.setattr(ec.so, "read_decision", lambda *_: {"operator": "x"})
    rw.traces.mkdir(parents=True, exist_ok=True)
    if context is not None:
        (rw.traces / "context.json").write_text(json.dumps(context))
    try:
        ec.evolve_agentic(rw, dict(tb.SEED), job)
    except Exception:  # noqa: BLE001 -- only the author's prompt is under test
        pass
    return sessions[0]["prompt"]


CONTEXT = {
    "last_mixed": {"rev": 2, "run": "r", "group": 9, "solved": 5, "total": 12,
                   "instruction": "traces/last-mixed/instruction.md"},
    "prediction": {"rev": 4, "expected": "6 of 12", "run": "r", "group": 11,
                   "solved": 1, "total": 12},
}


@pytest.mark.parametrize("job", ["harder", "easier"])
def test_author_prompt_opens_with_the_prediction_and_names_the_last_mixed(
    tmp_path, monkeypatch, job
):
    prompt = _author_prompt(tmp_path, monkeypatch, job, CONTEXT)
    assert prompt.startswith(
        "The version you are rewriting (r4) was predicted to solve 6 of 12, and its "
        "first training group solved 1 of 12.\n\nThis task was solved"
    )
    assert "THE LAST MIXED VERSION\nr2 is the latest version" in prompt
    assert "5 of 12 solved (run r, group 9)" in prompt


def test_author_prompt_takes_what_the_context_holds(tmp_path, monkeypatch):
    only_mixed = {"last_mixed": CONTEXT["last_mixed"]}
    prompt = _author_prompt(tmp_path / "a", monkeypatch, "harder", only_mixed)
    assert prompt.startswith("This task was solved")
    assert "THE LAST MIXED VERSION" in prompt
    bare = _author_prompt(tmp_path / "b", monkeypatch, "harder")
    assert "THE LAST MIXED VERSION" not in bare and "was predicted" not in bare


def test_rewrite_context_tolerates_a_missing_or_broken_file(tmp_path) -> None:
    assert ec._rewrite_context(tmp_path) == ("", "")
    (tmp_path / "context.json").write_text("{not json")
    assert ec._rewrite_context(tmp_path) == ("", "")


# -- item 6a: harder target and a scorable prediction ------------------------


@pytest.mark.parametrize(
    "text,expected",
    [('expected: "4 of 12"', "4 of 12"), ('- `expected: "4 of 12"`', "4 of 12"),
     ("## Prediction\n\nExpected: 3 of 12\n", "3 of 12"), ("expected:  5  of  8", "5 of 8"),
     ("I expect 4 of 12 to pass.", None), ('expected: "about half"', None),
     ('expected: "13 of 12"', None), ('the expected: "4 of 12" line', None), ("", None)],
)
def test_harder_expected_takes_only_the_line(text, expected) -> None:
    assert so.harder_expected(text) == expected


def _harder_traces(rw, n=12) -> None:
    rw.traces.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (rw.traces / f"attempt-{i:02d}.jsonl").write_text('{"reward": 1.0}\n')


def _hardening_author(monkeypatch, first: str, repaired: str | None, edit_task=False):
    """The harder author writes ``first`` as run/hardening.md; resumed, it
    writes ``repaired`` (and, if asked, edits the task). Returns the prompts."""
    real = ec._run_codex
    seen = []

    def run_codex(run, cwd, prompt, resume=None):
        p = real(run, cwd, prompt, resume)
        if (cwd / "solution").exists():
            seen.append(prompt)
            text = repaired if resume else first
            if text is not None:
                (cwd / "run/hardening.md").write_text(text)
            if resume and edit_task:
                (cwd / "instruction.md").write_text("something else\n")
        return p

    monkeypatch.setattr(ec, "_run_codex", run_codex)
    monkeypatch.setattr(ec, "_session_id", lambda _sd: "sid")
    return seen


@pytest.mark.parametrize("blind", [True, False])
def test_harder_prompt_without_the_flag_keeps_the_half_aim(tmp_path, monkeypatch, blind):
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    if not blind:
        monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")
    _harder_traces(rw)
    seen = _hardening_author(monkeypatch, "I expect 5 of 12.\n", None)
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert ec._HALF_AIM in seen[0] and 'expected: "k of' not in seen[0]
    assert len(seen) == 1 and "_harder_expected" not in out


@pytest.mark.parametrize("blind", [True, False])
def test_harder_target_replaces_the_half_aim(tmp_path, monkeypatch, blind):
    monkeypatch.setenv("EVOLVE_HARDER_TARGET", "3-5")
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    if not blind:
        monkeypatch.setattr(ec, "VERIFIER_AUTHOR", "same")
    _harder_traces(rw)
    seen = _hardening_author(monkeypatch, 'Plan.\n\nexpected: "4 of 12"\n', None)
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert ec._HALF_AIM not in seen[0]
    assert "Aim for a task the next group of 12 attempts solves 3 to 5 times." in seen[0]
    assert '`expected: "k of 12"`' in seen[0]
    assert len(seen) == 1  # the line was there: no repair session
    assert out["_harder_expected"] == "4 of 12"


def test_harder_target_scales_to_the_group(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_TARGET", "3-5")
    rw = tb._rewrite(tmp_path, monkeypatch)
    tb._wire(monkeypatch, [], [])
    _harder_traces(rw, 8)
    seen = _hardening_author(monkeypatch, 'expected: "3 of 8"\n', None)
    ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert "next group of 8 attempts solves 2 to 3 times" in seen[0]


def test_a_free_text_prediction_goes_back_to_the_author(tmp_path, monkeypatch):
    monkeypatch.setenv("EVOLVE_HARDER_TARGET", "3-5")
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    _harder_traces(rw)
    seen = _hardening_author(
        monkeypatch, "I expect 4 of 12.\n", 'I expect 4 of 12.\nexpected: "4 of 12"\n'
    )
    out = ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert [s["role"] for s in sessions] == ["author", "author", "verifier"]
    assert sessions[1]["resume"] == "sid"
    assert seen[1].startswith("The caller cannot read your prediction")
    assert out["_harder_expected"] == "4 of 12"


@pytest.mark.parametrize("edit_task", [False, True])
def test_a_prediction_still_missing_or_a_repair_that_edits_discards(
    tmp_path, monkeypatch, edit_task
):
    monkeypatch.setenv("EVOLVE_HARDER_TARGET", "3-5")
    rw = tb._rewrite(tmp_path, monkeypatch)
    sessions = []
    tb._wire(monkeypatch, sessions, [])
    _harder_traces(rw)
    _hardening_author(
        monkeypatch, "about half\n",
        'expected: "4 of 12"\n' if edit_task else "still about half\n", edit_task,
    )
    error, match = (
        (RuntimeError, "harder expected repair changed task files")
        if edit_task
        else (ValueError, 'requires a line expected: "k of n"')
    )
    with pytest.raises(error, match=match):
        ec.evolve_agentic(rw, dict(tb.SEED), "harder")
    assert [s["role"] for s in sessions] == ["author", "author"]


def test_a_recorded_harder_expected_is_the_prediction(tmp_path) -> None:
    from torchtitan.experiments.rl.examples.tmax import layout

    task = layout.Root(tmp_path).evolution.task("tw_a")
    rw = task.rewrite("harder", "20261031-000000Z")
    rw.path.mkdir(parents=True)
    rw.meta.write_text(
        json.dumps({"status": "accepted", "result_rev": 2, "harder_expected": "5 of 12"})
    )
    assert od._prediction_for(task, 2) == (5, 12)


# -- item 6b: harder share of the rewrite slots ------------------------------


def _sig(name: str) -> od.Signal:
    direction = "harder" if name.startswith("h") else "easier"
    return od.Signal(None, Path(name), name, {"direction": direction})


def _names(signals) -> list[str]:
    return [s.sid for s in signals]


BATCH = [_sig(n) for n in ("e1", "e2", "h1", "e3", "h2", "h3")]  # newest first


def test_slot_order_without_the_flag_is_newest_first(monkeypatch) -> None:
    assert od.harder_slot_frac() is None
    for running in (0, 5):
        got = od.slot_order(BATCH, 4, running_harder=running, workers=10, frac=None)
        assert _names(got) == ["e1", "e2", "h1", "e3"]
    assert od.slot_order(BATCH, 0, running_harder=0, workers=10, frac=None) == []


def test_slot_order_prefers_harder_up_to_the_share() -> None:
    # ceil(0.6 * 10) = 6 harder wanted, none running: all three harder first.
    got = od.slot_order(BATCH, 4, running_harder=0, workers=10, frac=0.6)
    assert _names(got) == ["h1", "h2", "h3", "e1"]
    # Five harder already running: one more harder, then newest first.
    got = od.slot_order(BATCH, 4, running_harder=5, workers=10, frac=0.6)
    assert _names(got) == ["h1", "e1", "e2", "e3"]
    # At the share: newest first, as without the flag.
    got = od.slot_order(BATCH, 4, running_harder=6, workers=10, frac=0.6)
    assert _names(got) == ["e1", "e2", "h1", "e3"]


def test_slot_order_never_leaves_a_slot_idle_for_a_missing_direction() -> None:
    easier = [s for s in BATCH if s.sid.startswith("e")]
    harder = [s for s in BATCH if s.sid.startswith("h")]
    assert _names(od.slot_order(easier, 5, running_harder=0, workers=10, frac=1.0)) == [
        "e1", "e2", "e3"]
    assert _names(od.slot_order(harder, 5, running_harder=9, workers=10, frac=0.2)) == [
        "h1", "h2", "h3"]
    assert _names(od.slot_order(BATCH, 6, running_harder=0, workers=10, frac=0.0)) == _names(BATCH)


def test_slot_order_share_is_not_rounded_up_by_float_error() -> None:
    batch = [_sig(n) for n in ("e1", "e2", "e3")] + [_sig(f"h{i}") for i in range(10)]
    got = _names(od.slot_order(batch, 10, running_harder=0, workers=10, frac=0.7))
    # 0.7 * 10 wants 7 harder, not 8; the three slots left go newest first.
    assert got == [f"h{i}" for i in range(7)] + ["e1", "e2", "e3"]


@pytest.mark.parametrize("value,frac", [("", None), ("0.6", 0.6), ("1", 1.0), ("0", 0.0)])
def test_harder_slot_frac_reads_the_env(monkeypatch, value, frac) -> None:
    monkeypatch.setenv("EVOLVE_HARDER_SLOT_FRAC", value)
    assert od.harder_slot_frac() == frac


@pytest.mark.parametrize("value", ["1.5", "-0.1"])
def test_harder_slot_frac_rejects_out_of_range(monkeypatch, value) -> None:
    monkeypatch.setenv("EVOLVE_HARDER_SLOT_FRAC", value)
    with pytest.raises(ValueError, match="EVOLVE_HARDER_SLOT_FRAC"):
        od.harder_slot_frac()
