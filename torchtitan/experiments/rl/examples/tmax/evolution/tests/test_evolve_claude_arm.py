# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The claude arm: same session contract as codex, different CLI.

The arm exists so a rewrite can run on a Claude Code subscription rather than
an API key, so the things worth pinning are the ones that decide *which*
credentials a session spends and whether a resumed session finds its thread.
"""

import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve as ev
import evolve_codex as ec
from torchtitan.experiments.rl.examples.tmax import layout


@pytest.fixture
def claude(monkeypatch, tmp_path):
    """A root whose bin/ holds a claude, with the arm selected and a login."""
    root = layout.Root(tmp_path / "root")
    root.bin.mkdir(parents=True)
    (root.bin / "claude").write_text("#!/bin/sh\nexit 0\n")
    (root.bin / "claude").chmod(0o755)
    account = tmp_path / "account"
    account.mkdir()
    (account / ".credentials.json").write_text('{"token": "test"}')
    monkeypatch.setenv("TRL_BASE", str(root.path))
    monkeypatch.setenv("EVOLVE_CLAUDE_ACCOUNT_HOME", str(account))
    monkeypatch.setattr(ec, "EVOLVE_AGENT", "claude")
    return root


def test_default_agent_follows_the_arm_name(monkeypatch):
    """One name selects both the arm and the CLI; EVOLVE_AGENT still wins."""
    monkeypatch.delenv("EVOLVE_AGENT", raising=False)
    monkeypatch.delenv("SWE_RETUNE_AGENT", raising=False)
    assert ec._default_agent() == "codex"
    monkeypatch.setenv("SWE_RETUNE_AGENT", "claude")
    assert ec._default_agent() == "claude"
    monkeypatch.setenv("SWE_RETUNE_AGENT", "codex")
    assert ec._default_agent() == "codex"
    monkeypatch.setenv("EVOLVE_AGENT", "claude")
    assert ec._default_agent() == "claude"


def test_agent_bin_follows_the_arm(claude):
    assert ec._agent_bin().name == "claude"
    ec._require_codex()  # the binary is there; no raise


def test_unknown_agent_is_refused(monkeypatch, claude):
    monkeypatch.setattr(ec, "EVOLVE_AGENT", "gemini")
    with pytest.raises(ValueError, match="codex or claude"):
        ec._require_codex()


def test_missing_binary_names_the_arm(monkeypatch, tmp_path):
    root = layout.Root(tmp_path / "root")
    root.bin.mkdir(parents=True)
    monkeypatch.setenv("TRL_BASE", str(root.path))
    monkeypatch.setattr(ec, "EVOLVE_AGENT", "claude")
    with pytest.raises(RuntimeError, match="claude binary not found"):
        ec._require_codex()


def test_fresh_session_names_its_thread(claude):
    sid = str(uuid.uuid4())
    cmd = ec._claude_cmd(Path("/pkg"), sid, resume=False)
    assert cmd[0].endswith("/claude")
    assert "--session-id" in cmd and sid in cmd
    assert "--resume" not in cmd


def test_resume_continues_the_named_thread(claude):
    sid = str(uuid.uuid4())
    cmd = ec._claude_cmd(Path("/pkg"), sid, resume=True)
    assert cmd[cmd.index("--resume") + 1] == sid
    assert "--session-id" not in cmd


def test_session_runs_unattended_and_unconfigured(claude):
    """Nobody is at the terminal and no human's settings may leak in."""
    cmd = ec._claude_cmd(Path("/pkg"), str(uuid.uuid4()), resume=False)
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "-p" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"


def test_model_is_its_own_knob(monkeypatch, claude):
    """SYNTH_MODEL names an OpenAI model; the claude arm must not read it."""
    monkeypatch.setattr(ec, "CLAUDE_MODEL", "sonnet")
    cmd = ec._claude_cmd(Path("/pkg"), str(uuid.uuid4()), resume=False)
    assert cmd[cmd.index("--model") + 1] == "sonnet"


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_effort_reaches_both_clis_and_the_session_record(monkeypatch, claude, effort):
    monkeypatch.setattr(ec, "CODEX_EFFORT", effort)
    rw = layout.Root.from_env().evolution.task("effort").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as run:
        cmd = ec._claude_cmd(Path("/pkg"), run.meta["claude_session_id"], resume=False)
        assert cmd[cmd.index("--effort") + 1] == effort
        assert run.meta["reasoning_effort"] == effort
    assert f"model_reasoning_effort={effort}" in ec._codex_cmd(Path("/pkg"))


def test_env_is_private_and_spends_the_subscription(monkeypatch, claude, tmp_path):
    """An API key left in the environment would bill the API instead of the
    subscription this arm exists to use."""
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.delenv("EVOLVE_CLAUDE_USE_API_KEY", raising=False)
    home = tmp_path / "home"
    env = ec._claude_env(home)
    assert env["CLAUDE_CONFIG_DIR"] == str(home)
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_login_is_linked_into_the_private_home(monkeypatch, claude, tmp_path):
    """The login lives inside the config directory, so a private home without
    it answers "Not logged in" after spending a process. It is linked, not
    copied, so a token refresh writes through to the one file."""
    monkeypatch.delenv("EVOLVE_CLAUDE_USE_API_KEY", raising=False)
    home = tmp_path / "home"
    ec._claude_env(home)
    link = home / ".credentials.json"
    assert link.is_symlink()
    assert json.loads(link.read_text()) == {"token": "test"}


