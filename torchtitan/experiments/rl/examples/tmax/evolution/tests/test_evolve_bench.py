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
    env = {**eb.STUDENT_RECIPES[eb.DEFAULT_RECIPE], "SWE_BENCH_RECIPE": eb.DEFAULT_RECIPE,
           "SWE_ROLLOUT_CONCURRENCY": eb.ROLLOUT_CONCURRENCY,
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


def _as_legacy(ev: Path) -> Path:
    """Rewrite an eval's launch.json as if it ran the legacy-2400 recipe."""
    launch = json.load(open(ev / "launch.json"))
    launch["env"] = {**eb.STUDENT_RECIPES["legacy-2400"], "SWE_BENCH_RECIPE": "legacy-2400",
                     "TMAX_AGENT": "terminus", "SWE_TB2_DATA": launch["env"]["SWE_TB2_DATA"]}
    (ev / "launch.json").write_text(json.dumps(launch))
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
    assert env["SWE_ROLLOUT_CONCURRENCY"] == "128" and env["TMAX_AGENT"] == "terminus"
    assert env["TMAX_CALL_LIMIT"] == "120"
    # every eval runs the training recipe, also of a set measured under the legacy one
    assert env["SWE_BENCH_RECIPE"] == "train-2026-10-25"
    assert (env["SWE_TMAX_CONTEXT"], env["SWE_MAX_CONTEXT_LEN"], env["SWE_EVAL_TIME_BUDGET_SEC"]) == ("81920", "79872", "7200")
    assert (env["SWE_TB2_VAL_K"], env["TMAX_TURN_MAX_TOKENS"]) == ("16", "16384")
    assert (env["SWE_EVAL_CONTEXT_NOTICE"], env["SWE_EVAL_GRADE_UNSUBMITTED"], env["SWE_EVAL_STRICT_OPENING"]) == ("1", "0", "1")
    (set_dir / "manifest.json").write_text(json.dumps({"student_harness": "terminus", "student_recipe": "legacy-2400"}))
    assert eb.eval_env(es, "terminus", {})["SWE_BENCH_RECIPE"] == "train-2026-10-25"
    with pytest.raises(SystemExit, match="pins"):
        eb.eval_env(es, "terminus", {"SWE_ROLLOUT_CONCURRENCY": "600"})
    assert eb.eval_env(es, "terminus", {}, concurrency=48)["SWE_ROLLOUT_CONCURRENCY"] == "48"
    with pytest.raises(SystemExit, match="1 to 128"):
        eb.eval_env(es, "terminus", {}, concurrency=256)
    with pytest.raises(SystemExit, match="measured under terminus"):
        eb.eval_env(es, "miniswe", {})


def test_eval_env_takes_a_release_mix_against_its_release_manifest(tmp_path):
    rel = tmp_path / "releases" / "abc"
    mix = _jsonl(rel / "mix.jsonl", [_row("a")])
    (rel / "manifest.json").write_text(json.dumps({"release_sha256": "abc", "files": {"mix.jsonl": eb.layout.sha256_file(mix)}}))
    assert eb.eval_env(mix, "miniswe", {})["TMAX_AGENT"] == "miniswe"
    mix.write_text(mix.read_text() + "\n")
    with pytest.raises(SystemExit, match="no longer matches"):
        eb.eval_env(mix, "miniswe", {})
    with pytest.raises(SystemExit, match="--base"):
        eb.run_eval(mix, "base", "0,1", "terminus", None)


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


def test_package_install_rewrite_classifiers_are_diff_based(tmp_path):
    old = _pkg(tmp_path / "r0", test="true", instruction="solve the task", dockerfile="FROM x\nRUN pip install torch==2.1")
    new = _pkg(tmp_path / "r1", test="true", instruction="solve the task\nInstall uv and download foo==1.2", dockerfile="FROM x\nRUN pip install torch==2.2")
    d = eb.diff_stats(old, new, "")
    assert d["instruction_adds_install_guidance"]
    assert d["dockerfile_removes_or_changes_install"]
    kept = _pkg(tmp_path / "r2", test="true", instruction="solve the task", dockerfile="FROM x\nRUN pip install torch==2.1")
    assert not eb.instruction_adds_install_guidance(old.joinpath("instruction.md").read_text(), kept.joinpath("instruction.md").read_text())
    assert not eb.dockerfile_removes_or_changes_install(old.joinpath("environment/Dockerfile").read_text(), kept.joinpath("environment/Dockerfile").read_text())


def test_install_metrics_read_student_keystrokes_and_vanillux_commands(tmp_path):
    es_before = _jsonl(tmp_path / "before.jsonl", [_row("a")])
    es_after = _jsonl(tmp_path / "after.jsonl", [_row("a", rev=1)])

    def make(name, evalset, commands, output, rewards):
        ev = tmp_path / "evals" / name
        rec = ev / "validation_rollouts" / "a" / "g-1-r0.jsonl"
        rec.parent.mkdir(parents=True)
        rec.write_text(json.dumps({"task": "a", "reward": rewards[0]}) + "\n" +
                       json.dumps({"turn": 1, "keystrokes": commands, "output": output}) + "\n")
        idx = [{"task": "a", "state": "PASS" if rewards[0] else "FAIL", "infra_failed": False,
                "finish_reason": "submit", "record": "validation_rollouts/a/g-1-r0.jsonl"}]
        (ev / eb.INDEX).parent.mkdir(parents=True)
        (ev / eb.INDEX).write_text(json.dumps(idx))
        (ev / "launch.json").write_text(json.dumps(_launch(evalset)))
        return ev

    before = make("before-install", es_before, ["echo no install"], "ok", [1.0])
    after = make("after-install", es_after, ["python -m pip install torch\npip install wheel", "tmux send-keys pip install fake"], "No space left on device", [0.0])
    a = eb.read_eval(after)
    b = eb.read_eval(before)
    out = eb.install_metrics([a], [b], [{"task": "a", "job": "harder", "class": "accepted",
                                        "diff": {"instruction_adds_install_guidance": True}}])
    assert out["before"]["install_share"] == 0.0
    assert out["after"]["install_share"] == 1.0
    assert out["after"]["median_install_commands"] == 2
    assert out["after"]["disk_full_share"] == 1.0
    assert out["directions"]["harder"]["delta"]["install_share"] == 1.0


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
    assert f["infra_lost_attempts"] == {"t3": ["infra:codex_exit_nonzero", "infra:interrupted"]}
    assert f["acceptance_rate"] == 0.5  # infra losses are not verdicts
    assert out["directions"]["harder"]["mixed"] == 1
    assert out["rewrite_stats"]["harder"]["grader_network"] == {"introduced": 0, "kept": 0, "removed": 1}
    rep = out["repair"]
    assert rep["external_network"]["fixed_mechanical"] == 1
    assert rep["external_network"]["ground_truth"]["agree"] == 1
    assert rep["grader_fetch"]["tasks"] == 1
    _, md = eb.compare([("x", out)], allow_mismatch=False)
    assert "| x | eval / terminus / train-2026-10-25 | 1 / 3 |" in md


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
    out = eb.freeze(bench=bench, name="s1", run=None, evals=[ev], first_group=[], selected=None, release=None, mix=mix,
                    sources=[src], log=eb.Log(tmp_path / "f.log"))
    m, sha = eb.load_set(out, verify=True)
    assert m["directions"] == {"harder": 1, "easier": 1}
    assert m["dropped"] == {"c": "mixed"} and m["student_harness"] == "terminus"
    assert m["measured"]["a"]["source"]["kind"] == "eval" and m["measured"]["a"]["bucket"] == "all_solved"
    sig = json.load(open(out / "runs" / "s1" / "signals" / "a--g-1.json"))
    assert sig["direction"] == "harder" and sig["rev"] == 0 and len(sig["attempts"]) == 5
    assert (out / "runs" / "s1" / sig["attempts"][0]).exists()
    # A changed package is caught before an arm runs on it.
    (src / "tasks" / "a" / "instruction.md").write_text("changed")
    with pytest.raises(SystemExit, match="pinned items changed"):
        eb.load_set(out, verify=True)
    with pytest.raises(SystemExit, match="frozen once"):
        eb.freeze(bench=bench, name="s1", run=None, evals=[ev], first_group=[], selected=None, release=None, mix=mix,
                  sources=[src], log=eb.Log(tmp_path / "f.log"))


def test_freeze_leaves_out_a_package_that_is_not_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "_bench_commit", lambda: "test")
    src = tmp_path / "corpus"
    _pkg(src / "tasks" / "a", test="other")
    mix = _jsonl(tmp_path / "mix.jsonl", [_row("a", test="mine")])
    ev = _eval(tmp_path, "base", mix, {"a": "PPPPP"})
    out = eb.freeze(bench=tmp_path / "bench", name="s", run=None, evals=[ev], first_group=[], selected=None, release=None,
                    mix=mix, sources=[src], log=eb.Log(tmp_path / "f.log"))
    m, _ = eb.load_set(out, verify=True)
    assert m["dropped"] == {"a": "package_not_row"} and m["tasks"] == 0


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


