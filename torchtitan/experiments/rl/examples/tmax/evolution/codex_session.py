#!/usr/bin/env python3
"""One Codex session, watched while it runs and corrected in place.

`codex exec` is a batch: it takes a prompt, works for tens of minutes, and
exits. Whatever it got wrong is found afterwards, by the caller's checks, and
the only remedy is another session -- a fresh 6 to 40 minutes to say something
that could have been said at minute three. The provider's cybersecurity
classifier is the same shape: it stops the process, and the work is lost.

The app-server protocol does not have that limitation. A thread stays open,
`turn.stream()` reports what the agent is doing as it does it, and
`turn.steer()` puts a message into the turn that is already running. Measured
on this host: an agent told to create twelve files, steered at three seconds
to stop after three, created exactly three and the turn completed normally.

So this drives the session and watches the package as it changes. When a
completed item leaves the package outside a rule the caller would have
rejected it for -- the rewrite is more than one rung above the seed, the
verifier depends on a name the task never states -- the agent is told then,
in the turn it is already in.

Run as a script, the way `codex exec` was, so the loop's contract is
unchanged (a process, a return code, the same files under `harness/`). It
needs the `openai-codex` SDK, which lives in its own virtualenv rather than
the training one: `TRL_SDK_PY` names that interpreter.

    codex_session.py --work <trace dir> --prompt-file F [--timeout S]
                     [--name codex] [--model M] [--effort E] [--resume THREAD]

It owns two files and no others, so the caller keeps writing the streams the
way it does for `codex exec`:

    harness/<name>.events.jsonl   one line per protocol event, as it happens
    harness/<name>_session.json   thread id, turn status, the steers it sent

The agent's final message goes to stdout and anything that went wrong to
stderr, which the caller streams to <name>.stdout.txt / <name>.stderr.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import task_size as ts          # noqa: E402 -- stdlib-only siblings
import verifier_literals as vl  # noqa: E402

CYBER_FLAG = "flagged for possible cybersecurity risk"


def _verifier_rel(pkg: Path) -> str:
    return "tests/test_state.py" if (pkg / "tests" / "test_state.py").exists() else "tests/test.sh"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class PackageWatcher:
    """What to say to the agent about the package as it stands, or nothing.

    Each rule speaks at most once a session. The agent is mid-work, and a
    rule that repeats every time a file moves would spend the turn arguing
    about a file the agent was already rewriting. Once is enough to change
    what it is aiming at; the caller's own checks still decide the verdict.
    """

    def __init__(self, pkg: Path):
        self.pkg = pkg
        self.said: set[str] = set()

    def check(self) -> list[str]:
        out = []
        for kind, message in (("step", self._step()), ("names", self._names())):
            if message and kind not in self.said:
                self.said.add(kind)
                out.append(message)
        return out

    def _step(self) -> str | None:
        seed = _read_json(self.pkg / "run" / "seed_size.json")
        if not seed:
            return None
        try:
            now = ts.size_of_package(self.pkg, _verifier_rel(self.pkg))
        except Exception:  # noqa: BLE001 -- a watcher must never end the session
            return None
        vs = ts.violations(seed, now)
        # Only the overshoot is worth interrupting for. "Not yet three lines
        # above the seed" is where every session starts, and saying so at
        # minute one would be noise.
        vs = [v for v in vs if "at least" not in v]
        return ts.why(vs) if vs else None

    def _names(self) -> str | None:
        baseline = _read_json(self.pkg / "run" / "seed_literals.json") or []
        try:
            names = vl.audit_package(self.pkg, _verifier_rel(self.pkg), baseline)
        except Exception:  # noqa: BLE001
            return None
        return vl.why(names) if names else None


def _event_row(ev) -> dict:
    payload = getattr(ev, "payload", None)
    row = {"t": round(time.time(), 3), "method": getattr(ev, "method", "?")}
    dump = getattr(payload, "model_dump", None)
    if dump is not None:
        try:
            row["payload"] = dump(mode="json", by_alias=True)
        except Exception:  # noqa: BLE001
            row["payload"] = str(payload)[:2000]
    elif payload is not None:
        row["payload"] = str(payload)[:2000]
    return row


# Deltas are per-token; keeping them would make the event log the transcript
# and drown the events worth watching.
NOISY = ("item/agentMessage/delta", "item/reasoning/summaryTextDelta",
         "item/reasoning/summaryPartAdded", "thread/tokenUsage/updated")


def run(work: Path, prompt: str, *, timeout: int, name: str, model: str,
        effort: str, resume: str | None) -> dict:
    from openai_codex import Codex                    # noqa: PLC0415 -- optional dep
    from openai_codex.client import CodexConfig       # noqa: PLC0415

    harness = work / "harness"
    harness.mkdir(parents=True, exist_ok=True)
    pkg = work / "pkg"
    events_path = harness / f"{name}.events.jsonl"

    env = {**os.environ, "CODEX_HOME": str(work / ".cxhome")}
    (work / ".cxhome").mkdir(exist_ok=True)
    overrides = tuple(os.environ.get("EVOLVE_CODEX_OVERRIDES", "").split("\n")) if \
        os.environ.get("EVOLVE_CODEX_OVERRIDES") else ()
    cfg = CodexConfig(codex_bin=os.environ.get("CODEX_BIN"), cwd=str(pkg), env=env,
                      config_overrides=tuple(o for o in overrides if o))

    watcher = PackageWatcher(pkg)
    record = {"schema_version": 1, "status": "exited", "name": name,
              "started_time_unix_ns": time.time_ns(), "returncode": 0,
              "timeout_seconds": timeout, "steers": [], "model": model}
    deadline = time.time() + timeout
    with events_path.open("w") as events, Codex(config=cfg) as codex:

        def note(row: dict) -> None:
            events.write(json.dumps(row, sort_keys=True) + "\n")
            events.flush()

        thread = (codex.thread_resume(resume) if resume
                  else codex.thread_start(model=model, config={"model_reasoning_effort": effort}))
        record["thread_id"] = getattr(thread, "id", None)
        note({"t": round(time.time(), 3), "method": "harness/thread",
              "payload": {"id": record["thread_id"], "resumed": bool(resume)}})
        turn = thread.turn(prompt)
        for ev in turn.stream():
            method = getattr(ev, "method", "?")
            if method not in NOISY:
                note(_event_row(ev))
            if method == "item/completed":
                for message in watcher.check():
                    try:
                        turn.steer(message)
                        record["steers"].append(message)
                        note({"t": round(time.time(), 3), "method": "harness/steer",
                              "payload": {"message": message}})
                    except Exception as exc:  # noqa: BLE001 -- the turn may have ended
                        note({"t": round(time.time(), 3), "method": "harness/steer_failed",
                              "payload": {"error": f"{type(exc).__name__}: {exc}"[:300]}})
            if method == "turn/completed":
                status = getattr(getattr(ev.payload, "turn", None), "status", None)
                record["turn_status"] = str(getattr(status, "value", status))
            if method == "turn/failed":
                err = getattr(ev.payload, "error", None)
                message = str(getattr(err, "message", err))
                record["turn_status"] = "failed"
                record["error"] = message[:500]
                record["returncode"] = 1
                print(message, file=sys.stderr, flush=True)
            if time.time() > deadline:
                record["status"] = "timed_out"
                record["returncode"] = 124
                try:
                    turn.interrupt()
                except Exception:  # noqa: BLE001
                    pass
                break
        message = getattr(turn, "final_response", None) or ""
    record["finished_time_unix_ns"] = time.time_ns()
    (harness / f"{name}_session.json").write_text(json.dumps(record, indent=1) + "\n")
    if message:
        print(message, flush=True)
    for sent in record["steers"]:
        print(f"[harness] steered the running turn: {sent}", file=sys.stderr, flush=True)
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--work", required=True)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--name", default="codex")
    ap.add_argument("--model", default=os.environ.get("SYNTH_MODEL", "gpt-5.6"))
    ap.add_argument("--effort", default=os.environ.get("CODEX_EFFORT", "high"))
    ap.add_argument("--resume", default=None, help="continue this thread id")
    args = ap.parse_args()
    try:
        record = run(Path(args.work), Path(args.prompt_file).read_text(),
                     timeout=args.timeout, name=args.name, model=args.model,
                     effort=args.effort, resume=args.resume)
    except Exception as exc:  # noqa: BLE001 -- the caller reads stderr and the code
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    sys.exit(record.get("returncode", 0))


if __name__ == "__main__":
    main()
