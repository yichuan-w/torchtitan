# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The loop over one experiment root: discovery through the ledger, r0 from
the source corpus, one rewrite directory per handled signal, acceptance as a
renamed revision and a new mix version, and status.json rebuilt from files."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_ondella as od
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

SEED = {
    "instruction.md": "Write the report to /app/report.txt.\n",
    "environment/Dockerfile": "FROM scratch\nWORKDIR /app\n",
    "solution/solve.sh": "#!/bin/sh\n",
    "tests/test.sh": "echo 1 > /app/reward.txt\n",
}
RUN = "tmax-9b--20260904-181500Z"
VERDICTS = {"oracle": "pass", "dark_paths": [], "dark_literals": [], "step": []}


def _root(tmp_path, monkeypatch, tmax: dict | None = None) -> layout.Root:
    """A root with one seed task in the tw-extract corpus and a mix at v1.
    `tmax` is the row's grading payload, for a row that carries a pin hook."""
    base = tmp_path / "root"
    monkeypatch.setenv("TRL_BASE", str(base))
    monkeypatch.setattr(od, "SIMPLIFY_ENABLED", True)
    monkeypatch.setattr(od, "FLEET", {"cpu": None, "mem_gb": None, "disk_gb": None})
    root = layout.Root(base)
    seed = root.data / "sources" / "tw-extract" / "tasks" / "tw_a"
    for rel, text in SEED.items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(text)
    (seed / "instruction.md.bak-1").write_text("pre-canary text\n")
    row = {
        "prompt": SEED["instruction.md"],
        "label": "tw_a",
        "metadata": {
            "instance_id": "tw_a",
            "rev": 0,
            "daytona_cpu": 1,
            "daytona_mem_gb": 2,
            "daytona_disk_gb": 2,
            **({"tmax": tmax} if tmax else {}),
        },
    }
    root.mix.publish([json.dumps(row)])
    return root


def _signal(
    root: layout.Root,
    *,
    run: str = RUN,
    task: str = "tw_a",
    group: int = 7,
    rev: int = 0,
    direction: str = "harder",
    created: str = "20260904-183012Z",
    n: int = 2,
) -> str:
    """A run with `n` rollout records and the signal that names them; returns
    the signal id."""
    r = root.run(run)
    attempts = []
    for i in range(n):
        p = r.rollout_record(task, group, i)
        rollout_record.write_record(
            p,
            {
                "task": task,
                "rev": rev,
                "run": run,
                "group": group,
                "rollout": i,
                "reward": 1.0 if direction == "harder" else 0.0,
                "turns": 1,
            },
            [{"turn": 1, "keystrokes": ["ls /app\n"], "output": ""}],
        )
        attempts.append(str(p.relative_to(r.path)))
    solved = n if direction == "harder" else 0
    layout.write_json_atomic(
        r.signal(task, group),
        {
            "task": task,
            "rev": rev,
            "run": run,
            "group": group,
            "direction": direction,
            "solved": solved,
            "total": n,
            "created": created,
            "attempts": attempts,
        },
    )
    return layout.signal_id(run, task, group)