def test_audit_bundle_union_keeps_single_arm_tasks(tmp_path):
    arms = []
    for label, tasks in (("x", ("t1", "only-x")), ("y", ("t1", "only-y"))):
        root = tmp_path / label
        for task in tasks:
            _pkg(root / "evolution" / "tasks" / task / "r0", test="true")
            _pkg(root / "evolution" / "tasks" / task / "r1", test=f"check {label}")
            d = root / "evolution" / "tasks" / task / "rewrites" / "20261024-000000Z--harder"
            d.mkdir(parents=True)
            (d / "rewrite.json").write_text(json.dumps({"task": task, "job": "harder", "status": "accepted",
                                                         "input_rev": 0, "result_rev": 1}))
        arms.append((label, root))
    out, key = tmp_path / "audit", tmp_path / "key.json"
    eb.audit_bundle(arms=arms, out=out, key=key, sample=None, seed=0, union=True, log=eb.Log(tmp_path / "a.log"))
    got = json.loads(key.read_text())["tasks"]
    assert sorted(got) == ["only-x", "only-y", "t1"]
    assert sorted(got["only-x"].values()) == ["x"]
    assert sorted(got["only-y"].values()) == ["y"]


def test_audit_argv_and_env_are_clean_and_read_only(tmp_path, monkeypatch):
    prompt = tmp_path / "INSTRUCTIONS.md"
    argv = eb._audit_argv("us.anthropic.claude-audit", prompt)
    assert argv[0:4] == ["claude", "-p", "--bare", "--restricted"]
    assert argv[argv.index("--system-prompt-file") + 1] == str(prompt)
    assert argv[argv.index("--tools") + 1:argv.index("--output-format")] == ["Read", "Grep", "Glob"]
    assert "Bash" not in argv and "Write" not in argv and "WebFetch" not in argv and "WebSearch" not in argv
    monkeypatch.setattr(eb.shutil, "which", lambda name: "/caller/bin/claude")
    env = eb._audit_env({"AWS_BEARER_TOKEN_BEDROCK": "secret"}, "us-east-1", tmp_path / "home")
    assert env["HOME"] == str(tmp_path / "home")
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "home" / "claude-config")
    assert set(env) == {"PATH", "HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION",
                        "AWS_BEARER_TOKEN_BEDROCK"}
    assert "/Users/andyl/.claude" not in env.values()


