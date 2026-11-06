# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import evolve_chain as ec  # noqa: E402


@pytest.mark.parametrize(
    "passed,graded,want",
    [(12, 12, "12/12"), (0, 12, "0/12"), (3, 12, "mixed"), (11, 12, "mixed"), (1, 12, "mixed"),
     (11, 11, "12/12"), (0, 10, "0/12"), (5, 9, "ungraded")],
)
def test_band(passed, graded, want):
    assert ec.band(passed, graded) == want


def _hop(h, direction, band, state="measured", audit=None):
    hop = {"hop": h, "direction": direction, "state": state, "band": band if state == "measured" else None,
           "audit": audit}
    hop["cls"] = ec.hop_class(hop, audit)
    return hop


CLEAN = {"label": "EFFECTIVE", "confirmed": [], "defects": [], "settled": True}
LEAKY = {"label": "LEAKY", "confirmed": ["LEAKY"], "defects": ["LEAKY"], "settled": True}


def test_metrics_stubborn_and_oscillation():
    per_task = {
        # easier twice, still 0/12: stubborn
        "a": [_hop(1, "easier", "0/12"), _hop(2, "easier", "0/12"), _hop(3, "easier", "mixed", audit=CLEAN)],
        # one unchanged hop only: not stubborn
        "b": [_hop(1, "harder", "12/12"), _hop(2, "harder", "mixed", audit=LEAKY)],
        # harder overshoots to 0/12, easier swings back to 12/12: oscillation
        "c": [_hop(1, "harder", "0/12"), _hop(2, "easier", "12/12"), _hop(3, "harder", "mixed")],
        # unchanged then not accepted: stubborn only when not-accepted counts as unchanged
        "d": [_hop(1, "harder", "12/12"), _hop(2, "harder", None, state="not_accepted")],
        "e": [_hop(1, "easier", "mixed", audit=CLEAN)],
    }
    m = ec.chain_metrics(per_task, 5)
    assert m["stubborn"]["ids"] == ["a"]
    assert m["stubborn_counting_not_accepted"]["ids"] == ["a", "d"]
    assert m["oscillation"]["ids"] == ["c"]
    assert m["stubborn"]["rate"] == 0.2
    assert m["per_hop"][1]["reached"] == 5
    assert m["per_hop"][1]["0/12"] == 2 and m["per_hop"][1]["12/12"] == 2 and m["per_hop"][1]["clean_mixed"] == 1
    assert m["per_hop"][1]["by_direction"]["easier"]["clean_mixed"] == 1
    assert m["per_hop"][2]["mixed_defect"] == 1 and m["per_hop"][2]["confirmed_defects"] == {"LEAKY": 1}
    assert m["per_hop"][3]["clean_mixed"] == 1 and m["per_hop"][3]["mixed_unaudited"] == 1
    assert m["per_hop"][2]["not_accepted"] == 1
    assert m["per_hop"][3]["reached"] == 2


def _record(path: Path, task: str, rev: int, reward: float, group: int = -3, infra: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"task": task, "rev": rev, "group": group, "rollout": 0, "reward": reward,
                                "infra_failed": infra}) + "\nturns\n")


def _rewrite(root: Path, tid: str, stamp: str, job: str, input_rev: int, status: str) -> None:
    d = root / "evolution" / "tasks" / tid / "rewrites" / f"{stamp}--{job}"
    d.mkdir(parents=True)
    rw = {"task": tid, "job": job, "input_rev": input_rev, "status": status}
    if status == "accepted":
        rw["result_rev"] = input_rev + 1
    (d / "rewrite.json").write_text(json.dumps(rw))


def test_task_hops_reads_rewrites_and_hop_evals(tmp_path):
    root = tmp_path / "arm"
    _rewrite(root, "t", "20261106-000000Z", "harder", 0, "failed")
    _rewrite(root, "t", "20261106-010000Z", "harder", 0, "accepted")
    _rewrite(root, "t", "20261106-020000Z", "harder", 1, "accepted")
    _rewrite(root, "t", "20261106-030000Z", "easier", 2, "rejected")
    ev1 = root / "evals" / "chain-hop1--x"
    for i in range(12):
        _record(ev1 / "validation_rollouts" / "t" / f"g-3-r{i}.jsonl", "t", 1, 1.0)
    ev2 = root / "evals" / "chain-hop2--y"
    for i in range(12):
        _record(ev2 / "validation_rollouts" / "t" / f"g-4-r{i}.jsonl", "t", 2, 0.0, infra=i == 0)
    hops = ec.task_hops(root, "t", "12/12")
    assert [h["hop"] for h in hops] == [1, 2, 3]
    assert hops[0]["direction"] == "harder" and hops[0]["band"] == "12/12" and hops[0]["attempts"] == 2
    assert hops[1]["band"] == "0/12" and hops[1]["graded"] == 11 and hops[1]["infra"] == 1
    assert hops[2]["direction"] == "easier" and hops[2]["state"] == "not_accepted"
    # a mixed entry never enters
    assert ec.task_hops(root, "t", "mixed") == []


