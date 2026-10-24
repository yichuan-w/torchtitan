"""evolve_bench.py's bookkeeping on synthetic roots, evals and runs: no sandbox,
no GPU, no model call."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_bench as eb  # noqa: E402


def _row(task: str, rev: int = 0, test: str = "exit 0") -> dict:
    return {"prompt": task, "label": task,
            "metadata": {"instance_id": task, "rev": rev, "tmax": {"test_sh": test, "fixtures": {}}}}


def _jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _launch(evalset: Path, **over) -> dict:
    env = {**eb.STUDENT_RECIPE, "SWE_ROLLOUT_CONCURRENCY": eb.ROLLOUT_CONCURRENCY,
           "TMAX_AGENT": "terminus", "SWE_TB2_DATA": str(evalset)}
    return {"checkpoint": None, "tt_commit": "abc1234", "gpu_names": "NVIDIA B300",
            "num_generators": 1, "evalset_sha256": eb.layout.sha256_file(evalset), "env": env, **over}


def _eval(tmp: Path, name: str, evalset: Path, results: dict[str, list], attempt_dir: bool = False, **launch) -> Path:
    """results: task -> list of 'P' / 'F' / 'I' (infra), one per attempt."""
    ev = tmp / "evals" / name
    rec_root = Path(str(ev) + "--attempt-1") if attempt_dir else ev
    index = []
    for g, (task, outcomes) in enumerate(sorted(results.items()), start=1):
        for i, o in enumerate(outcomes):
            rel = f"validation_rollouts/{task}/g-{g}-r{i}.jsonl"
            p = rec_root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"task": task, "reward": 1.0 if o == "P" else 0.0}) + "\n")
            index.append({"task": task, "state": "PASS" if o == "P" else "FAIL", "infra_failed": o == "I",
                          "finish_reason": "submit" if o in "PF" else None, "record": rel})
    (ev / eb.INDEX).parent.mkdir(parents=True, exist_ok=True)
    (ev / eb.INDEX).write_text(json.dumps(index))
    (ev / "launch.json").write_text(json.dumps(_launch(evalset, **launch)))
    return ev


def test_bucket_follows_repair_eval_summary():
    assert eb.bucket(0, 1) == "ungraded"
    assert eb.bucket(5, 5) == "all_solved"
    assert eb.bucket(0, 5) == "none_solved"
    assert eb.bucket(2, 5) == "mixed"


def test_exact_tests_match_known_values():
    assert eb.mcnemar_exact(0, 5) == pytest.approx(0.0625)
    assert eb.mcnemar_exact(3, 3) == 1.0
    # R: fisher.test(matrix(c(1, 11, 9, 3), 2))$p.value
    assert eb.fisher_exact(1, 9, 11, 3) == pytest.approx(0.002759, abs=1e-6)


def test_read_eval_counts_infra_apart_and_finds_ungraded(tmp_path):
    es = _jsonl(tmp_path / "set.jsonl", [_row(t) for t in "abcde"])
    ev = _eval(tmp_path, "e1", es, {"a": "PPPPP", "b": "FFFFF", "c": "PFFFI", "d": "IIIIF"})
    e = eb.read_eval(ev)
    c = eb.counts(e["tasks"])
    assert (c["mixed"], c["all_solved"], c["none_solved"], c["ungraded"], c["no_attempts"]) == (1, 1, 1, 1, 1)
    assert c["attempts_infra"] == 5
    assert c["finish_failed"] == {"submit": 9}
    assert e["recipe"]["harness"] == "terminus"


def test_recipe_mismatch_covers_harness_and_gpu(tmp_path):
    es = _jsonl(tmp_path / "set.jsonl", [_row("a")])
    a = eb.read_eval(_eval(tmp_path, "a", es, {"a": "PF"}))["recipe"]
    launch = _launch(es, gpu_names="AMD MI355X")
    launch["env"]["TMAX_AGENT"] = "miniswe"
    b_dir = _eval(tmp_path, "b", es, {"a": "PF"})
    (b_dir / "launch.json").write_text(json.dumps(launch))
    b = eb.read_eval(b_dir)["recipe"]
    mm = eb.recipe_mismatch(a, b)
    assert set(mm) == {"harness", "gpu_names"}
    assert eb.recipe_mismatch(a, None)


def test_eval_env_refuses_a_clash_and_another_harness(tmp_path):
    set_dir = tmp_path / "sets" / "s"
    set_dir.mkdir(parents=True)
    (set_dir / "manifest.json").write_text(json.dumps({"student_harness": "terminus"}))
    es = tmp_path / "x.jsonl"
    eb._write_evalset(es, [_row("a")], set_dir=str(set_dir))
    env = eb.eval_env(es, "terminus", {"PATH": "/bin"})
    assert env["SWE_ROLLOUT_CONCURRENCY"] == "256" and env["TMAX_AGENT"] == "terminus"
    with pytest.raises(SystemExit, match="pins"):
        eb.eval_env(es, "terminus", {"SWE_ROLLOUT_CONCURRENCY": "445"})
    with pytest.raises(SystemExit, match="measured under terminus"):
        eb.eval_env(es, "miniswe", {})


def test_classify_separates_infra_from_verdicts():
    c = lambda st, reason="", stage="agent": eb.classify({"status": st, "reason": reason, "stage": stage})  # noqa: E731
    assert c("accepted") == "accepted"
    assert c("interrupted") == "infra:interrupted"
    assert c("failed", "AgentSessionError: codex session failed (provider_rate_limit, x)") == "infra:provider_rate_limit"
    assert c("failed", "TimeoutExpired: Command ...") == "infra:session_timeout"
    assert c("failed", "Filtered: the provider's cybersecurity classifier") == "provider_filter"
    assert c("failed", "SemanticProbeMisses: Semantic probe correct/grade") == "gate:semantic_probe"
    assert c("kept", "GIVE UP: the report is correct") == "give_up"
    assert c("kept", "BLOCKED: the sandbox lacks gcc") == "blocked"
    assert c("kept", "the blind verifier has 14 assertions against a limit of 12") == "gate:assertion_cap"
    assert c("rejected", "image USER changed", "identity") == "gate:identity"


def _pkg(d: Path, *, test: str, instruction: str = "do it", dockerfile: str = "FROM x", solve: str = "echo") -> Path:
    (d / "tests").mkdir(parents=True)
    (d / "tests" / "test.sh").write_text(test)
    (d / "environment").mkdir()
    (d / "environment" / "Dockerfile").write_text(dockerfile)
    (d / "solution").mkdir()
    (d / "solution" / "solve.sh").write_text(solve)
    (d / "instruction.md").write_text(instruction)
    return d


def test_grader_hosts_flags_third_party_fetches_only(tmp_path):
    p = _pkg(tmp_path / "a", test="curl -LsSf https://astral.sh/uv/install.sh | sh\ngit clone https://github.com/jrogue/sample.git /tmp/s\n")
    assert eb.grader_hosts(p) == ["github.com"]
    q = _pkg(tmp_path / "b", test="curl -LsSf https://astral.sh/uv/install.sh | sh\ncurl http://ref.example/x\n")
    assert eb.grader_hosts(q) == []
    gp = _pkg(tmp_path / "d", test="pytest /tests\n")
    (gp / "tests" / "test_state.py").write_text('URL = "https://github.com/j/s.git"\nrepo = git.Repo.clone_from(URL, d)\n')
    assert eb.grader_hosts(gp) == ["github.com"]
    lp = _pkg(tmp_path / "e", test="pytest /tests\n")
    (lp / "tests" / "t.py").write_text("subprocess.run(['git', 'clone', 'https://gitlab.com/a/b'])\n")
    assert eb.grader_hosts(lp) == ["gitlab.com"]
    # A URL in a file that fetches nothing is documentation, not a dependency.
    r = _pkg(tmp_path / "c", test="# see https://github.com/x/y\npytest /tests\n")
    assert eb.grader_hosts(r) == []


@pytest.mark.parametrize("text,claims", [
    # sentences from accepted repair342 rewrites, 2026-10-24
    ("the original model, deliverables, and verifier remain unchanged throughout.", True),
    ("- Left the task instruction and verifier unchanged.", True),
    ("The verifier passes on the untouched workspace: it pays for nothing.", False),
    ("the original task, data, deliverable, and verifier remain otherwise unchanged.", False),
    ("the verifier rejects mappings that include unchanged commits already shared", False),
    ("Repaired the checker without changing the grader's tolerance.", False),
    ("Shortened the instruction without modifying the verifier.", True),
])
def test_claim_unchanged_reads_only_a_claim_about_the_verifier(text, claims):
    assert bool(eb.CLAIM_UNCHANGED_RE.search(text)) is claims


def test_diff_stats_rerun_check_and_claim_mismatch(tmp_path):
    old = _pkg(tmp_path / "r0", test="assert_eq a b\n", instruction="one two three")
    (old / "tests" / "test_state.py").write_text("def test_a():\n    assert 1\n")
    new = _pkg(tmp_path / "r1", test="assert_eq a b\n", instruction="one two three four", dockerfile="FROM y")
    (new / "tests" / "test_state.py").write_text(
        "import subprocess, tempfile\ndef test_a():\n    assert 1\n"
        "def test_rerun():\n    d = tempfile.mkdtemp()\n    open(d + '/in.csv', 'w').write('x')\n"
        "    subprocess.run(['python3', '/app/run.py', d])\n    assert True\n")
    d = eb.diff_stats(old, new, "I left the verifier unchanged and only edited the instruction.")
    assert d["adds_rerun_check"] and d["dockerfile_changed"] and d["tests_changed"]
    assert d["assertions"][1] - d["assertions"][0] == 1
    assert d["instruction_words"] == [3, 4]
    assert d["claim_mismatch"]
    assert not eb.diff_stats(old, new, "Added a check that reruns the script.")["claim_mismatch"]


def _root_with_rewrites(tmp: Path) -> Path:
    """A root with three tasks: t1 accepted harder (r0 grader clones github,
    r1 does not), t2 kept, t3 lost to infra twice."""
    root = tmp / "root"
    for tid in ("t1", "t2", "t3"):
        _pkg(root / "evolution" / "tasks" / tid / "r0", test="git clone https://github.com/a/b.git" if tid == "t1" else "true")
    _pkg(root / "evolution" / "tasks" / "t1" / "r1", test="pytest /tests")
    sig_dir = root / "runs" / "src" / "signals"
    sig_dir.mkdir(parents=True)

    def rw(tid, stamp, job, status, reason="", result_rev=None, stage=None):
        d = root / "evolution" / "tasks" / tid / "rewrites" / f"{stamp}--{job}"
        d.mkdir(parents=True)
        (sig_dir / f"{tid}--g1.json").write_text(json.dumps({"solved": 5 if job == "harder" else 0, "total": 5}))
        (d / "rewrite.json").write_text(json.dumps({"task": tid, "job": job, "status": status, "reason": reason,
                                                    "stage": stage, "input_rev": 0, "result_rev": result_rev,
                                                    "signal": f"src/{tid}--g1"}))

    rw("t1", "20261024-000000Z", "harder", "accepted", result_rev=1, stage="daytona_oracle")
    rw("t2", "20261024-000000Z", "easier", "kept", "GIVE UP: nothing to simplify")
    rw("t3", "20261024-000000Z", "easier", "failed", "AgentSessionError: codex session failed (codex_exit_nonzero, 1)")
    rw("t3", "20261024-010000Z", "easier", "interrupted")
    return root


def test_tally_root_with_eval_funnel_directions_and_repair(tmp_path):
    root = _root_with_rewrites(tmp_path)
    es = _jsonl(tmp_path / "acc.jsonl", [_row("t1", rev=1)])
    ev = _eval(tmp_path, "after", es, {"t1": "PFPFF"})
    labels = tmp_path / "labels.tsv"
    labels.write_text("task_id\tdefect_class\tfixed\tnote\nt1\texternal_network\tyes\tclones a live repo\n")
    log = eb.Log(tmp_path / "t.log")
    out = eb.tally(evals=[ev], runs=[], evalset=None, root=root, set_dir=None, before=[], labels=labels, log=log)
    f = out["funnel"]
    assert f["final"] == {"accepted": 1, "give_up": 1, "infra:interrupted": 1}
    assert f["acceptance_rate"] == 0.5  # infra losses are not verdicts
    assert out["directions"]["harder"]["mixed"] == 1
    assert out["rewrite_stats"]["harder"]["grader_network"] == {"introduced": 0, "kept": 0, "removed": 1}
    rep = out["repair"]
    assert rep["external_network"]["fixed_mechanical"] == 1
    assert rep["external_network"]["ground_truth"]["agree"] == 1
    assert rep["grader_fetch"]["tasks"] == 1
    _, md = eb.compare([("x", out)], allow_mismatch=False)
    assert "| x | eval / terminus | 1 / 3 |" in md


def test_tally_reads_bands_off_a_training_run(tmp_path):
    root = _root_with_rewrites(tmp_path)
    run = root / "runs" / "train"
    (run / "rollouts" / "t1").mkdir(parents=True)
    (run / "launch.json").write_text(json.dumps({"env": {"TMAX_AGENT": "miniswe", "SWE_GROUP_SIZE": "4"}}))
    # rev 1's first group (7) is all-fail; a later group (9) would read mixed.
    for g, rewards, started in ((7, [0.0, 0.0, 0.0, -0.3], "20261024-020000Z"), (9, [1.0, 0.0, 0.0, 0.0], "20261024-030000Z")):
        for i, r in enumerate(rewards):
            (run / "rollouts" / "t1" / f"g{g}-r{i}.jsonl").write_text(
                json.dumps({"task": "t1", "rev": 1, "group": g, "reward": r, "started": started, "finish_reason": "submit"}) + "\n")
    out = eb.tally(evals=[], runs=[run], evalset=None, root=root, set_dir=None, before=[], labels=None,
                   log=eb.Log(tmp_path / "t.log"))
    assert out["recipe"]["harness"] == "miniswe"
    assert out["directions"]["harder"]["overshoot"] == 1


def test_before_after_transitions_and_merge_refuses_overlap(tmp_path):
    es0 = _jsonl(tmp_path / "orig.jsonl", [_row(t) for t in "abc"])
    before = _eval(tmp_path, "b", es0, {"a": "PPPPP", "b": "FFFFF", "c": "PPFFF"})
    es1 = _jsonl(tmp_path / "acc1.jsonl", [_row("a", 1)])
    es2 = _jsonl(tmp_path / "acc2.jsonl", [_row(t, 1) for t in "bc"])
    a1 = _eval(tmp_path, "a1", es1, {"a": "PPFFF"})
    a2 = _eval(tmp_path, "a2", es2, {"b": "FFFFF", "c": "PPPPP"}, attempt_dir=True)
    out = eb.tally(evals=[a1, a2], runs=[], evalset=None, root=None, set_dir=None, before=[before], labels=None,
                   log=eb.Log(tmp_path / "t.log"))
    t = out["before_after"]
    assert t["table"] == {"all_solved->mixed": 1, "mixed->all_solved": 1, "none_solved->none_solved": 1}
    assert (t["mixed_gained"], t["mixed_lost"]) == (1, 1)
    with pytest.raises(SystemExit, match="already did"):
        eb.merge_evals([eb.read_eval(a1), eb.read_eval(a1)])


def test_freeze_from_eval_pins_everything_and_evalset_follows(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "_bench_commit", lambda: "test")
    src = tmp_path / "corpus"
    rows = [_row(t, test=f"test {t}") for t in ("a", "b", "c")]
    for r in rows:
        _pkg(src / "tasks" / r["label"], test=r["metadata"]["tmax"]["test_sh"])
    mix = _jsonl(tmp_path / "mix.jsonl", rows)
    ev = _eval(tmp_path, "base", mix, {"a": "PPPPP", "b": "FFFFF", "c": "PFPFP"}, attempt_dir=True)
    bench = tmp_path / "bench"
    out = eb.freeze(bench=bench, name="s1", run=None, eval_dir=ev, selected=None, release=None, mix=mix,
                    sources=[src], log=eb.Log(tmp_path / "f.log"))
    m, sha = eb.load_set(out, verify=True)
    assert m["directions"] == {"harder": 1, "easier": 1}
    assert m["no_signal"] == {"mixed": 1} and m["student_harness"] == "terminus"
    sig = json.load(open(out / "runs" / "s1" / "signals" / "a--g-1.json"))
    assert sig["direction"] == "harder" and sig["rev"] == 0 and len(sig["attempts"]) == 5
    assert (out / "runs" / "s1" / sig["attempts"][0]).exists()
    # A changed package is caught before an arm runs on it.
    (src / "tasks" / "a" / "instruction.md").write_text("changed")
    with pytest.raises(SystemExit, match="pinned items changed"):
        eb.load_set(out, verify=True)
    with pytest.raises(SystemExit, match="frozen once"):
        eb.freeze(bench=bench, name="s1", run=None, eval_dir=ev, selected=None, release=None, mix=mix,
                  sources=[src], log=eb.Log(tmp_path / "f.log"))


def test_freeze_leaves_out_a_package_that_is_not_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "_bench_commit", lambda: "test")
    src = tmp_path / "corpus"
    _pkg(src / "tasks" / "a", test="other")
    mix = _jsonl(tmp_path / "mix.jsonl", [_row("a", test="mine")])
    ev = _eval(tmp_path, "base", mix, {"a": "PPPPP"})
    out = eb.freeze(bench=tmp_path / "bench", name="s", run=None, eval_dir=ev, selected=None, release=None,
                    mix=mix, sources=[src], log=eb.Log(tmp_path / "f.log"))
    m, _ = eb.load_set(out, verify=True)
    assert m["package_not_row"] == ["a"] and m["tasks"] == 0


def test_load_arm_refuses_pinned_or_derived_env(tmp_path):
    arm = {"name": "ctl", "profile": "andy", "commit": "abc", "bin": "/b", "workers": 24,
           "env": {"SYNTH_MODEL": "gpt-5.6-sol"}}
    p = tmp_path / "arm.json"
    p.write_text(json.dumps(arm))
    assert eb.load_arm(p)["workers"] == 24
    for bad in ({"SWE_POLICY_CANARY_K": "2"}, {"WORKERS": "8"}):
        p.write_text(json.dumps({**arm, "env": bad}))
        with pytest.raises(SystemExit, match="pins or derives"):
            eb.load_arm(p)


def test_compare_refuses_differing_recipes_unless_allowed(tmp_path):
    es = _jsonl(tmp_path / "s.jsonl", [_row("a")])
    a = eb.tally(evals=[_eval(tmp_path, "a", es, {"a": "PF"})], runs=[], evalset=None, root=None, set_dir=None,
                 before=[], labels=None, log=eb.Log(tmp_path / "t.log"))
    b = eb.tally(evals=[_eval(tmp_path, "b", es, {"a": "PP"}, tt_commit="def5678")], runs=[], evalset=None,
                 root=None, set_dir=None, before=[], labels=None, log=eb.Log(tmp_path / "t.log"))
    with pytest.raises(SystemExit, match="not comparable"):
        eb.compare([("a", a), ("b", b)], allow_mismatch=False)
    res, md = eb.compare([("a", a), ("b", b)], allow_mismatch=True)
    assert md.startswith("**NOT COMPARABLE**")
    assert res["pairs"][0]["common_accepted"]["mixed_lost"] == 1


def test_audit_bundle_blinds_and_audit_tally_unblinds(tmp_path):
    arms = []
    for label in ("x", "y"):
        root = tmp_path / label
        _pkg(root / "evolution" / "tasks" / "t1" / "r0", test="true")
        _pkg(root / "evolution" / "tasks" / "t1" / "r1", test=f"check {label}")
        d = root / "evolution" / "tasks" / "t1" / "rewrites" / "20261024-000000Z--harder"
        d.mkdir(parents=True)
        (d / "rewrite.json").write_text(json.dumps({"task": "t1", "job": "harder", "status": "accepted",
                                                    "input_rev": 0, "result_rev": 1}))
        arms.append((label, root))
    out, key = tmp_path / "audit", tmp_path / "keys" / "key.json"
    with pytest.raises(SystemExit, match="outside"):
        eb.audit_bundle(arms=arms, out=out, key=out / "key.json", sample=None, seed=0, log=eb.Log(tmp_path / "a.log"))
    eb.audit_bundle(arms=arms, out=out, key=key, sample=None, seed=0, log=eb.Log(tmp_path / "a.log"))
    k = json.load(open(key))
    assert sorted(k["tasks"]["t1"].values()) == ["x", "y"]
    assert not any(p.name in ("x", "y") for p in out.rglob("*"))
    diffs = "".join(p.read_text() for p in out.rglob("diff_*.txt"))
    assert "check x" in diffs and str(tmp_path) not in diffs
    vx = next(v for v, a in k["tasks"]["t1"].items() if a == "x")
    (out / "t1" / vx / "verdict.json").write_text(json.dumps(
        {"label": "UNFAIR", "findings": [{"id": "f1", "class": "UNFAIR"}, {"id": "f2", "class": "SEED"}]}))
    (out / "t1" / vx / "verify.json").write_text(json.dumps({"f1": "confirmed"}))
    tallies = {lbl: {"funnel": {"usd_counted": 10.0},
                     "rewrites": [{"task": "t1", "class": "accepted", "after_band": "mixed"}]} for lbl in ("x", "y")}
    res = eb.audit_tally(audit=out, key=key, tallies=tallies)
    assert res["x"]["confirmed_defects"] == {"UNFAIR": 1} and res["x"]["mixed_defect_rate"] == 1.0
    assert res["x"]["usd_per_clean_mixed"] is None
    assert res["y"]["audited"] == 0