def _release(tmp: Path, tasks: list[str]) -> Path:
    rel = tmp / "releases" / "r1"
    for t in tasks:
        _pkg(rel / "sources" / "src-a" / "tasks" / t, test=f"test {t}")
    mix = _jsonl(rel / "mix.jsonl", [_row(t, test=f"test {t}") for t in tasks])
    (rel / "config.json").write_text(json.dumps({"seed": 1, "sources": [{"name": "src-a", "exclude": ["x"]}]}))
    (rel / "manifest.json").write_text(json.dumps({"release_sha256": "r1", "files": {"mix.jsonl": eb.layout.sha256_file(mix)}}))
    return rel


def _record(d: Path, task: str, g: int, i: int, reward: float) -> None:
    p = d / "validation_rollouts" / task / f"g-{g}-r{i}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"task": task, "reward": reward, "agent": "vanillux"}) + "\n")


def test_stage_streams_signals_then_freezes_from_the_index(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "_bench_commit", lambda: "test")
    rel = _release(tmp_path, ["a", "b", "c"])
    ev = tmp_path / "bench" / "evals" / "base"
    att = Path(str(ev) + "--attempt-1")
    ev.mkdir(parents=True)
    launch = _launch(rel / "mix.jsonl")
    launch["env"]["TMAX_AGENT"] = "vanillux"
    (ev / "launch.json").write_text(json.dumps(launch))
    bench = tmp_path / "bench"
    sd = eb._stream_dir(bench, "s")
    run = sd / "runs" / "s"
    (run / "signals").mkdir(parents=True)
    eb.layout.write_json_atomic(sd / "stream.json", {"k": 5, "student_harness": "vanillux", "decided": {},
                                                    "selected": {}, "package_not_row": []})
    root = tmp_path / "root"
    root.mkdir()
    (root / "bench_arm.json").write_text(json.dumps({"set_manifest_sha256": None}))
    # first poll: a has all 5 (all solved), b only 3
    for i in range(5):
        _record(att, "a", 1, i, 1.0)
    for i in range(3):
        _record(att, "b", 2, i, 0.0)
    seen = []

    def finish_eval(_sec):
        seen.append(json.load(open(sd / "selected.json")))
        for i in range(3, 5):
            _record(att, "b", 2, i, 0.0)
        for i, r in enumerate((1.0, 0.0, 1.0, 0.0, 0.0)):
            _record(att, "c", 3, i, r)
        index = [{"task": t, "state": "PASS" if r >= 1 else "FAIL", "infra_failed": False, "finish_reason": "submit",
                  "record": f"validation_rollouts/{t}/g-{g}-r{i}.jsonl"}
                 for t, g, rs in (("a", 1, [1.0] * 5), ("b", 2, [0.0] * 5), ("c", 3, (1.0, 0.0, 1.0, 0.0, 0.0)))
                 for i, r in enumerate(rs)]
        (ev / eb.INDEX).parent.mkdir(parents=True, exist_ok=True)
        (ev / eb.INDEX).write_text(json.dumps(index))

    monkeypatch.setattr(eb.time, "sleep", finish_eval)
    eb.stage(bench=bench, name="s", eval_dir=ev, release=rel, root=root, poll_sec=1, log=eb.Log(tmp_path / "s.log"))
    assert seen == [{"a": {"group": -1}}]  # a was staged while b was still running
    st = json.load(open(sd / "stream.json"))
    assert st["decided"] == {"a": "all_solved", "b": "none_solved", "c": "mixed"}
    assert st["final_check"]["bucket_mismatch"] == {} and st["final_check"]["set_not_staged"] == []
    assert json.load(open(sd / "selected.json")) == {"a": {"group": -1}, "b": {"group": -2}}
    assert (sd / "done").exists()
    m, sha = eb.load_set(bench / "sets" / "s", verify=True)
    assert m["dropped"] == {"c": "mixed"} and m["student_harness"] == "vanillux"
    assert m["release"]["config"]["sources"][0]["exclude"] == ["x"]
    assert json.load(open(root / "bench_arm.json"))["set_manifest_sha256"] == sha
    sig = json.load(open(run / "signals" / "b--g-2.json"))
    assert sig["direction"] == "easier" and all((run / a).exists() for a in sig["attempts"])