def test_split_eval_strips_the_prefix(tmp_path):
    comb = tmp_path / "evals" / "20261106-000000Z--comb"
    rows = []
    for label in ("A0", "AQ2"):
        tid = f"{label}__t"
        rows.append({"label": tid, "metadata": {"instance_id": tid, "bench_orig_instance_id": "t",
                                                "bench_group": label, "rev": 1, "tmax": {"test_sh": "x"}}})
        for i in range(2):
            _record(comb / "validation_rollouts" / tid / f"g-1-r{i}.jsonl", tid, 1, float(label == "A0"))
    evalset = tmp_path / "comb.jsonl"
    evalset.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (comb / "trainer" / "validation_traces" / "step-0").mkdir(parents=True)
    (comb / "trainer" / "validation_traces" / "step-0" / "index.json").write_text(json.dumps(
        [{"task": r["label"], "record": f"validation_rollouts/{r['label']}/g-1-r{i}.jsonl", "state": "PASS"}
         for r in rows for i in range(2)]))
    (comb / "launch.json").write_text(json.dumps({"env": {"SWE_TB2_DATA": str(evalset), "TMAX_AGENT": "terminus"}}))
    out = ec.split_eval(comb, "AQ2", tmp_path / "arm" / "evals" / "chain-hop1--comb")
    heads = [json.loads(p.read_text().splitlines()[0]) for p in sorted((out / "validation_rollouts" / "t").iterdir())]
    assert [h["task"] for h in heads] == ["t", "t"] and all(h["reward"] == 0.0 for h in heads)
    index = json.loads((out / "trainer" / "validation_traces" / "step-0" / "index.json").read_text())
    assert [e["record"] for e in index] == ["validation_rollouts/t/g-1-r0.jsonl", "validation_rollouts/t/g-1-r1.jsonl"]
    row = json.loads((out / "evalset.jsonl").read_text().splitlines()[0])
    assert row["metadata"]["instance_id"] == "t" and "bench_group" not in row["metadata"]
    launch = json.loads((out / "launch.json").read_text())
    assert launch["env"]["SWE_TB2_DATA"] == str((out / "evalset.jsonl").resolve())


def test_repeat_check_rules():
    measured = {"hop": 2, "direction": "harder", "state": "measured", "band": "12/12", "rev": 2}
    assert ec.repeat_check("no-difficulty-added", measured, None, {}, False)[0] == "yes"
    assert ec.repeat_check("hint-left", measured, None, {}, False)[0] == "unaudited"
    assert ec.repeat_check("hint-left", measured, {"label": "EFFECTIVE", "confirmed": ["LEAKY"]}, {}, False)[0] == "yes"
    assert ec.repeat_check("reward-hack", measured, None, {}, False)[0] == "n/a"
    easier = {"hop": 1, "direction": "easier", "state": "measured", "band": "0/12", "rev": 1}
    assert ec.repeat_check("never-solved", easier, None, {}, False)[0] == "yes"
    assert ec.repeat_check("verifier-weakened", easier, None, {"assertions": [10, 6], "tests_size_ratio": 0.7},
                           False)[0] == "yes"
    refused = {"hop": 1, "direction": "harder", "state": "not_accepted", "class": "give_up"}
    assert ec.repeat_check("refuses-harder", refused, None, {}, False)[0] == "yes"


