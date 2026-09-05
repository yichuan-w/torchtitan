"""The watcher decides what to say to a live session, and says it once."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import codex_session as cs


def _pkg(tmp_path, *, seed_lines=9, seed_asserts=6, solve, verifier,
         instruction="Write the report.\n", seed_literals=()):
    pkg = tmp_path / "pkg"
    for d in ("run", "solution", "tests", "environment"):
        (pkg / d).mkdir(parents=True, exist_ok=True)
    (pkg / "instruction.md").write_text(instruction)
    (pkg / "environment" / "Dockerfile").write_text("FROM scratch\n")
    (pkg / "solution" / "solve.sh").write_text(solve)
    (pkg / "tests" / "test_state.py").write_text(verifier)
    (pkg / "run" / "seed_size.json").write_text(json.dumps(
        {"solution_lines": seed_lines, "verifier_asserts": seed_asserts}))
    (pkg / "run" / "seed_literals.json").write_text(json.dumps(list(seed_literals)))
    return pkg


def test_says_nothing_while_the_rewrite_is_within_a_rung(tmp_path) -> None:
    pkg = _pkg(tmp_path, solve="\n".join(f"step {i}" for i in range(13)) + "\n",
               verifier="assert one\nassert two\n")
    assert cs.PackageWatcher(pkg).check() == []


def test_says_nothing_before_the_agent_has_added_anything(tmp_path) -> None:
    # A session starts at the seed's size; "not yet 3 lines above" is not a
    # reason to interrupt.
    pkg = _pkg(tmp_path, solve="\n".join(f"step {i}" for i in range(9)) + "\n",
               verifier="assert one\n")
    assert cs.PackageWatcher(pkg).check() == []


def test_speaks_when_the_rewrite_overshoots_the_rung(tmp_path) -> None:
    pkg = _pkg(tmp_path, solve="\n".join(f"step {i}" for i in range(40)) + "\n",
               verifier="assert one\n")
    w = cs.PackageWatcher(pkg)
    said = w.check()
    assert len(said) == 1 and "one rung" in said[0] and "at most 8 more" in said[0]
    # Once per session: the agent is mid-rewrite and does not need it repeated.
    assert w.check() == []


def test_speaks_when_the_verifier_needs_a_name_the_task_never_states(tmp_path) -> None:
    pkg = _pkg(tmp_path, solve="\n".join(f"step {i}" for i in range(12)) + "\n",
               verifier='assert report["source_sha256"]\nassert report["legacy_key"]\n',
               seed_literals=["legacy_key"])
    said = cs.PackageWatcher(pkg).check()
    assert len(said) == 1 and "source_sha256" in said[0] and "legacy_key" not in said[0]


def test_both_rules_can_speak_and_each_only_once(tmp_path) -> None:
    pkg = _pkg(tmp_path, solve="\n".join(f"step {i}" for i in range(40)) + "\n",
               verifier='assert report["source_sha256"]\n')
    w = cs.PackageWatcher(pkg)
    said = w.check()
    assert len(said) == 2
    assert w.check() == []


def test_a_package_without_a_recorded_seed_is_left_alone(tmp_path) -> None:
    pkg = _pkg(tmp_path, solve="x\n" * 90, verifier="assert one\n")
    (pkg / "run" / "seed_size.json").unlink()
    (pkg / "run" / "seed_literals.json").unlink()
    assert cs.PackageWatcher(pkg).check() == []


def test_run_writes_events_and_its_record_in_the_session_and_works_in_the_package(
        tmp_path, monkeypatch) -> None:
    """The driver takes one session directory's paths from evolve_codex: the
    package is its cwd, the codex home is private to the session, and the
    two files it owns land beside session.json."""
    pkg = _pkg(tmp_path, solve="step\n", verifier="assert one\n")
    seen: dict = {}

    class Event:
        def __init__(self, method, payload=None):
            self.method, self.payload = method, payload

    class Turn:
        final_response = "all done"

        def stream(self):
            yield Event("item/agentMessage/delta", "noise")
            yield Event("item/completed")
            yield Event("turn/completed", types.SimpleNamespace(
                turn=types.SimpleNamespace(status="completed")))

        def steer(self, message):
            seen.setdefault("steers", []).append(message)

    class Thread:
        id = "thr-1"

        def turn(self, prompt):
            seen["prompt"] = prompt
            return Turn()

    class Codex:
        def __init__(self, config):
            seen["config"] = config

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def thread_start(self, model, config):
            seen["model"], seen["thread_config"] = model, config
            return Thread()

    class CodexConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    sdk = types.ModuleType("openai_codex")
    sdk.Codex = Codex
    client = types.ModuleType("openai_codex.client")
    client.CodexConfig = CodexConfig
    monkeypatch.setitem(sys.modules, "openai_codex", sdk)
    monkeypatch.setitem(sys.modules, "openai_codex.client", client)
    monkeypatch.setenv("EVOLVE_CODEX_OVERRIDES", "model_provider=oai\n")
    session = tmp_path / "sessions" / "20260904-000000Z--agent"

    record = cs.run(pkg, session / "codex", session / "events.jsonl", "do it",
                    timeout=30, model="m", effort="high", resume=None)

    cfg = seen["config"]
    assert cfg.cwd == str(pkg) and cfg.env["CODEX_HOME"] == str(session / "codex")
    assert cfg.config_overrides == ("model_provider=oai",)
    assert (session / "codex").is_dir()
    assert seen["prompt"] == "do it" and seen["model"] == "m"
    assert seen["thread_config"] == {"model_reasoning_effort": "high"}
    events = [json.loads(line) for line in (session / "events.jsonl").read_text().splitlines()]
    assert [e["method"] for e in events] == ["harness/thread", "item/completed", "turn/completed"]
    assert events[0]["payload"] == {"id": "thr-1", "resumed": False}
    on_disk = json.loads((session / "sdk.json").read_text())
    assert on_disk["thread_id"] == "thr-1" and on_disk["turn_status"] == "completed"
    assert on_disk["returncode"] == 0 and on_disk["status"] == "exited"
    assert record["steers"] == [] and "steers" not in seen     # the package is within a rung