def test_cut_wave_takes_new_accepted_revisions_only(tmp_path, monkeypatch):
    root = tmp_path / "root"
    sd = tmp_path / "s.stream"
    sd.mkdir()
    (sd / "selected.json").write_text(json.dumps({"a": {}, "b": {}, "c": {}}))
    (sd / "stream.json").write_text(json.dumps({"student_harness": "vanillux"}))
    root.mkdir()
    (root / "bench_arm.json").write_text(json.dumps({"stream": str(sd), "set": "s", "set_dir": str(tmp_path / "sets" / "s"),
                                                    "arm": {"name": "x"}}))
    mix = eb.layout.Root(root).mix
    mix.publish([json.dumps(_row("a", 1)), json.dumps(_row("b", 0)), json.dumps(_row("c", 0)), json.dumps(_row("d", 2))])
    log = eb.Log(tmp_path / "w.log")
    assert eb.cut_wave(root=root, every=2, minutes=600, log=log) is None  # one new, not yet due
    w1 = eb.cut_wave(root=root, every=1, minutes=600, log=log)
    assert json.load(open(w1.with_name(w1.stem + ".manifest.json")))["labels"] == ["a"]
    mix.publish([json.dumps(_row("a", 1)), json.dumps(_row("b", 1)), json.dumps(_row("c", 1)), json.dumps(_row("d", 2))])
    w2 = eb.cut_wave(root=root, every=2, minutes=600, log=log)
    m2 = json.load(open(w2.with_name(w2.stem + ".manifest.json")))
    assert w2.name == "bench-wave-02.jsonl" and m2["labels"] == ["b", "c"] and m2["student_harness"] == "vanillux"
    # a wave's own manifest names the harness before the set exists
    assert eb.eval_env(w2, "vanillux", {})["TMAX_AGENT"] == "vanillux"
    with pytest.raises(SystemExit, match="measured under vanillux"):
        eb.eval_env(w2, "terminus", {})