def test_a_missing_login_fails_before_spending_a_process(monkeypatch, claude, tmp_path):
    monkeypatch.setenv("EVOLVE_CLAUDE_ACCOUNT_HOME", str(tmp_path / "nowhere"))
    monkeypatch.delenv("EVOLVE_CLAUDE_USE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="no Claude Code login"):
        ec._claude_env(tmp_path / "home")


def test_api_key_is_opt_in(monkeypatch, claude, tmp_path):
    """Billing the API instead is allowed, but only when asked for: the key
    survives and no login is required."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("EVOLVE_CLAUDE_USE_API_KEY", "1")
    monkeypatch.setenv("EVOLVE_CLAUDE_ACCOUNT_HOME", str(tmp_path / "nowhere"))
    env = ec._claude_env(tmp_path / "home")
    assert env["ANTHROPIC_API_KEY"] == "anthropic-key"
    assert not (tmp_path / "home" / ".credentials.json").exists()


def test_record_says_which_cli_and_credentials(monkeypatch, claude):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rw = layout.Root.from_env().evolution.task("task-a").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as run:
        assert run.meta["agent"] == "claude"
        assert run.meta["authentication"] == "claude_subscription"
        assert run.meta["driver"] == "claude-print"
        uuid.UUID(run.meta["claude_session_id"])  # a real uuid, or this raises


def test_session_id_reads_the_record_back(claude):
    rw = layout.Root.from_env().evolution.task("task-b").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as run:
        sid = run.meta["claude_session_id"]
        sd = run.dir
    assert ec._session_id(sd) == sid


def test_resume_continues_in_the_home_holding_the_thread(claude):
    """Claude Code looks a conversation up inside CLAUDE_CONFIG_DIR, so a
    resuming session runs out of the home of the session it continues."""
    rw = layout.Root.from_env().evolution.task("task-c").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as first:
        first_home = first.dir.codex_home
    with ec.session(rw, "repair", timeout=10, resumes=first.dir) as second:
        assert second.meta["claude_config_dir"] == str(first_home)
        env = ec._claude_env(Path(second.meta["claude_config_dir"]))
        assert env["CLAUDE_CONFIG_DIR"] == str(first_home)


def test_three_repairs_resume_the_same_thread(claude):
    cli = claude.bin / "claude"
    cli.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "resuming = '--resume' in args\n"
        "flag = '--resume' if resuming else '--session-id'\n"
        "sid = args[args.index(flag) + 1]\n"
        "thread = Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects' / sid\n"
        "if resuming and not thread.is_file():\n"
        "    sys.exit('No conversation found')\n"
        "thread.parent.mkdir(exist_ok=True)\n"
        "with thread.open('a') as out:\n"
        "    out.write(sys.stdin.read() + '\\n')\n"
    )
    rw, pkg = _package(claude, "resume-chain")
    with ec.session(rw, "agent", timeout=10) as first:
        assert ec._run_codex(first, pkg, "initial").returncode == 0
        sid = ec._session_id(first.dir)
        home = first.dir.codex_home
    prior = first.dir
    for number in range(3):
        with ec.session(rw, f"repair-{number}", timeout=10, resumes=prior) as run:
            result = ec._run_codex(run, pkg, f"repair {number}", resume=ec._session_id(prior))
            assert result.returncode == 0, result.stderr
        prior = run.dir
    assert ec._session_id(prior) == sid
    assert json.loads(prior.meta.read_text())["claude_config_dir"] == str(home)
    transcript = (home / "projects" / sid).read_text()
    for text in ("initial", "repair 0", "repair 1", "repair 2"):
        assert text in transcript


def test_private_home_survives_the_session(claude):
    """The codex arm keeps only sessions/ and drops the rest as rebuildable
    cache. Claude Code keeps its transcript elsewhere under the config dir, so
    the same sweep would delete the thread a later --resume needs."""
    rw = layout.Root.from_env().evolution.task("task-d").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as run:
        home = run.dir.codex_home
        (home / "projects").mkdir()
        (home / "projects" / "thread.jsonl").write_text("{}\n")
    assert (home / "projects" / "thread.jsonl").exists()


def test_codex_arm_still_prunes(claude, monkeypatch):
    """The guard is on the claude arm only."""
    monkeypatch.setattr(ec, "EVOLVE_AGENT", "codex")
    rw = layout.Root.from_env().evolution.task("task-e").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as run:
        home = run.dir.codex_home
        (home / "cache").mkdir()
    assert not (home / "cache").exists()


def _package(root, name="task-p"):
    """A rewrite package the fake CLI can be pointed at."""
    rw = root.evolution.task(name).rewrite("harder")
    pkg = rw.package
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "AGENTS.md").write_text("# Role\nFix solution/solve.sh when it is wrong.\n")
    return rw, pkg


def test_role_reaches_the_model_in_the_prompt(claude):
    """Claude Code does not look for AGENTS.md, so the role has to be carried
    in rather than discovered -- otherwise the session runs with no role."""
    rw, pkg = _package(layout.Root.from_env())
    with ec.session(rw, "agent", timeout=30) as run:
        ec._run_codex(run, pkg, "JOB TEXT")
        sent = run.dir.prompt.read_text()
    assert sent.startswith("# Role")
    assert "JOB TEXT" in sent


def test_prompt_requests_direct_edits_and_readback(claude):
    rw, pkg = _package(layout.Root.from_env(), "task-q")
    with ec.session(rw, "agent", timeout=30) as run:
        ec._run_codex(run, pkg, "JOB TEXT")
        sent = run.dir.prompt.read_text()
    assert "Edit files in this package directly" in sent
    assert "Read back files you create" in sent


def test_recorded_solution_instructions_reach_the_claude_arm(claude):
    """A longlongcheck package carries gpt6_actions.json instead of solve.sh.
    The prompt rewrite that explains the action-list format lives on the path
    both arms share, so the claude arm must get it too."""
    rw, pkg = _package(layout.Root.from_env(), "task-r")
    (pkg / "solution").mkdir(exist_ok=True)
    (pkg / ev.ACTION_PATH).write_text("[]")
    with ec.session(rw, "agent", timeout=30) as run:
        ec._run_codex(run, pkg, "JOB TEXT")
        sent = run.dir.prompt.read_text()
    assert "recorded terminal actions" in sent
    assert "completion_marker" in sent
    # The role travelled too, with its solve.sh mentions rewritten.
    assert "solution/solve.sh" not in sent
    assert ev.ACTION_PATH in sent


def test_meta_lands_on_disk_for_a_reader(claude):
    """session.json is the record a later resume and a human both read."""
    rw = layout.Root.from_env().evolution.task("task-f").rewrite("harder")
    with ec.session(rw, "agent", timeout=10) as run:
        sd = run.dir
    on_disk = json.loads(sd.meta.read_text())
    assert on_disk["agent"] == "claude"
    assert on_disk["status"] == "completed"
    assert on_disk["claude_session_id"]


def test_retune_arm_accepts_only_agent_clis(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import feedback_loop as fb

    monkeypatch.delenv("SWE_RETUNE_AGENT", raising=False)
    assert fb.retune_arm() == "codex"
    for arm in ("codex", "claude"):
        monkeypatch.setenv("SWE_RETUNE_AGENT", arm)
        assert fb.retune_arm() == arm
    monkeypatch.setenv("SWE_RETUNE_AGENT", "chat")
    with pytest.raises(ValueError, match="SWE_RETUNE_AGENT must be"):
        fb.retune_arm()


def test_claude_arm_attaches_student_feedback(monkeypatch, tmp_path):
    """A harder signal on the claude arm carries the student's measurement.

    The agent reads the measurement from
    run/student_feedback.json. The loop is what attaches it to the signal.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import evolve_ondella as eo

    monkeypatch.setenv("SWE_RETUNE_AGENT", "claude")
    signal = {
        "task": "task_x",
        "run": "run_x",
        "group": 1,
        "rev": 0,
        "solved": 12,
        "total": 12,
        "direction": "harder",
    }
    task = layout.Root(tmp_path / "root").evolution.task("task_x")
    eo._attach_student_feedback(signal, task, job="harder", rev=0)
    assert signal["student_feedback"]["measurement"]["solved"] == 12
    assert signal["student_feedback"]["measurement"]["total"] == 12


def test_claude_usage_read_from_the_result_object(claude, tmp_path):
    """A session records what it spent, since synth_client never sees it.

    The `usage` on a rewrite comes from the chat client, which an agentic arm
    does not call, so without this a run cannot say what a rewrite cost.
    """
    sd = type("SD", (), {"stdout": tmp_path / "stdout.txt"})()
    sd.stdout.write_text(
        json.dumps(
            {
                "total_cost_usd": 1.0418358,
                "num_turns": 19,
                "duration_ms": 1096995,
                "usage": {
                    "input_tokens": 34,
                    "output_tokens": 19181,
                    "cache_read_input_tokens": 928959,
                    "cache_creation_input_tokens": 7213,
                },
            }
        )
    )
    u = ec._claude_usage(sd)
    assert u["cost_usd"] == pytest.approx(1.0418358)
    assert u["turns"] == 19
    assert u["output_tokens"] == 19181
    assert u["cache_read_input_tokens"] == 928959


@pytest.mark.parametrize(
    "body", ["", "not json", "[]", json.dumps({"type": "other"})]
)
def test_claude_usage_survives_an_unusable_stream(claude, tmp_path, body):
    """A session that already ran must not fail over its own accounting."""
    sd = type("SD", (), {"stdout": tmp_path / "stdout.txt"})()
    sd.stdout.write_text(body)
    assert ec._claude_usage(sd) is None


def test_claude_usage_absent_stream_is_none(claude, tmp_path):
    sd = type("SD", (), {"stdout": tmp_path / "missing.txt"})()
    assert ec._claude_usage(sd) is None