def test_hop_collect_stages_the_next_hop(tmp_path):
    """A combined hop-1 eval -> the arm's derived eval -> a hop-2 signal at
    rev 1 that offline_stage.py (as offline_drive.sh calls it) stages into a
    root whose r0 rewrite was accepted."""
    import argparse
    import subprocess

    bench = tmp_path / "bench"
    root = bench / "arms" / "c--A0"
    (root / "evolution" / "tasks" / "t" / "r0").mkdir(parents=True)
    (root / "evolution" / "tasks" / "t" / "r1").mkdir(parents=True)
    _rewrite(root, "t", "20261106-000000Z", "harder", 0, "accepted")
    chain = bench / "chains" / "c"
    chain.mkdir(parents=True)
    (chain / "chain.json").write_text(json.dumps({"arms": {"A0": {"root": str(root)}}, "harness": "terminus"}))
    (bench / "sets" / "c").mkdir(parents=True)
    (bench / "sets" / "c" / "manifest.json").write_text(json.dumps({"measured": {"t": {"solved": 12, "graded": 12}}}))
    comb = tmp_path / "evals" / "20261106-010000Z--hop1"
    row = {"label": "A0__t", "metadata": {"instance_id": "A0__t", "bench_orig_instance_id": "t", "bench_group": "A0",
                                         "rev": 1, "tmax": {"test_sh": "x"}}}
    (tmp_path / "hop1.jsonl").write_text(json.dumps(row) + "\n")
    for i in range(12):
        _record(comb / "validation_rollouts" / "A0__t" / f"g-5-r{i}.jsonl", "A0__t", 1, 1.0, group=-5)
    (comb / "launch.json").write_text(json.dumps({"env": {"SWE_TB2_DATA": str(tmp_path / "hop1.jsonl")}}))
    ec.hop_collect(argparse.Namespace(bench=bench, chain="c", hop=1, eval=[comb]))
    run = root / "chain" / "hop2-signals"
    sig = json.loads(next((run / "signals").iterdir()).read_text())
    assert sig["task"] == "t" and sig["rev"] == 1 and sig["direction"] == "harder" and sig["total"] == 12
    assert ec.task_hops(root, "t", "12/12")[0]["band"] == "12/12"
    out = subprocess.run(
        [sys.executable, str(Path(ec.__file__).parent / "offline_stage.py"), "--run", str(run),
         "--selected", str(run / "selected.json"), "--root", str(root), "--name", "hop2.offline1",
         "--skip-accepted", "--only-failed", "--max-attempts", "2", "--attempts-since", "20261106-0100"],
        check=True, capture_output=True, text=True).stdout
    assert out.startswith("staged 1 signals, 12 rollout records")


def test_audit_bundle_hop2_gives_each_variant_its_own_original(tmp_path):
    import argparse

    bench = tmp_path / "bench"
    roots = {}
    for label in ("A0", "AQ2"):
        root = bench / "arms" / f"c--{label}"
        for r in range(3):
            d = root / "evolution" / "tasks" / "t" / f"r{r}"
            (d / "tests").mkdir(parents=True)
            (d / "instruction.md").write_text(f"{label} r{r}\n")
        _rewrite(root, "t", "20261106-000000Z", "harder", 0, "accepted")
        _rewrite(root, "t", "20261106-010000Z", "harder", 1, "accepted")
        roots[label] = root
    chain = bench / "chains" / "c"
    chain.mkdir(parents=True)
    (chain / "chain.json").write_text(json.dumps({"arms": {k: {"root": str(v)} for k, v in roots.items()}}))
    out, key = tmp_path / "audit", tmp_path / "audit-key.json"
    ec.audit_bundle(argparse.Namespace(bench=bench, chain="c", hop=2, out=out, key=key, seed=1))
    k = json.loads(key.read_text())
    assert sorted(k["task_of"]) == ["t~1", "t~2"] and set(k["task_of"].values()) == {"t"}
    for dname, variants in k["tasks"].items():
        (label,) = variants.values()
        assert (out / dname / "original" / "instruction.md").read_text() == f"{label} r1\n"
        assert (out / dname / "V1" / "package" / "instruction.md").read_text() == f"{label} r2\n"
    (out / "t~1" / "V1" / "verdict.json").write_text(json.dumps(
        {"label": "LEAKY", "findings": [{"id": "f1", "class": "LEAKY"}]}))
    (out / "t~1" / "V1" / "verify.json").write_text(json.dumps({"f1": "confirmed"}))
    got = ec._audits([(out, key)])
    lab = k["tasks"]["t~1"]["V1"]
    assert got[(lab, "t", 2)] == {"label": "LEAKY", "confirmed": ["LEAKY"], "defects": ["LEAKY"], "settled": True}