def test_freeze_takes_the_first_measurement_of_each_row_and_drops_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(eb, "_bench_commit", lambda: "test")
    src = tmp_path / "corpus"
    tasks = ["r1", "c1", "c2", "c3", "c4", "c5"]
    rows = [_row(t, test=f"test {t}") for t in tasks]
    for r in rows:
        _pkg(src / "tasks" / r["label"], test=r["metadata"]["tmax"]["test_sh"])
    mix = _jsonl(tmp_path / "mix.jsonl", rows)
    ev = _as_legacy(_eval(tmp_path, "base", mix, {"r1": "FFFFF"}))  # the repair row, from an eval
    run = tmp_path / "train" / "runs" / "sep"
    (run / "rollouts").mkdir(parents=True)
    (run / "launch.json").write_text(json.dumps({"tt_commit": "old1234", "env": {"TMAX_AGENT": "terminus"}}))
    trained = [_row(t, test=f"test {t}") for t in ("c1", "c2", "c3", "c4")] + [_row("c5", test="older grader")]
    _jsonl(run / "inputs" / "mix.jsonl", trained)

    def group(t, g, rewards, rev=0):
        for i, r in enumerate(rewards):
            p = run / "rollouts" / t / f"g{g}-r{i}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            head = {"task": t, "rev": rev, "group": g, "reward": r, "infra_failed": r is None}
            p.write_text(json.dumps(head) + "\n")

    # c1: first group's first 5 graded all solved (an infra attempt skipped), later attempts fail
    group("c1", 7, [1.0, None, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0])
    group("c1", 40, [0.0] * 8)  # a later group is not the before
    group("c2", 9, [0.0] * 12)  # none solved
    group("c3", 3, [1.0, 0.0, 1.0, 0.0, 0.0, 0.0])  # mixed: already carries gradient, out
    group("c5", 2, [0.0] * 6)  # measured on another test_sh, out
    with pytest.raises(SystemExit, match="ran the legacy-2400 recipe"):
        eb.freeze(bench=tmp_path / "bench", name="m", run=None, evals=[ev], first_group=[run], selected=None,
                  release=None, mix=mix, sources=[src], log=eb.Log(tmp_path / "f.log"))
    out = eb.freeze(bench=tmp_path / "bench", name="m", run=None, evals=[ev], first_group=[run], selected=None,
                    recipe="legacy-2400", release=None, mix=mix, sources=[src], log=eb.Log(tmp_path / "f.log"))
    m, _ = eb.load_set(out, verify=True)
    assert m["directions"] == {"easier": 2, "harder": 1}
    assert m["dropped"] == {"c3": "mixed", "c4": "not_measured", "c5": "measured_other_test_sh"}
    assert m["measured"]["c1"]["source"] == {"kind": "run_first_group", "dir": str(run.resolve()), "tt_commit": "old1234",
                                             "harness": "terminus", "k": 5, "group_size": 8}
    assert (m["measured"]["c1"]["solved"], m["measured"]["c1"]["graded"], m["measured"]["c1"]["group"]) == (5, 5, 7)
    assert m["measured"]["r1"]["source"]["kind"] == "eval"
    sig = json.load(open(out / "runs" / "m" / "signals" / "c1--g7.json"))
    assert sig["direction"] == "harder" and len(sig["attempts"]) == 8  # the whole group is staged

    # the tally reads the before-bands from the set when no before eval is given
    root = tmp_path / "root"
    _pkg(root / "evolution" / "tasks" / "c1" / "r0", test="test c1")
    _pkg(root / "evolution" / "tasks" / "c1" / "r1", test="test c1 harder")
    d = root / "evolution" / "tasks" / "c1" / "rewrites" / "20261025-000000Z--harder"
    d.mkdir(parents=True)
    (d / "rewrite.json").write_text(json.dumps({"task": "c1", "job": "harder", "status": "accepted", "input_rev": 0,
                                                "result_rev": 1}))
    _, sha = eb.load_set(out, verify=False)
    (root / "bench_arm.json").write_text(json.dumps({"set_dir": str(out), "set_manifest_sha256": sha}))
    w1 = _jsonl(tmp_path / "w1.jsonl", [_row("c1", 1)])
    after = _as_legacy(_eval(tmp_path, "wave1", w1, {"c1": "PFPFF"}))
    res = eb.tally(evals=[after], runs=[], evalset=None, root=root, set_dir=None, before=[], labels=None,
                   log=eb.Log(tmp_path / "t.log"))
    assert res["before_after"]["table"] == {"all_solved->mixed": 1}
    assert res["student_recipe"] == "legacy-2400" and res["legacy_recipe"]
    # an after-eval of another recipe than the set's needs before-evals of its own recipe
    after_new = _eval(tmp_path, "wave1-new", w1, {"c1": "PFPFF"})
    with pytest.raises(SystemExit, match="give --before"):
        eb.tally(evals=[after_new], runs=[], evalset=None, root=root, set_dir=None, before=[], labels=None,
                 log=eb.Log(tmp_path / "t.log"))
    orig = _jsonl(tmp_path / "orig.jsonl", [_row("c1", test="test c1")])
    with pytest.raises(SystemExit, match="another recipe"):
        eb.tally(evals=[after_new], runs=[], evalset=None, root=root, set_dir=None,
                 before=[_as_legacy(_eval(tmp_path, "before-old", orig, {"c1": "PPPPP"}))], labels=None,
                 log=eb.Log(tmp_path / "t.log"))
    res = eb.tally(evals=[after_new], runs=[], evalset=None, root=root, set_dir=None,
                   before=[_eval(tmp_path, "before-new", orig, {"c1": "PPPPPPPPPPPPPPPP"})], labels=None,
                   log=eb.Log(tmp_path / "t.log"))
    assert res["before_after"]["table"] == {"all_solved->mixed": 1}
    assert res["student_recipe"] == "train-2026-10-25" and not res["legacy_recipe"]
    assert res["inputs"]["set"]["dropped"] == {"mixed": 1, "not_measured": 1, "measured_other_test_sh": 1}