def test_prebuilt_seed_uses_digest_without_changing_original(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    row = json.loads(root.mix.live.read_text())
    image = "ghcr.io/example/tasks@sha256:" + "a" * 64
    row["metadata"].update(
        image=image, prebuilt_provenance={"verification_sha256": "proof"}
    )
    root.mix.publish([json.dumps(row)])
    task = root.evolution.task("tw_a")
    task.path.mkdir(parents=True, exist_ok=True)
    result = od.materialize_r0(root, task, "tw_a")
    assert (result / "environment/Dockerfile").read_text() == f"FROM {image}\n"
    original = root.data / "sources/tw-extract/tasks/tw_a/environment/Dockerfile"
    assert original.read_text() == SEED["environment/Dockerfile"]
    assert (
        json.loads((result / ".prebuilt-source.json").read_text())["source_dockerfile"]
        == original.read_text()
    )


def test_measured_feedback_survives_the_fold(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    feedback = {"solved": 16, "scored": 16, "action": "harder"}
    seen = _stub(monkeypatch, student_feedback=feedback)
    result = od.run_round(root, workers=1)
    assert result["accepted"] == 1
    saved = json.loads(seen[0]["rewrite"].meta.read_text())
    assert saved["student_feedback"] == feedback
    assert "student_feedback" not in root.mix.live.read_text()


def test_training_signal_supplies_measured_feedback_and_parent_revision(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    _signal(root)
    seen = _stub(monkeypatch)
    od.run_round(root, workers=1)
    feedback = seen[0]["signal"]["student_feedback"]
    assert feedback["measurement"] == {
        "run": RUN,
        "group": 7,
        "rev": 0,
        "solved": 2,
        "total": 2,
    }
    assert "previous_revision" not in feedback
    _signal(root, group=8, rev=1)
    od.run_round(root, workers=1)
    feedback = seen[1]["signal"]["student_feedback"]
    assert feedback["measurement"]["rev"] == 1
    assert feedback["previous_revision"] == {
        "rev": 0,
        "instruction": SEED["instruction.md"],
    }
    original = json.loads(root.run(RUN).signal("tw_a", 8).read_text())
    assert "student_feedback" not in original
    assert "student_feedback" not in root.mix.live.read_text()


def test_each_new_signal_preserves_its_feedback_and_starts_a_rewrite(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    seen = _stub(
        monkeypatch, status="kept", harder_mode="student", require_solution_growth=False
    )
    for group, solved in ((7, 16), (8, 13), (9, 13)):
        _signal(root, group=group)
        path = root.run(RUN).signal("tw_a", group)
        signal = json.loads(path.read_text())
        signal["student_feedback"] = {"independent_measurement": {"solved": solved}}
        layout.write_json_atomic(path, signal)
        result = od.run_round(root, workers=1)
        assert result["handled"] == 1
    assert len(seen) == 3
    assert seen[1]["signal"]["student_feedback"] == {
        "independent_measurement": {"solved": 13}
    }


class _Seen(list):
    """The process_one calls, in order; `.rows` is what the fold asked the row
    builder for."""

    rows: list[dict]


def _stub(monkeypatch, status: str = "accepted", **extra) -> _Seen:
    """process_one as the loop sees it: edits the package the way an agent
    would (plus the harness files), returns the record."""
    seen = _Seen()
    seen.rows = []

    def fake(rewrite, signal, *, job, seed_dir, resources=None):
        seen.append(
            {
                "rewrite": rewrite,
                "signal": signal,
                "job": job,
                "seed_dir": seed_dir,
                "resources": resources,
                "previous_simplify": (
                    json.loads((rewrite.traces / "previous-simplify.json").read_text())
                    if (rewrite.traces / "previous-simplify.json").exists()
                    else None
                ),
                "previous_parent": {
                    str(
                        path.relative_to(rewrite.traces / "previous-simplify-parent")
                    ): path.read_bytes()
                    for path in (rewrite.traces / "previous-simplify-parent").rglob("*")
                    if path.is_file()
                },
            }
        )
        (rewrite.package / "instruction.md").write_text("harder\n")
        (rewrite.package / "AGENTS.md").write_text("role")
        (rewrite.package / "run").mkdir(exist_ok=True)
        (rewrite.package / "run" / "checks.jsonl").write_text('{"verdict": "pass"}\n')
        rec = {
            "status": status,
            "stage": "daytona_oracle",
            "verdicts": VERDICTS,
            "resources": {
                "cpu": 2,
                "mem_gb": 4,
                "disk_gb": 2,
                "source": "measured:loop_probe",
                "measured": {"mem_peak_mb": 3000},
                "floor": {},
            },
        }
        rec.update(extra)
        return rec

    monkeypatch.setattr(od.fb, "process_one", fake)

    def to_row(
        d, *, task_id=None, inject_agent_runtime=True, pretest=None, protected=None
    ):
        # As pack.to_row: the identity is the caller's, since the directory
        # is `package` here and `r<N>` once renamed; the hook is the caller's
        # too, and lands on the row's grading payload; so are the protected
        # lists (the real to_row lets tests/protected_paths.json override them).
        tid = task_id or Path(d).name
        text = (Path(d) / "instruction.md").read_text()
        seen.rows.append({"dir": d, "pretest": pretest, "protected": protected})
        tmax = {"test_sh": "echo 1\n"}
        if pretest and pretest[0]:
            tmax.update({"pre_test_sh": pretest[0], "pretest_env_identity": pretest[1]})
        if protected is not None:
            if protected.paths:
                tmax["protected_paths"] = list(protected.paths)
            if protected.cmds:
                tmax["protected_cmds"] = list(protected.cmds)
        return {
            "prompt": text,
            "label": tid,
            "metadata": {"instance_id": tid, "problem_statement": text, "tmax": tmax},
        }

    monkeypatch.setattr(od.pack, "to_row", to_row)
    return seen


def _ledger(root: layout.Root) -> list[dict]:
    return layout.read_jsonl(root.evolution.ledger)


def test_seed_lookup_rejects_ambiguous_sources(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    shutil.copytree(root.data / "sources/tw-extract", root.data / "sources/rebench")
    with pytest.raises(od.NoSeed, match="ambiguous seed package"):
        od.materialize_r0(root, root.evolution.task("tw_a"), "tw_a")


def test_seed_lookup_reports_missing_source_directory(tmp_path, monkeypatch):
    root = layout.Root(tmp_path / "empty")
    with pytest.raises(od.NoSeed, match="no seed package"):
        od.materialize_r0(root, root.evolution.task("missing"), "missing")


def test_round_persists_simplify_choice(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _signal(root, direction="easier")
    choice = {"operator": "reduce_scale", "retained_skill": "convert files"}
    seen = _stub(monkeypatch, simplify=choice)
    od.run_round(root, workers=1)
    meta = json.loads(seen[0]["rewrite"].meta.read_text())
    assert meta["simplify"] == choice


def test_round_records_a_spec_repair_separately_from_simplification(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    _signal(root, direction="easier")
    repair = {"diagnosis": "missing public format"}
    seen = _stub(
        monkeypatch, action="repair", family="repair", operator=None, spec_repair=repair
    )
    od.run_round(root, workers=1)
    meta = json.loads(seen[0]["rewrite"].meta.read_text())
    assert meta["status"] == "accepted" and meta["result_rev"] == 1
    assert meta["action"] == "repair" and meta["spec_repair"] == repair
    assert "simplify" not in meta
    events = layout.read_jsonl(root.evolution.task("tw_a").lineage)
    assert next(e for e in events if e["event"] == "rewrite")["action"] == "repair"


def test_next_revision_receives_prior_simplification_and_current_outcome(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    _signal(root, direction="easier")
    choice = {"operator": "reduce_scale", "change": "retain two inputs"}
    seen = _stub(monkeypatch, simplify=choice)
    od.run_round(root, workers=1)
    assert seen[0]["previous_simplify"] is None
    _signal(root, rev=1, group=8, direction="harder", created="20260904-183112Z")
    od.run_round(root, workers=1)
    prior = seen[1]["previous_simplify"]
    original = root.evolution.task("tw_a").rev(0)
    assert prior == {
        "input_rev": 0,
        "result_rev": 1,
        "simplify": choice,
        "observed": {"direction": "harder", "solved": 2, "total": 2},
        "parent": {
            "path": "traces/previous-simplify-parent",
            "sha256": {name: layout.sha256_file(original / name) for name in SEED},
        },
    }
    assert seen[1]["previous_parent"] == {
        name: content.encode() for name, content in SEED.items()
    }
    assert not (root.evolution.task("tw_a").rev(2) / "traces").exists()


def test_calibration_preserves_the_intervention_for_the_next_revision(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    choice = {"operator": "reduce_scale", "change": "retain two inputs"}
    _signal(root, direction="easier")
    _stub(monkeypatch, simplify=choice)
    assert od.run_round(root, workers=1)["accepted"] == 1

    _signal(root, rev=1, group=8, direction="harder", created="20260904-183112Z")
    calibrated = _stub(monkeypatch, calibration=True)
    process = od.fb.process_one

    def calibrate(rewrite, *args, **kwargs):
        result = process(rewrite, *args, **kwargs)
        (rewrite.package / "run/hardening.md").write_text("Restore one input.\n")
        return result

    monkeypatch.setattr(od.fb, "process_one", calibrate)
    assert od.run_round(root, workers=1)["accepted"] == 1
    meta = json.loads(calibrated[0]["rewrite"].meta.read_text())
    assert "simplify" not in meta
    assert meta["calibration"] is True
    adjustment = {
        "input_rev": 1,
        "observed": {"direction": "harder", "solved": 2, "total": 2},
        "rationale": "Restore one input.\n",
    }
    assert meta["simplify_context"]["calibrations"] == [adjustment]

    _signal(root, rev=2, group=9, direction="easier", created="20260904-183212Z")
    following = _stub(monkeypatch)
    assert od.run_round(root, workers=1)["accepted"] == 1
    assert following[0]["previous_simplify"] == {
        "input_rev": 0,
        "result_rev": 2,
        "simplify": choice,
        "calibrations": [adjustment],
        "observed": {"direction": "easier", "solved": 0, "total": 2},
        "parent": {
            "path": "traces/previous-simplify-parent",
            "sha256": {
                name: layout.sha256_file(root.evolution.task("tw_a").rev(0) / name)
                for name in SEED
            },
        },
    }
    assert (
        following[0]["previous_parent"]["instruction.md"]
        == SEED["instruction.md"].encode()
    )


@pytest.mark.parametrize("change", ["file", "metadata", "both"])
def test_changed_simplify_reference_is_rejected_without_changing_original(
    tmp_path, monkeypatch, change
):
    root = _root(tmp_path, monkeypatch)
    _signal(root, direction="easier")
    _stub(monkeypatch, simplify={"operator": "reduce_scale"})
    assert od.run_round(root, workers=1)["accepted"] == 1
    _signal(root, rev=1, group=8, created="20260904-183112Z")
    seen = _stub(monkeypatch, calibration=True)
    process = od.fb.process_one

    def change_reference(rewrite, *args, **kwargs):
        result = process(rewrite, *args, **kwargs)
        reference = rewrite.traces / "previous-simplify-parent/instruction.md"
        if change in ("file", "both"):
            reference.write_text("A different original goal.\n")
        if change in ("metadata", "both"):
            record = rewrite.traces / "previous-simplify.json"
            context = json.loads(record.read_text())
            context["input_rev"] = 1
            context["parent"]["sha256"]["instruction.md"] = layout.sha256_file(
                reference
            )
            record.write_text(json.dumps(context))
        return result

    monkeypatch.setattr(od.fb, "process_one", change_reference)
    assert od.run_round(root, workers=1)["accepted"] == 0
    meta = json.loads(seen[0]["rewrite"].meta.read_text())
    assert meta["status"] == "rejected" and meta["stage"] == "simplify_context"
    original = root.evolution.task("tw_a").rev(0) / "instruction.md"
    assert original.read_text() == SEED["instruction.md"]
    assert not root.evolution.task("tw_a").rev(2).exists()


def test_ordinary_hardening_does_not_reuse_an_older_simplification(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    _signal(root, direction="easier")
    _stub(monkeypatch, simplify={"operator": "reduce_scale"})
    assert od.run_round(root, workers=1)["accepted"] == 1
    _signal(root, rev=1, group=8, created="20260904-183112Z")
    _stub(monkeypatch)
    assert od.run_round(root, workers=1)["accepted"] == 1
    _signal(root, rev=2, group=9, created="20260904-183212Z")
    following = _stub(monkeypatch)
    assert od.run_round(root, workers=1)["accepted"] == 1
    assert following[0]["previous_simplify"] is None


@pytest.mark.parametrize(
    "corpus", ["tw-extract", "swe-extract", "rebench-extract", "extract"]
)
def test_round_materializes_r0_handles_the_signal_and_folds_r1(
    tmp_path, monkeypatch, corpus
) -> None:
    root = _root(tmp_path, monkeypatch)
    if corpus != "tw-extract":
        (root.data / "sources/tw-extract").rename(root.data / "sources" / corpus)
    sid = _signal(root)
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert (r["handled"], r["accepted"], r["mix_version"]) == (1, 1, 2), r
    task = root.evolution.task("tw_a")
    # r0 is the seed, copied once, without the backup the pool stripped.
    assert (task.rev(0) / "instruction.md").read_text() == SEED["instruction.md"]
    assert not (task.rev(0) / "instruction.md.bak-1").exists()
    # process_one got the rewrite, input revision and training box.
    call = seen[0]
    assert call["job"] == "harder" and call["seed_dir"] == task.rev(0)
    assert call["resources"] == {"cpu": 1, "mem_gb": 2, "disk_gb": 2, "source": "row"}
    # Accepted: the package became r1 without the harness files, and the
    # rewrite directory keeps the record.
    rewrites = task.rewrite_dirs()
    assert len(rewrites) == 1 and rewrites[0].path.name.endswith("--harder")
    rw = rewrites[0]
    assert not rw.package.exists()
    assert (task.rev(1) / "instruction.md").read_text() == "harder\n"
    assert not (task.rev(1) / "AGENTS.md").exists()
    assert not (task.rev(1) / "run").exists() and not (task.rev(1) / "traces").exists()
    meta = json.loads(rw.meta.read_text())
    assert meta["status"] == "accepted" and meta["result_rev"] == 1
    assert meta["input_rev"] == 0 and meta["signal"] == sid and meta["job"] == "harder"
    assert "operator" not in meta
    assert meta["resources"]["cpu"] == 2 and meta["verdicts"] == VERDICTS
    assert meta["finished"] >= meta["started"] and meta["sessions"] == []
    # The mix moved to v2 with the row at rev 1, sized from the measurement.
    version, path = root.mix.live_version()
    assert version == 2 and path.name.startswith("v0002--")
    manifest = json.loads(root.mix.manifest_of(path).read_text())
    assert manifest["parent_version"] == 1 and manifest["rows"] == 1
    row = json.loads(root.mix.live.read_text())
    assert row["label"] == "tw_a" and row["metadata"]["instance_id"] == "tw_a"
    assert (
        row["metadata"]["rev"] == 1
        and row["metadata"]["problem_statement"] == "harder\n"
    )
    assert (
        row["metadata"]["daytona_cpu"],
        row["metadata"]["daytona_mem_gb"],
        row["metadata"]["daytona_disk_gb"],
    ) == (2, 4, 2)
    # Lineage: a fold line and a rewrite index line.
    events = layout.read_jsonl(task.lineage)
    fold = next(e for e in events if e["event"] == "fold")
    assert (fold["from_rev"], fold["to_rev"], fold["mix_version"]) == (0, 1, 2)
    assert fold["rewrite"] == f"rewrites/{rw.path.name}"
    index = next(e for e in events if e["event"] == "rewrite")
    assert index["status"] == "accepted" and index["input_rev"] == 0
    # The ledger line, last, closes the signal.
    lines = _ledger(root)
    assert len(lines) == 1
    assert lines[0]["signal"] == sid and lines[0]["outcome"] == "handled"
    outcomes = layout.read_jsonl(root.evolution.run_outcomes(RUN))
    assert len(outcomes) == 1
    assert outcomes[0]["status"] == "accepted"
    assert outcomes[0]["direction"] == "harder"
    assert lines[0]["rewrite"] == f"tasks/tw_a/rewrites/{rw.path.name}"
    assert (
        lines[0]["task"],
        lines[0]["rev"],
        lines[0]["run"],
        lines[0]["group"],
        lines[0]["direction"],
    ) == ("tw_a", 0, RUN, 7, "harder")
    # The signal file was never touched.
    assert root.run(RUN).signal("tw_a", 7).exists()
    # status.json from the files.
    status = od.rebuild_status(root)
    assert status["mix_version"] == 2 and status["pending"] == 0
    assert status["handled"] == 1 and status["accepted"] == 1
    assert status["rewrites_running"] == 0 and status["rejected"] == {}
    assert json.loads(root.evolution.status.read_text()) == status
    # A second round finds nothing: the ledger closed the signal.
    assert od.run_round(root, workers=1)["reason"] == "no signals"
    assert len(seen) == 1


def test_rejected_rewrite_keeps_its_package_and_its_hardlinked_traces(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    _stub(monkeypatch, status="rejected", stage="step_size", reason="one rung")

    r = od.run_round(root, workers=1)

    assert r["counts"] == {"rejected": 1} and r["mix_version"] is None
    task = root.evolution.task("tw_a")
    rw = task.rewrite_dirs()[0]
    assert rw.package.is_dir() and not task.rev(1).exists()
    # The records are the run's, by inode.
    run = root.run(RUN)
    for i in (1, 2):
        linked = rw.traces / f"attempt-{i:02d}.jsonl"
        assert (
            linked.stat().st_ino == run.rollout_record("tw_a", 7, i - 1).stat().st_ino
        )
    meta = json.loads(rw.meta.read_text())
    assert meta["status"] == "rejected" and meta["stage"] == "step_size"
    assert meta["reason"] == "one rung" and meta["finished"]
    assert [e["event"] for e in layout.read_jsonl(task.lineage)] == ["rewrite"]
    assert _ledger(root)[0]["outcome"] == "handled"
    assert root.mix.live_version()[0] == 1
    status = od.rebuild_status(root)
    assert status["rejected"] == {"step_size": 1} and status["accepted"] == 0


def test_an_unreadable_signal_is_junk_once_it_is_old_enough(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    run = root.run("tmax-9b--20260904-190000Z")
    stale = run.signal("tw_a", 3)
    stale.parent.mkdir(parents=True)
    stale.write_text("{not json")
    old = time.time() - 2 * od.FRESH_SEC
    os.utime(stale, (old, old))
    fresh = run.signal("tw_a", 4)
    fresh.write_text("")
    incomplete = run.signal("tw_a", 5)
    incomplete.write_text(json.dumps({"task": "tw_a", "rev": 0}))
    seen = _stub(monkeypatch)

    od.run_round(root, workers=1)

    lines = {l["signal"]: l for l in _ledger(root)}
    assert lines[layout.signal_id(run.name, "tw_a", 3)]["outcome"] == "junk"
    assert "unreadable" in lines[layout.signal_id(run.name, "tw_a", 3)]["reason"]
    assert lines[layout.signal_id(run.name, "tw_a", 3)]["group"] == 3
    assert lines[layout.signal_id(run.name, "tw_a", 5)]["outcome"] == "junk"
    assert "lacks" in lines[layout.signal_id(run.name, "tw_a", 5)]["reason"]
    assert layout.signal_id(run.name, "tw_a", 4) not in lines  # fresh: retried later
    assert seen == []
    assert stale.exists() and fresh.exists()  # nothing moved
    assert od.rebuild_status(root)["junk"] == 2


def test_a_task_without_a_seed_is_junk(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root, task="nope")
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert r["junk"] == 1 and seen == []
    line = _ledger(root)[0]
    assert line["signal"] == sid and line["outcome"] == "junk"
    assert "data/sources" in line["reason"]
    assert not root.evolution.task("nope").rev(0).exists()


@pytest.mark.parametrize("value,enabled", [(None, False), ("0", False), ("1", True)])
def test_simplify_requires_explicit_opt_in(monkeypatch, value, enabled):
    with monkeypatch.context() as env:
        if value is None:
            env.delenv("SWE_EVOLVE_SIMPLIFY", raising=False)
        else:
            env.setenv("SWE_EVOLVE_SIMPLIFY", value)
        importlib.reload(od)
        assert od.SIMPLIFY_ENABLED is enabled
    importlib.reload(od)


def test_a_deferred_signal_stays_deferred_when_the_switch_is_turned_on(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root, direction="easier")
    seen = _stub(monkeypatch)
    monkeypatch.setattr(od, "SIMPLIFY_ENABLED", False)

    r = od.run_round(root, workers=1)
    assert r["deferred"] == 1 and seen == []
    assert [l["outcome"] for l in _ledger(root)] == ["deferred"]
    # Off: a deferred signal is closed, not re-lined every round.
    assert od.run_round(root, workers=1)["reason"] == "no signals"
    assert len(_ledger(root)) == 1
    assert od.rebuild_status(root)["deferred"] == 1

    # On: the backlog stays closed. Those signals measured revisions the pool
    # has moved past; the arm picks up what training produces from now on.
    monkeypatch.setattr(od, "SIMPLIFY_ENABLED", True)
    assert od.rebuild_status(root)["pending"] == 0
    assert od.run_round(root, workers=1)["reason"] == "no signals"
    assert seen == []
    assert [l["outcome"] for l in _ledger(root)] == ["deferred"]

    # A signal training produces after the switch is on is handled.
    _signal(root, group=8, direction="easier", created="20260904-190000Z")
    r = od.run_round(root, workers=1)
    assert r["handled"] == 1 and seen[0]["job"] == "easier"


def test_one_signal_per_task_the_newest_at_the_current_rev(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    older = _signal(root, group=7, created="20260904-183012Z")
    newer = _signal(root, group=8, created="20260904-183500Z")
    stale = _signal(root, group=9, rev=1, created="20260904-184000Z")
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert r["handled"] == 1 and r["superseded"] == 2
    assert len(seen) == 1 and seen[0]["signal"]["group"] == 8
    lines = {l["signal"]: l for l in _ledger(root)}
    assert lines[newer]["outcome"] == "handled"
    assert lines[older]["outcome"] == "superseded" and newer in lines[older]["reason"]
    assert (
        lines[stale]["outcome"] == "superseded"
        and "current rev 0" in lines[stale]["reason"]
    )
    # The task is at r1 now; a late signal about rev 0 is superseded too.
    late = _signal(root, group=10, rev=0, created="20260904-190000Z")
    od.run_round(root, workers=1)
    assert {l["signal"]: l for l in _ledger(root)}[late]["outcome"] == "superseded"
    assert len(seen) == 1


def test_unchanged_late_signal_starts_a_new_rewrite(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    seen = _stub(monkeypatch, status="kept", harder_mode="student", require_solution_growth=False)
    process = od.fb.process_one

    def with_late_signal(*args, **kwargs):
        _signal(root, group=8, created="20260904-190000Z")
        return process(*args, **kwargs)

    monkeypatch.setattr(od.fb, "process_one", with_late_signal)
    od.run_round(root, workers=1)
    result = od.run_round(root, workers=1)
    assert result["handled"] == 1
    assert len(seen) == 2
    assert seen[1]["signal"]["group"] == 8
    lines = _ledger(root)
    assert lines[-1]["outcome"] == "handled"
    assert lines[-1]["rewrite"] != lines[0]["rewrite"]
    assert od.rebuild_status(root)["pending"] == 0
    assert od.run_round(root, workers=1)["handled"] == 0


def test_rejected_rewrite_does_not_skip_a_new_signal(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    seen = _stub(monkeypatch, status="rejected", harder_mode="student", require_solution_growth=False)
    od.run_round(root, workers=1)
    _signal(root, group=8)
    assert od.run_round(root, workers=1)["handled"] == 1
    _signal(root, group=9, n=3)
    assert od.run_round(root, workers=1)["handled"] == 1
    _signal(root, group=10, n=3, direction="easier")
    assert od.run_round(root, workers=1)["handled"] == 1
    assert len(seen) == 4


def test_failed_execution_can_retry_unchanged_feedback(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    seen = _stub(monkeypatch, status="failed")
    od.run_round(root, workers=1)
    _signal(root, group=8)
    assert od.run_round(root, workers=1)["handled"] == 1
    assert len(seen) == 2


def test_new_revision_starts_a_new_rewrite(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    seen = _stub(monkeypatch)
    od.run_round(root, workers=1)
    _signal(root, group=8, rev=1)
    assert od.run_round(root, workers=1)["handled"] == 1
    assert len(seen) == 2
    assert root.evolution.task("tw_a").rev(2).exists()


def test_explicit_replay_runs_a_handled_signal_in_dry_mode(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root)
    seen = _stub(monkeypatch, status="kept")
    od.run_round(root, workers=1)
    assert od.run_round(root, workers=1, signal=sid)["handled"] == 1
    assert len(seen) == 2


def test_limit_leaves_the_rest_pending_rather_than_superseded(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    seed_b = root.data / "sources" / "tw-extract" / "tasks" / "tw_b"
    for rel, text in SEED.items():
        (seed_b / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed_b / rel).write_text(text)
    _signal(root, task="tw_a", group=1)
    _signal(root, task="tw_a", group=2, created="20260904-190000Z")
    _signal(root, task="tw_b", group=3)
    _stub(monkeypatch, status="kept", stage="agent", reason="no suitable hardening")

    od.run_round(root, workers=1, limit=1)

    lines = _ledger(root)
    # tw_a sorts first: its newest was handled and its older one superseded;
    # tw_b was not reached and has no line, so it is still pending.
    assert {l["outcome"] for l in lines} == {"handled", "superseded"}
    assert all(l["task"] == "tw_a" for l in lines)
    assert od.rebuild_status(root)["pending"] == 1


def test_dry_round_writes_only_the_rewrite_directory(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    _stub(monkeypatch)

    r = od.run_round(root, workers=1, dry=True)

    assert r["counts"] == {"accepted": 1} and r["mix_version"] is None
    task = root.evolution.task("tw_a")
    rw = task.rewrite_dirs()[0]
    meta = json.loads(rw.meta.read_text())
    assert (
        meta["dry"] is True
        and meta["status"] == "accepted"
        and meta["result_rev"] is None
    )
    assert rw.package.is_dir() and (rw.traces / "attempt-01.jsonl").exists()
    assert not task.rev(1).exists()
    assert not root.evolution.ledger.exists() and not task.lineage.exists()
    assert root.mix.live_version()[0] == 1
    # Dry rewrites are not counted, and the signal is still pending.
    status = od.rebuild_status(root)
    assert status["accepted"] == 0 and status["pending"] == 1


def test_replay_handles_a_closed_signal_dry(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    sid = _signal(root)
    seen = _stub(monkeypatch)
    od.run_round(root, workers=1)
    assert len(_ledger(root)) == 1

    r = od.run_round(root, signal=sid)

    assert r["handled"] == 1 and len(seen) == 2
    rewrites = root.evolution.task("tw_a").rewrite_dirs()
    assert len(rewrites) == 2
    replay = json.loads(rewrites[-1].meta.read_text())
    assert (
        replay["dry"] is True and replay["signal"] == sid and replay["input_rev"] == 0
    )
    assert seen[1]["seed_dir"] == root.evolution.task("tw_a").rev(0)
    assert len(_ledger(root)) == 1 and root.mix.live_version()[0] == 2


def test_lineage_snapshot_commits_records_and_never_packages_or_sessions(
    tmp_path, monkeypatch
) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    _stub(monkeypatch, status="rejected", stage="oracle", reason="boom")
    od.run_round(root, workers=1)
    od.rebuild_status(root)
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    session = rw.session("agent")
    session.codex_home.mkdir(parents=True)
    layout.write_json_atomic(session.meta, {"kind": "agent"})
    (session.codex_home / "rollout.jsonl").write_text("{}\n")

    od._snapshot_lineage(root, "test snapshot")

    git_dir = root.evolution.path / ".git"
    tracked = subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={root.path}", "ls-files"],
        cwd=root.path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert set(tracked) == {
        "evolution/ledger.jsonl",
        f"evolution/outcomes/{RUN}.jsonl",
        "evolution/status.json",
        "evolution/tasks/tw_a/lineage.jsonl",
        f"evolution/tasks/tw_a/rewrites/{rw.path.name}/rewrite.json",
        str(root.mix.manifest_of(root.mix.live_version()[1]).relative_to(root.path)),
    }
    assert not (root.path / ".git").exists()
    assert not any("package" in p or "sessions" in p or "traces" in p for p in tracked)


def test_training_box_reads_the_row_then_the_fleet_default(monkeypatch) -> None:
    monkeypatch.setattr(od, "FLEET", {"cpu": None, "mem_gb": None, "disk_gb": None})
    assert od.training_box("t", {"t": {"cpu": 1, "mem_gb": 2, "disk_gb": 2}}) == {
        "cpu": 1,
        "mem_gb": 2,
        "disk_gb": 2,
        "source": "row",
    }
    # A row declaring nothing, with no fleet default in the env: the harness
    # default applies, and the source says so rather than inventing a number.
    assert od.training_box("t", {}) == {
        "cpu": None,
        "mem_gb": None,
        "disk_gb": None,
        "source": "harness_default",
    }
    monkeypatch.setattr(od, "FLEET", {"cpu": 1, "mem_gb": 2, "disk_gb": 2})
    assert od.training_box("t", {"t": {"mem_gb": 4}}) == {
        "cpu": 1,
        "mem_gb": 4,
        "disk_gb": 2,
        "source": "row+fleet_default",
    }


def test_strip_harness_leaves_the_package(tmp_path) -> None:
    pkg = tmp_path / "package"
    for rel in (
        "instruction.md",
        "AGENTS.md",
        "sandbox",
        "run/checks.jsonl",
        "traces/attempt-01.jsonl",
        "environment/fixture.csv",
        "tests/__pycache__/x.pyc",
    ):
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_text("x")
    od.strip_harness(pkg)
    assert sorted(str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file()) == [
        "environment/fixture.csv",
        "instruction.md",
    ]


HOOK = "set -u\nexit 0\n"
STAMP = "image:hamishi740/swerl-tmax-v3:37a79d0fd9b9"


def test_round_carries_the_rows_pin_hook_through_the_rewrite_and_the_fold(
    tmp_path, monkeypatch
) -> None:
    root = _root(
        tmp_path,
        monkeypatch,
        tmax={
            "test_sh": "echo 1\n",
            "pre_test_sh": HOOK,
            "pretest_env_identity": STAMP,
        },
    )
    _signal(root)
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert (r["handled"], r["accepted"], r["mix_version"]) == (1, 1, 2), r
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    # The loop snapshots the row's hook beside rewrite.json, outside package/,
    # where the probe reads it and the agent cannot edit it.
    assert layout.read_pretest(rw.pretest) == (HOOK, STAMP)
    # The fold hands the same hook to the row builder, which re-derives this
    # package's environment identity; the row keeps grading with the pins.
    assert [r["pretest"] for r in seen.rows] == [(HOOK, STAMP)]
    tm = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    assert tm["pre_test_sh"] == HOOK and tm["pretest_env_identity"] == STAMP


def test_a_row_without_a_hook_folds_without_one(tmp_path, monkeypatch) -> None:
    root = _root(tmp_path, monkeypatch)
    _signal(root)
    seen = _stub(monkeypatch)

    assert od.run_round(root, workers=1)["accepted"] == 1
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    assert not rw.pretest.exists()
    assert [r["pretest"] for r in seen.rows] == [None]
    assert [r["protected"] for r in seen.rows] == [
        None
    ]  # no lists on the row: none passed
    tm = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    assert not {"pre_test_sh", "protected_paths", "protected_cmds"} & set(tm)


PATHS = ["/app/pinned", "/app/data dir/model.bin", "tests"]
CMDS = ["sqlite3 /app/db \"select count(*) from t where n='x'\""]


def test_fold_carries_the_rows_protected_lists_when_the_package_ships_none(
    tmp_path, monkeypatch
) -> None:
    """The mix row a rewrite descends from carries protected lists; the rewrite's
    package ships no tests/protected_paths.json. The fold hands the row's lists to
    the row builder (which lets a package file override them), so the folded
    revision keeps grading by the same baseline -- the hole the loop PR closes."""
    root = _root(
        tmp_path,
        monkeypatch,
        tmax={"test_sh": "echo 1\n", "protected_paths": PATHS, "protected_cmds": CMDS},
    )
    _signal(root)
    seen = _stub(monkeypatch)

    r = od.run_round(root, workers=1)

    assert (r["handled"], r["accepted"], r["mix_version"]) == (1, 1, 2), r
    rw = root.evolution.task("tw_a").rewrite_dirs()[0]
    assert not (
        root.evolution.task("tw_a").rev(1) / "tests" / "protected_paths.json"
    ).exists()
    # No hook on this row, but the lists travel in the same snapshot: the hook readers see
    # None, the list reader sees the parent's lists (what the probe and the tool validate with).
    assert layout.read_pretest(rw.pretest) is None
    assert layout.read_protected_lists(rw.pretest) == {
        "protected_paths": PATHS,
        "protected_cmds": CMDS,
    }
    assert [r["protected"] for r in seen.rows] == [
        od.pack.Protected(PATHS, CMDS)
    ]  # as LISTS
    tm = json.loads(root.mix.live.read_text())["metadata"]["tmax"]
    assert tm["protected_paths"] == PATHS and tm["protected_cmds"] == CMDS


def test_each_accepted_rewrite_is_folded_on_its_own(tmp_path, monkeypatch):
    """Two accepted rewrites in one round become two mix versions, each
    published the moment its rewrite was accepted: a loop stopped after the
    first has already folded it."""
    root = _root(tmp_path, monkeypatch)
    shutil.copytree(
        root.data / "sources/tw-extract/tasks/tw_a",
        root.data / "sources/tw-extract/tasks/tw_b",
    )
    rows = root.mix.live.read_text().splitlines()
    row_b = json.loads(rows[0])
    row_b.update({"label": "tw_b"})
    row_b["metadata"] = {**row_b["metadata"], "instance_id": "tw_b"}
    root.mix.publish(rows + [json.dumps(row_b)])
    before = root.mix.live_version()[0]
    _signal(root, task="tw_a")
    _signal(root, task="tw_b", group=8)
    seen = _stub(monkeypatch)
    r = od.run_round(root, workers=2)
    assert (r["handled"], r["accepted"]) == (2, 2), r
    assert len(seen) == 2
    versions = [v for v, _ in root.mix.versions() if v > before]
    assert versions == [before + 1, before + 2], versions
    assert r["mix_version"] == before + 2
    for tid in ("tw_a", "tw_b"):
        assert root.evolution.task(tid).rev(1).exists()
        fold = [
            e
            for e in layout.read_jsonl(root.evolution.task(tid).lineage)
            if e["event"] == "fold"
        ]
        assert len(fold) == 1 and fold[0]["to_rev"] == 1
    live = {
        json.loads(l)["label"]: json.loads(l)
        for l in root.mix.live.read_text().splitlines()
    }
    assert live["tw_a"]["metadata"]["rev"] == 1 and live["tw_b"]["metadata"]["rev"] == 1


def test_fold_carries_the_rows_domain_annotation(tmp_path, monkeypatch):
    """terminal_domain lives on the row (from the corpus table), not in the
    package, so a folded row keeps the replaced row's value."""
    root = _root(tmp_path, monkeypatch)
    rows = [json.loads(l) for l in root.mix.live.read_text().splitlines()]
    rows[0]["metadata"]["terminal_domain"] = "data-science"
    root.mix.publish([json.dumps(r) for r in rows])
    _signal(root, task="tw_a")
    _stub(monkeypatch)
    r = od.run_round(root, workers=1)
    assert r["accepted"] == 1, r
    live = json.loads(root.mix.live.read_text().splitlines()[0])
    assert live["metadata"]["rev"] == 1
    assert live["metadata"]["terminal_domain"] == "data-science"


def _add_task(root: layout.Root, tid: str) -> None:
    """A second seed task and its mix row, so a round has two to choose from."""
    seed = root.data / "sources" / "tw-extract" / "tasks" / tid
    for rel, text in SEED.items():
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_text(text)
    rows = [line for line in root.mix.live.read_text().splitlines() if line]
    rows.append(
        json.dumps(
            {
                "prompt": SEED["instruction.md"],
                "label": tid,
                "metadata": {
                    "instance_id": tid,
                    "rev": 0,
                    "daytona_cpu": 1,
                    "daytona_mem_gb": 2,
                    "daytona_disk_gb": 2,
                },
            }
        )
    )
    root.mix.publish(rows)


def test_a_signal_arriving_mid_round_starts_without_waiting_for_the_next(
    tmp_path, monkeypatch
):
    """A freed worker takes new work now, not at the next round.

    The batch this replaced fixed its todo when the round opened, so one slow
    rewrite held every other worker idle however long the queue was.
    """
    root = _root(tmp_path, monkeypatch)
    _add_task(root, "tw_b")
    _signal(root, task="tw_a")

    seen = _stub(monkeypatch)
    started = od.fb.process_one
    written = []

    def arrive(rewrite, signal, **kwargs):
        # tw_b's signal is written while tw_a's rewrite is still running, so
        # only a refill can reach it inside this round.
        if not written:
            written.append(_signal(root, task="tw_b", group=8))
        return started(rewrite, signal, **kwargs)

    monkeypatch.setattr(od.fb, "process_one", arrive)
    result = od.run_round(root, workers=2)

    assert written, "the mid-round signal was never written"
    assert {s["signal"]["task"] for s in seen} == {"tw_a", "tw_b"}
    assert result["handled"] == 2


def test_new_signal_uses_idle_worker_before_current_rewrite_finishes(
    tmp_path, monkeypatch
):
    root = _root(tmp_path, monkeypatch)
    _add_task(root, "tw_b")
    _signal(root, task="tw_a")
    seen = _stub(monkeypatch)
    original = od.fb.process_one
    a_started, b_started, release_a = Event(), Event(), Event()

    def hold_first(rewrite, signal, **kwargs):
        if signal["task"] == "tw_a":
            a_started.set()
            assert release_a.wait(5), "test did not release the first rewrite"
        else:
            b_started.set()
        return original(rewrite, signal, **kwargs)

    monkeypatch.setattr(od.fb, "process_one", hold_first)
    monkeypatch.setattr(od, "FREE_SLOT_POLL_SEC", 0.01, raising=False)
    with ThreadPoolExecutor(max_workers=1) as runner:
        future = runner.submit(od.run_round, root, workers=2)
        assert a_started.wait(2)
        _signal(root, task="tw_b", group=8)
        try:
            assert b_started.wait(2), "the idle worker did not see the new signal"
        finally:
            release_a.set()
        result = future.result(timeout=5)
    assert {s["signal"]["task"] for s in seen} == {"tw_a", "tw_b"}
    assert result["handled"] == 2


def test_a_handler_that_raises_does_not_get_handed_back_forever(
    tmp_path, monkeypatch
):
    """A signal whose rewrite closed no ledger line must not be re-picked.

    run_round swallows an exception raised before a rewrite existed and
    writes no ledger line for it, so the signal is still pending. A refill
    that asked only "what is pending?" would hand the same one back on every
    pass and the round would never end; `taken` is what stops it.
    """
    root = _root(tmp_path, monkeypatch)
    _add_task(root, "tw_b")
    _signal(root, task="tw_a")
    _signal(root, task="tw_b", group=8)

    calls = []

    def boom(root_, sig, **kwargs):
        # handle() itself, not process_one: process_one's own errors are
        # caught inside and become a record, which the round closes.
        calls.append(sig.task)
        raise RuntimeError("no rewrite for you")

    monkeypatch.setattr(od, "handle", boom)
    result = od.run_round(root, workers=2)

    # Each task attempted once, the round ended, and nothing was handled.
    assert sorted(calls) == ["tw_a", "tw_b"], calls
    assert result["handled"] == 0
    assert not _ledger(root)


def _package(path: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)


def test_a_rewrite_sees_every_earlier_rewrite_of_its_task(tmp_path):
    """traces/history: an accepted rewrite diffed against its result, a
    rejected one against the package it left, notes and the measurement that
    triggered each; harness files and the rewrite itself are not history."""
    root = layout.Root(tmp_path / "root")
    task = root.evolution.task("tw_a")
    r1 = {**SEED, "instruction.md": "Write the report to /app/report.txt, sorted.\n"}
    _package(task.rev(0), SEED)
    _package(task.rev(1), r1)
    signals = root.run(RUN).signals
    signals.mkdir(parents=True)
    (signals / "tw_a--g3.json").write_text(json.dumps(
        {"run": RUN, "group": 3, "rev": 0, "solved": 16, "total": 16}))

    first = layout.RewriteDir(task.rewrites / "20260901-000000Z--harder")
    _package(first.package, {**r1, "run/hardening.md": "Require sorting.\n",
                             "AGENTS.md": "role\n"})
    layout.write_json_atomic(first.meta, {
        "job": "harder", "input_rev": 0, "result_rev": 1, "status": "accepted",
        "signal": f"{RUN}/tw_a--g3", "changed": ["instruction"]})
    second = layout.RewriteDir(task.rewrites / "20260902-000000Z--easier")
    _package(second.package, {**r1, "solution/solve.sh": "#!/bin/sh\nsort\n",
                               "run/failure.txt": "oracle failed: reward 0\n"})
    layout.write_json_atomic(second.meta, {
        "job": "easier", "input_rev": 1, "result_rev": None, "status": "rejected",
        "stage": "oracle", "reason": "reference failed",
        "student_feedback": {"measurement": {"solved": 0, "total": 16}}})
    now = layout.RewriteDir(task.rewrites / "20260903-000000Z--easier")
    now.traces.mkdir(parents=True)

    layout.append_jsonl(task.lineage, {"event": "fold", "from_rev": 0, "to_rev": 1,
                                       "rewrite": f"rewrites/{first.path.name}"})
    assert od._write_history(root, task, now, rev=1) == 3  # two attempts, one step
    history = now.traces / "history"
    [step] = [json.loads(line) for line in (history / "revisions.jsonl").read_text().splitlines()]
    assert step["from_rev"] == 0 and step["to_rev"] == 1 and step["by_rewrite"] == first.path.name
    assert "+Write the report to /app/report.txt, sorted." in (history / step["diff"]).read_text()
    index = [json.loads(line) for line in (history / "index.jsonl").read_text().splitlines()]
    assert [e["rewrite"] for e in index] == [first.path.name, second.path.name]
    assert index[0]["status"] == "accepted" and index[0]["measured_on_input"]["solved"] == 16
    assert index[1]["reason"] == "reference failed" and index[1]["measured_on_input"]["solved"] == 0
    accepted = (history / index[0]["diff"]).read_text()
    assert "+Write the report to /app/report.txt, sorted." in accepted
    assert "AGENTS.md" not in accepted and "hardening" not in accepted
    tried = (history / index[1]["diff"]).read_text()
    assert "+sort" in tried and "instruction.md" not in tried
    assert "Require sorting." in (history / index[0]["notes"]).read_text()
    assert "reward 0" in (history / index[1]["notes"]).read_text()
    summary = (history / "summary.md").read_text()
    assert "### r0 -> r1" in summary and f"rewrite {first.path.name} (harder)" in summary
    assert "Training on r0 before it: 16/16 attempts solved" in summary
    assert "instruction.md (+1 -1)" in summary
    assert f"### {second.path.name}: easier from r1, rejected at oracle" in summary
    assert "Reason: reference failed" in summary and "solution/solve.sh (+1 -0)" in summary


def test_a_task_without_earlier_rewrites_gets_no_history(tmp_path):
    root = layout.Root(tmp_path / "root")
    task = root.evolution.task("tw_a")
    now = layout.RewriteDir(task.rewrites / "20260903-000000Z--harder")
    now.traces.mkdir(parents=True)
    _package(task.rev(0), SEED)
    assert od._write_history(root, task, now, rev=0) == 0
    assert not (now.traces / "history").exists()