def test_recipes_cap_context_inside_the_model_len_and_compare_keeps_them_apart(tmp_path):
    for r in eb.STUDENT_RECIPES.values():
        model_len = int(r.get("SWE_TMAX_CONTEXT", "65536"))  # config_registry's default context
        assert int(r["SWE_MAX_CONTEXT_LEN"]) == model_len - 2048 and model_len % 32 == 0
    es = _jsonl(tmp_path / "s.jsonl", [_row("a")])
    new = eb.read_eval(_eval(tmp_path, "new", es, {"a": "PF"}))
    legacy_launch = _launch(es)
    legacy_launch["env"] = {**eb.STUDENT_RECIPES["legacy-2400"], "SWE_BENCH_RECIPE": "legacy-2400",
                            "TMAX_AGENT": "terminus", "SWE_TB2_DATA": str(es)}
    old_dir = _eval(tmp_path, "old", es, {"a": "PF"})
    (old_dir / "launch.json").write_text(json.dumps(legacy_launch))
    mm = eb.recipe_mismatch(new["recipe"], eb.read_eval(old_dir)["recipe"])
    assert {"student_recipe", "SWE_TMAX_CONTEXT", "SWE_MAX_CONTEXT_LEN", "SWE_EVAL_TIME_BUDGET_SEC"} <= set(mm)


def test_mechanical_fix_says_nothing_about_classes_it_cannot_check():
    acc = {"has_solution": True, "hosts_after": ["github.com"]}
    assert eb.mechanical_fix("hf:INSTR-VERIFIER-MISMATCH", None) is None
    assert eb.mechanical_fix("audit_20261024_broken", acc) is None
    assert eb.mechanical_fix("no_reference_solution", None) is False
    assert eb.mechanical_fix("no_reference_solution", acc) is True
    assert eb.mechanical_fix("grader_fetch", acc) is False


def test_overshoot_pairs_counts_direction_aware_overshoot():
    def res(bands):
        return {"rewrites": [{"task": t, "class": "accepted", "job": j, "after_band": b} for t, (j, b) in bands.items()]}
    a = res({"t1": ("harder", "none_solved"), "t2": ("easier", "mixed"), "t3": ("easier", "all_solved")})
    b = res({"t1": ("harder", "mixed"), "t2": ("easier", "all_solved"), "t3": ("easier", "all_solved")})
    o = eb.overshoot_pairs(a, b, ["t1", "t2", "t3"])
    assert (o["tasks"], o["overshoot"], o["gained"], o["lost"]) == (3, [2, 2], 1, 1)


def test_shard_evalset_splits_disjointly_and_never_writes_into_a_set(tmp_path):
    set_dir = tmp_path / "sets" / "s"
    set_dir.mkdir(parents=True)
    (set_dir / "manifest.json").write_text(json.dumps({"student_harness": "terminus"}))
    orig = set_dir / "original.jsonl"
    eb._write_evalset(orig, [_row(t) for t in "abcdefg"], set="s", set_dir=str(set_dir))
    before = sorted(p.name for p in set_dir.iterdir())
    shards = [eb.shard_evalset(orig, f"{i}/3", tmp_path / "bench") for i in (1, 2, 3)]
    assert sorted(p.name for p in set_dir.iterdir()) == before
    labels = [json.load(open(p.with_name(p.stem + ".manifest.json")))["labels"] for p in shards]
    assert labels == [["a", "d", "g"], ["b", "e"], ["c", "f"]]
    assert shards[0] == tmp_path / "bench" / "evalsets" / "s" / "original.shard-01-of-03.jsonl"
    m = json.load(open(shards[0].with_name(shards[0].stem + ".manifest.json")))
    assert m["set_dir"] == str(set_dir) and m["shard"] == [1, 3]
    assert eb.eval_env(shards[0], "terminus", {})["SWE_TB2_DATA"] == str(shards[0])
    assert eb.shard_evalset(orig, "1/3", tmp_path / "bench") == shards[0]  # asking again reuses it
    shards[0].write_text(shards[0].read_text() + "\n")
    with pytest.raises(SystemExit, match="is not shard"):
        eb.shard_evalset(orig, "1/3", tmp_path / "bench")
    with pytest.raises(SystemExit, match="1 <= i <= n"):
        eb.shard_evalset(orig, "4/3", tmp_path / "bench")
    # an arm's evalset gets its shards beside it
    acc = tmp_path / "root" / "data" / "evalsets" / "bench-accepted.jsonl"
    eb._write_evalset(acc, [_row("a", 1), _row("b", 2)], set_dir=str(set_dir))
    assert eb.shard_evalset(acc, "2/2", tmp_path / "root").parent == acc.parent


def test_tally_merges_shards_and_refuses_a_shard_of_another_recipe(tmp_path):
    es1 = _jsonl(tmp_path / "s1.jsonl", [_row("a")])
    es2 = _jsonl(tmp_path / "s2.jsonl", [_row("b")])
    e1 = _eval(tmp_path, "s1", es1, {"a": "PF"})
    e2 = _eval(tmp_path, "s2", es2, {"b": "PP"})
    out = eb.tally(evals=[e1, e2], runs=[], evalset=None, root=None, set_dir=None, before=[], labels=None,
                   log=eb.Log(tmp_path / "t.log"))
    assert (out["after"]["mixed"], out["after"]["all_solved"]) == (1, 1)
    with pytest.raises(SystemExit, match="another recipe"):
        eb.tally(evals=[e1, _as_legacy(e2)], runs=[], evalset=None, root=None, set_dir=None, before=[],
                 labels=None, log=eb.Log(tmp_path / "t.log"))


def test_salvage_keeps_finished_tasks_and_writes_the_rest(tmp_path):
    set_dir = tmp_path / "sets" / "s"
    set_dir.mkdir(parents=True)
    (set_dir / "manifest.json").write_text(json.dumps({"student_harness": "terminus"}))
    es = tmp_path / "evalsets" / "x.jsonl"
    eb._write_evalset(es, [_row(t) for t in "abc"], set_dir=str(set_dir))
    ev = _eval(tmp_path, "stopped", es, {"a": "PF", "b": "FF", "c": "P"}, attempt_dir=True)
    launch = json.load(open(ev / "launch.json"))
    launch["env"]["SWE_TB2_VAL_K"] = "2"
    (ev / "launch.json").write_text(json.dumps(launch))
    with pytest.raises(SystemExit, match="own index"):
        eb.salvage(ev, eb.Log(tmp_path / "s.log"))
    (ev / eb.INDEX).unlink()  # stopped before the controller wrote it
    rest = eb.salvage(ev, eb.Log(tmp_path / "s.log"))
    assert json.load(open(rest.with_name(rest.stem + ".manifest.json")))["labels"] == ["c"]
    e = eb.read_eval(ev)
    assert e["salvaged"] and sorted(e["tasks"]) == ["a", "b"]
    assert (e["tasks"]["a"]["bucket"], e["tasks"]["b"]["bucket"]) == ("mixed", "none_solved")
    assert eb.eval_env(rest, "terminus", {})["SWE_TB2_DATA"] == str(rest)
    # the rest, scored by another eval, merges with the salvaged one
    rest_ev = _eval(tmp_path, "rest", rest, {"c": "PP"})
    rl = json.load(open(rest_ev / "launch.json"))
    rl["env"]["SWE_TB2_VAL_K"] = "2"
    (rest_ev / "launch.json").write_text(json.dumps(rl))
    assert sorted(eb.merge_evals([e, eb.read_eval(rest_ev)])) == ["a", "b", "c"]
    with pytest.raises(SystemExit, match="exists"):
        (ev / eb.SALVAGED).unlink()
        eb.salvage(ev, eb.Log(tmp_path / "s.log"))


def test_rest_evalset_is_what_the_done_evals_did_not_score(tmp_path):
    set_dir = tmp_path / "sets" / "s"
    set_dir.mkdir(parents=True)
    (set_dir / "manifest.json").write_text(json.dumps({"student_harness": "terminus"}))
    es = tmp_path / "evalsets" / "acc.jsonl"
    eb._write_evalset(es, [_row(t, 1) for t in "abcd"], set_dir=str(set_dir))
    s1 = eb.shard_evalset(es, "1/2", tmp_path)
    ev = _eval(tmp_path, "s1", s1, {"a": "PF", "c": "FF"})
    rest = eb.rest_evalset(es, [ev], eb.Log(tmp_path / "r.log"))
    assert json.load(open(rest.with_name(rest.stem + ".manifest.json")))["labels"] == ["b", "d"]
    assert eb.eval_env(rest, "terminus", {})["SWE_TB2_DATA"] == str(rest)
    with pytest.raises(SystemExit, match="exists"):
        eb.rest_evalset(es, [ev], eb.Log(tmp_path / "r.log"))
