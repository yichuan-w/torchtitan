#!/usr/bin/env python3
"""Agentic retune: change a task with the Codex CLI instead of one chat call.

The chat retune (evolve.simplify) crams the failure traces into a single prompt
truncated to 20k chars, then asks once. This gives Codex the FULL traces as
files in a working directory and a role via AGENTS.md, and lets it read, focus,
and rewrite agentically -- no truncation, and the role/rules live in a
maintainable file rather than a built string.

Same contract as evolve.simplify / evolve.evolve: takes the task dict, returns
a new task dict with files rewritten and `_hint` set. The output goes through
the SAME revalidation downstream -- this only changes HOW the new files are
written, not the gate they must pass.

Runs Codex LOCALLY on della (the agent reads and rewrites here; the task's own
container it reaches through ./sandbox, on Daytona). Auth mirrors
solve_daytona's verified incantation: a fresh CODEX_HOME (a stray ChatGPT token
otherwise wins and 401s), a model_provider whose base_url is the same us.api
endpoint synth_client uses, and the key injected via env.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import evolve as ev
import synth_client as llm
import verifier_literals as vl


class Blocked(Exception):
    """The agent declined the job rather than forcing a pass.

    Distinct from a crash: nothing went wrong, the task simply stays as it is.
    """


def _trace_root() -> Path:
    """Return the directory that holds durable Codex traces.

    Unless ``SWE_EVOLUTION_TRACE_DIR`` overrides it, ``codex_traces`` is placed
    inside ``SWE_TASK_EVOLUTION_DIR``. The signal consumer scans only the
    directory's top-level JSON files, so trace subdirectories are not signals.
    """
    override = os.environ.get("SWE_EVOLUTION_TRACE_DIR")
    if override:
        return Path(override)
    signals = os.environ.get("SWE_TASK_EVOLUTION_DIR")
    if signals:
        return Path(signals) / "codex_traces"
    base = Path(os.environ.get(
        "TRL_BASE", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
    return base / "evolution/signals/codex_traces"


def _task_id(task: dict) -> str:
    explicit = task.get("_task_id") or task.get("task_id") or task.get("_seed_id")
    if explicit:
        return str(explicit)
    source = task.get("_src_dir")
    return Path(source).name if source else "unknown"


def _write_json_atomic(path: Path, value: dict) -> None:
    incoming = path.with_suffix(path.suffix + ".incoming")
    incoming.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(incoming, path)


def _prune_private_home(work: Path) -> None:
    """Keep session JSONL files and discard transient Codex client state.

    Each invocation has a private ``CODEX_HOME``. Files outside ``sessions/``
    are not needed for the saved trace and can be rebuilt by the client.
    """
    home = work / ".cxhome"
    if not home.is_dir():
        return
    for child in home.iterdir():
        if child.name == "sessions":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


@contextlib.contextmanager
def _trace_work(job: str, task: dict):
    """Create one durable, self-describing Codex trace directory.

        <trace>/trace.json   this record
        <trace>/harness/     what the harness gave and got back: the prompt,
                             the pre-agent archive of pkg/, process output
        <trace>/.cxhome/     the CLI's private home; sessions/ survives, the
                             rest is pruned
        <trace>/pkg/         the agent's working directory: the package,
                             AGENTS.md, ./sandbox, run/, traces/

    The agent's cwd is pkg/, one level down, so nothing the harness records
    about the session is in its view. It used to be: the agent listed
    .cxhome/ and the input archive with `find .` and once deleted its own
    scratch to tidy up.
    """
    root = _trace_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    work = Path(tempfile.mkdtemp(prefix=f"codex-{job}-", dir=root))
    # GPFS default ACLs can widen mkdtemp's requested mode. These directories
    # contain private verifiers, reference solutions, and rollout transcripts.
    work.chmod(0o700)
    (work / "pkg").mkdir(mode=0o700)
    (work / "harness").mkdir(mode=0o700)
    metadata = {
        "schema_version": 2,
        "record_type": "codex_evolution_trace",
        "job": job,
        "task_id": _task_id(task),
        "model": CODEX_MODEL,
        "reasoning_effort": CODEX_EFFORT,
        "status": "running",
        "started_time_unix_ns": time.time_ns(),
        "finished_time_unix_ns": None,
    }
    _write_json_atomic(work / "trace.json", metadata)
    try:
        yield work
    except BaseException as exc:
        metadata["status"] = "blocked" if isinstance(exc, Blocked) else "failed"
        metadata["error_type"] = type(exc).__name__
        metadata["error"] = str(exc)
        try:
            exc.codex_trace_dir = str(work)
        except Exception:  # noqa: BLE001 -- some exceptions refuse attributes
            pass
        raise
    else:
        metadata["status"] = "completed"
    finally:
        metadata["finished_time_unix_ns"] = time.time_ns()
        try:
            _prune_private_home(work)
        except OSError as exc:
            metadata["cache_cleanup_error"] = f"{type(exc).__name__}: {exc}"
        _write_json_atomic(work / "trace.json", metadata)


def _attach_trace(out: dict, task: dict, work: Path) -> dict:
    prior = list(task.get("_codex_trace_dirs") or [])
    out["_codex_trace_dir"] = str(work)
    if str(work) not in prior:
        prior.append(str(work))
    out["_codex_trace_dirs"] = prior
    return out


CODEX_BIN = os.environ.get("CODEX_BIN", "/scratch/gpfs/TRIDAO/al9080/terminal-rl/bin/codex")
CODEX_MODEL = os.environ.get("SYNTH_MODEL", "gpt-5.6")
# Same knob as the chat calls: high unless SYNTH_EFFORT says otherwise. Left
# unset, the CLI ran the sessions at its own default, which the session log
# records as reasoning_effort=null.
CODEX_EFFORT = llm.EFFORT
API_BASE = os.environ.get("SYNTH_API_BASE", "https://us.api.openai.com/v1")
# Turn budget the agent is told to respect; the hard cap is the subprocess
# timeout below. "deadline in the prompt" per the design.
MAX_TOOL_CALLS = int(os.environ.get("CODEX_RETUNE_MAX_CALLS", "25"))
TIMEOUT_SEC = int(os.environ.get("CODEX_RETUNE_TIMEOUT", "600"))

_AGENTS_MD = """# Your job: make ONE terminal task easier, by rewriting its instruction only

You are re-tuning a training task an agent kept failing. Files in this directory:

- `instruction.md` — the task as given to the agent. THIS is the only file you edit.
- `environment/Dockerfile` — how the task's container is built (context, read-only).
- `{verifier}` — the private verifier that grades a solution (context, read-only).
- `solution/solve.sh` — a reference solution, if present (context, read-only).
- `traces/failures.txt` — the FULL transcripts of the failed attempts: each turn's
  command (inside `<tool_call>`) and the terminal output (inside `<tool_response>`).

The agent solved this {solved} of {attempts} attempts — too hard. Rewrite
`instruction.md` so a capable agent lands it about half the time.

## How to make it easier ({level})
{level_rule}

## Hard rule — never leak the verifier
Never name the verifier in the instruction: no test file paths (tests/...), no
test or function names, no `pytest` / `test.sh` command to run. Point at the
BEHAVIOUR to fix or where in the source to look — naming the check that grades it
hands over the answer, and the task is then rejected. The verifier is given to you
only so you know what NOT to reveal; never weaken or reference it.

## Work efficiently
Read the traces to find where the agent actually got stuck, then make the smallest
edit that clears that one failure. At most {max_calls} tool calls. When done, the
rewritten `instruction.md` is your entire output — do not print it, just save it.
"""

_PROMPT = ("Read AGENTS.md and the files it points to, then rewrite instruction.md "
           "in place to make this task easier as instructed. Save it and stop.")


def simplify_codex(task: dict, solved: int = 0, attempts: int = 16,
                   trajectory: str = "", hint: str = "specific") -> dict:
    """Codex-driven counterpart of evolve.simplify. Returns a new task dict with
    `instruction` rewritten and `_hint="codex"`. Raises on hard failure."""
    if not os.path.exists(CODEX_BIN):
        raise RuntimeError(f"codex binary not found at {CODEX_BIN}")
    level = "specific" if trajectory else "vague"
    with _trace_work("retune", task) as work:
        pkg = work / "pkg"
        # lay the task out on disk exactly as the package looks
        fmap = ev.file_map(task)
        for key, rel in fmap.items():
            dest = pkg / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(task[key])
        (pkg / "traces").mkdir(exist_ok=True)
        (pkg / "traces" / "failures.txt").write_text(trajectory or "(no transcript captured)")
        (pkg / "AGENTS.md").write_text(_AGENTS_MD.format(
            verifier=ev._verifier_rel(task), solved=solved, attempts=attempts,
            level=level, level_rule=ev.HINT_LEVELS[level], max_calls=MAX_TOOL_CALLS))

        p = _run_codex(work, _PROMPT, timeout=TIMEOUT_SEC)
        new_instruction = (pkg / fmap["instruction"]).read_text()
        if not new_instruction.strip():
            raise RuntimeError("codex emptied the instruction")
        if new_instruction == task["instruction"]:
            raise RuntimeError(f"codex left the instruction unchanged "
                               f"(exit {p.returncode}): {p.stdout[-200:]}")
        out = {**task, "instruction": new_instruction, "_hint": "codex"}
        return _attach_trace(out, task, work)


_ORACLE_AGENTS_MD = """# Your job: make the reference solution pass the verifier

This task was just rewritten to be harder. The rewrite regenerated the
instruction, the solution and the verifier together, and they do not agree: the
reference solution was run against the verifier and FAILED. Fix that.

Files in this directory:

- `solution/solve.sh` — the reference solution. Usually the file to fix.
- `{verifier}` — the verifier that grades it. Fix only where it is unfair.
- `instruction.md` — what the agent is told (context; edit only if it promises
  something the verifier does not check, or omits something the verifier needs).
- `environment/Dockerfile` — how the container is built (context, read-only).
- `run/failure.txt` — what actually happened when the solution ran, exit code
  {exit_code}. Start here. You are not guessing about behaviour: this is the
  behaviour.

## Method
Read `run/failure.txt` first and let it decide where you look — an impression of
what the code should do is what produced this failure. Identify the specific
check that failed, find the lines of `solve.sh` responsible for it, and fix
those. Do not rewrite what the run shows is already working.

## Which side to change
Prefer `solve.sh`. The verifier encodes what the task asks for, and weakening it
to fit an incomplete solution is how a task becomes worthless. Change a check
only when it demands something the instruction never promised and the workspace
never reveals — such a check would fail a capable agent too. If a check re-runs
the workflow after changing an input, the solution has to recompute from the
inputs as they are at that moment; a hard-coded answer passes the first run and
fails the re-run.

If the failure is environmental rather than logical — a missing tool, no network
— do not code around it. Write `BLOCKED: <reason>` to `run/verdict.txt` and stop.

## Work efficiently
At most {max_calls} tool calls. Save your edits in place; do not print the files.
"""

_ORACLE_PROMPT = ("Read AGENTS.md, then read run/failure.txt and fix the task so "
                  "the reference solution passes the verifier. Save your edits "
                  "in place and stop.")


def _split_attempts(trajectory: str) -> list[str]:
    """Split a concatenated transcript back into per-attempt chunks."""
    if not trajectory.strip():
        return []
    parts = re.split(r"\n(?=[-=]{3,}\s*attempt|\[attempt |### attempt)",
                     trajectory, flags=re.I)
    return [p for p in (part.strip() for part in parts) if p] or [trajectory]


def _render_attempt(attempt: dict) -> str:
    """One rollout as the agent reads it: the header, then every turn whole.

    Same shape as feedback_loop.format_trace, minus its cuts. That function was
    written for the chat prompt and trims each turn's terminal output to 600
    characters and the verifier tail to 400; on disk there is no budget to
    protect, and the cut landed exactly where the agent needed to look.
    """
    lines = [f"--- attempt reward={attempt.get('reward')} "
             f"turns={attempt.get('turns')} ---"]
    for step in attempt.get("transcript") or []:
        lines.append(f"$ {step.get('cmd', '')}")
        lines.append(f"  {step.get('out', '')}")
    if attempt.get("test_tail"):
        lines.append(f"verifier tail: {attempt['test_tail']}")
    return "\n".join(lines)


def _codex_env(work: Path) -> dict:
    home = work / ".cxhome"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    env["OPENAI_API_KEY"] = llm._api_key()
    # ./sandbox is a copy of agent_sandbox.sh dropped into pkg/, so from inside
    # the workdir the script cannot find agent_sandbox.py beside itself. Tell
    # it where the harness lives. Without this every session ends in BLOCKED
    # and the loop records it as `kept`.
    env["EVOLVE_HARNESS_DIR"] = str(Path(__file__).resolve().parent)
    return env


def _codex_cmd(work: Path, resume: str | None = None) -> list[str]:
    cmd = [CODEX_BIN, "exec"]
    if resume:
        cmd += ["resume", resume]
    cmd += [
        "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
        "-c", "model_providers.oai.name=openai",
        "-c", f"model_providers.oai.base_url={API_BASE}",
        "-c", "model_providers.oai.env_key=OPENAI_API_KEY",
        "-c", "model_provider=oai",
        "-c", f"model_reasoning_effort={CODEX_EFFORT}",
    ]
    # `exec resume` takes no -C (codex-cli 0.149: it continues in the
    # session's recorded cwd); the subprocess is started in pkg/ either way.
    if not resume:
        cmd += ["-C", str(work / "pkg")]
    cmd += ["-m", CODEX_MODEL, "-"]
    return cmd


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def _snapshot_inputs(work: Path) -> None:
    """Archive the exact pre-agent package before Codex can modify it."""
    pkg = work / "pkg"
    archive = work / "harness" / "input-package.tar.gz"
    incoming = archive.with_suffix(".gz.incoming")
    with tarfile.open(incoming, "w:gz") as tf:
        for child in sorted(pkg.iterdir()):
            tf.add(child, arcname=child.name, recursive=True)
    os.replace(incoming, archive)


def _write_process_record(
    work: Path,
    *,
    name: str,
    command: list[str],
    status: str,
    started_time_unix_ns: int,
    stdout: str | bytes | None = None,
    stderr: str | bytes | None = None,
    returncode: int | None = None,
    timeout_seconds: int | None = None,
    error: str | None = None,
) -> None:
    harness = work / "harness"
    harness.mkdir(exist_ok=True)
    (harness / f"{name}.stdout.txt").write_text(_as_text(stdout))
    (harness / f"{name}.stderr.txt").write_text(_as_text(stderr))
    record = {
        "schema_version": 1,
        "command": command,
        "status": status,
        "returncode": returncode,
        "timeout_seconds": timeout_seconds,
        "started_time_unix_ns": started_time_unix_ns,
        "finished_time_unix_ns": time.time_ns(),
    }
    if error:
        record["error"] = error
    _write_json_atomic(harness / f"{name}_process.json", record)


def _run_codex(
    work: Path,
    prompt: str,
    *,
    timeout: int = TIMEOUT_SEC,
    resume: str | None = None,
    name: str = "codex",
) -> subprocess.CompletedProcess:
    """Run codex over `work/pkg` with a private CODEX_HOME and the us.api provider.

    A stray ChatGPT token in the shared home otherwise wins and 401s, which is
    why the home is per-invocation rather than shared. `resume` continues a
    recorded session (its id) instead of starting one; the pre-agent archive is
    taken only for a fresh session, since a resumed one starts from the files
    the first left.
    """
    harness = work / "harness"
    harness.mkdir(exist_ok=True)
    (harness / f"{name}_prompt.txt").write_text(prompt)
    if not resume:
        _snapshot_inputs(work)
    cmd = _codex_cmd(work, resume=resume)
    started = time.time_ns()
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work / "pkg"),
            env=_codex_env(work),
        )
    except subprocess.TimeoutExpired as exc:
        _write_process_record(
            work, name=name, command=cmd, status="timed_out",
            started_time_unix_ns=started, stdout=exc.stdout, stderr=exc.stderr,
            timeout_seconds=timeout, error=str(exc),
        )
        raise
    except Exception as exc:
        _write_process_record(
            work, name=name, command=cmd, status="failed_to_start",
            started_time_unix_ns=started, timeout_seconds=timeout,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    _write_process_record(
        work, name=name, command=cmd, status="exited",
        started_time_unix_ns=started, stdout=result.stdout, stderr=result.stderr,
        returncode=result.returncode, timeout_seconds=timeout,
    )
    return result


def _lay_out(task: dict, pkg: Path) -> dict:
    """Put the whole package in `pkg`, then overwrite the editable files.

    Laying out only the four files that round-trip through the task dict gives
    the agent a package that cannot run: `tests/test.sh` is the harness entry
    point, and an environment usually carries an entrypoint and fixtures the
    Dockerfile copies in. Their absence does not surface as "files are missing"
    -- validation raises, the caller catches everything as a platform error, and
    the agent reads "the sandbox is broken" and gives up on a package that was
    never whole. So copy the source tree first and let the dict win on top of
    it; the dict is still the only thing read back.
    """
    src = task.get("_src_dir")
    if src and Path(src).is_dir():
        shutil.copytree(
            src, pkg, dirs_exist_ok=True,
            # Backups of the pre-canary-strip instruction are not part of the
            # task and would show the agent text the pool deliberately removed.
            ignore=shutil.ignore_patterns("*.bak-*", ".provenance.json",
                                          "__pycache__", ".git"))
    fmap = ev.file_map(task)
    for key, rel in fmap.items():
        dest = pkg / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(task[key])
    # copytree applies the source package's root mode to the existing directory.
    # Restore the private mode before writing prompts or sessions beside it.
    pkg.chmod(0o700)
    _write_seed_literals(pkg, fmap["test_state_py"])
    return fmap


def _write_seed_literals(pkg: Path, verifier_rel: str) -> None:
    """What the seed's verifier already depends on unseen, for `./sandbox
    check`'s names audit to subtract: the agent answers for the names its
    rewrite added, not for the seed's. Written once, at layout, while the
    package on disk is still the seed."""
    path = pkg / "run" / "seed_literals.json"
    if path.exists():
        return
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(vl.audit_package(pkg, verifier_rel)) + "\n")


def repair_oracle_codex(task: dict, observed: str, exit_code: int = 1) -> dict:
    """Fresh-session repair of a task whose reference solution failed the run.

    The chat repair reads the two files and guesses which side is wrong. Here the
    agent has both files on disk at full length and the run output that proves
    they disagree, and can grep between them instead of holding a 500-line
    solution and a 500-line verifier in one prompt. `resume_agentic` is the
    better tool when the session that wrote the files is on disk; this is for
    when it is not. Raises on failure.
    """
    if not os.path.exists(CODEX_BIN):
        raise RuntimeError(f"codex binary not found at {CODEX_BIN}")
    with _trace_work("oracle", task) as work:
        pkg = work / "pkg"
        fmap = _lay_out(task, pkg)
        (pkg / "run").mkdir(exist_ok=True)
        (pkg / "run" / "failure.txt").write_text(
            observed or "(no output captured)")
        (pkg / "AGENTS.md").write_text(_ORACLE_AGENTS_MD.format(
            verifier=ev._verifier_rel(task), exit_code=exit_code,
            max_calls=MAX_TOOL_CALLS))
        p = _run_codex(work, _ORACLE_PROMPT)
        verdict = pkg / "run" / "verdict.txt"
        if verdict.exists() and verdict.read_text().strip().upper().startswith("BLOCKED"):
            raise RuntimeError(f"codex reported blocked: "
                               f"{verdict.read_text().strip()[:160]}")
        out = {**task, **{key: (pkg / rel).read_text()
                          for key, rel in fmap.items()}}
        if all(out[key] == task[key] for key in fmap):
            raise RuntimeError(f"codex changed nothing (exit {p.returncode}): "
                               f"{p.stdout[-200:]}")
        out["_repaired"] = "codex_oracle_observed"
        return _attach_trace(out, task, work)

# --------------------------------------------------------------------------
# Agentic evolution: one session, with the task's own container as a tool
# --------------------------------------------------------------------------

SPEC = Path(__file__).resolve().parent / "agents" / "task_evolution.md"
SANDBOX = Path(__file__).resolve().parent / "agent_sandbox.sh"
AGENT_TIMEOUT = int(os.environ.get("EVOLVE_AGENT_TIMEOUT", "2400"))
SCAFFOLD = {"AGENTS.md", "sandbox"}

_HARDER_JOB = """This task was solved {solved} of {attempts} attempts, so it is too
easy to teach anything. Make it harder along exactly one of these axes:

{candidates}

Transcripts of the attempts that solved it are in `traces/`, one file per
attempt. Read a couple first: what made the task easy is visible there — the
guidance the instruction handed over, the step the agent never had to work out.

Pick from that list and nothing else. The list is not a menu of equals — it is
ordered, and the order was computed against the whole task pool: which
transformations are under-represented right now, and which ones this task has a
foothold for. Work down it and take the first axis this package genuinely
supports, so that substituting whichever is easiest to write cannot quietly
collapse the pool onto a few kinds of change. Then write that axis's id, alone
on one line, to `run/operator.txt`, before you start changing anything.

Then do the work in this order, one file at a time. The order is not
arbitrary — each file is written against the one before it, and the synthesis
pipeline that produced these tasks runs the same sequence for that reason:

  1. `solution/solve.sh` — what the answer now is under this axis.
  2. the verifier — written against that answer and against the axis, not
     against incidental details of the workspace.
  3. `instruction.md` — what the agent is told. It has to make everything the
     verifier requires discoverable: findable in the workspace, or stated here.
  4. `environment/Dockerfile` — the environment the other three assume.

Add whatever new files the change needs — a fixture the Dockerfile COPYs, a
config, a data file. Anything you write in the package comes back with it.

Constraints on the environment, because step 4 is yours now and these are what
the pipeline that built these tasks learned the hard way:

- **Every COPY source must exist in the package.** A Dockerfile line referring to
  a file you did not write is the most common way a rewritten task is thrown
  away: it builds nowhere, and the failure arrives long after this session ended.
- Preserve the seed's base image and installation style; make the smallest change
  the axis needs. An environment rewritten wholesale is a new task, not a harder
  one.
- No internet-only runtime behaviour, no proxies, credentials or external
  services. The sandbox may have none of them and the reference solution will
  fail where an agent would too.
- The container is the size training gives this task (`run/resources.json`),
  and `./sandbox check` measures what the reference solution costs in it. The
  task is provisioned from that measurement, never below the seed's size and
  never from a number you write. If the solution outgrows the box, `check`
  says what ran out; make the solution need less. `./sandbox check --max`
  measures at the platform ceiling (4 vCPU / 8 GiB / 10 GiB) and is for the
  rare harder task that genuinely needs more than the seed had, not a way
  past a failing check. A task that needs more than the ceiling never starts,
  so it is unrunnable rather than hard.
- Guard edits against paths you did not create (`test -f` first); prefer adding a
  local fixture over patching something the image cloned.

**Before you run `./sandbox check`, reconcile the verifier against the solution
one check at a time.** For each check, name the lines of `solve.sh` that make it
pass. A check with nothing behind it is the single largest way these tasks are
lost: the reference runs cleanly, scores zero, and the whole task is thrown away
after a full image build. Where nothing satisfies a check, fix `solve.sh` if the
check is fair, and fix the check if it asks for something the instruction never
promised and the workspace never reveals — that one would fail a real agent too.
Do this literally, check by check; an impression that it all hangs together is
what produces the failure.

If, having read the package, you judge that none of the listed axes fits this
task, write `GIVE UP: operator-misfit — <why>` and stop. Say which ones you
considered and what was missing for each; a later round will come back with
different counts, and that note is what it reads.

Aim for a task a capable agent lands about half the time."""

_EASIER_JOB = """This task was solved {solved} of {attempts} attempts — the agent
never got there, so it teaches nothing either.

The full transcripts of the failed attempts are in `traces/`, one file per
attempt. Read them to find where the agent actually got stuck, then make the
smallest change that clears that one obstacle. Prefer adding to the instruction
what a fair task would have said; only take structure out of the task itself if
the instruction cannot carry it.

Aim for a task a capable agent lands about half the time."""

_REPAIR_JOB = """Your rewrite did not survive the caller's check. It rebuilt the
package from scratch, ran `solution/solve.sh` against the verifier (exit
{exit_code}) and audited what the verifier demands against what an agent can
see. The verdict and the run's output are in `run/failure.txt`.

Read that first. If the run failed, find the check that failed and the lines
of `solution/solve.sh` responsible for it, and fix them; prefer fixing the
solution, and change the verifier only where it demands something the
instruction never promised and the workspace never reveals. If the verdict
names paths the verifier requires that nothing the agent can see reveals, name
them where the agent will read them (the instruction, or a file in the image
the instruction points at), or make the verifier stop depending on them; do
not weaken what it checks otherwise. Do not rewrite what the run shows is
already working. The container you had is gone; `./sandbox up` gives you a
fresh one.

Confirm with `./sandbox check` before you stop."""

_BUDGET = """

Budget: this session is ended at {deadline} ({budget_min} minutes from now),
whatever state the files are in, and a session that ends that way is discarded
whole. `./sandbox up`, `reset` and `check` build the image and take minutes each;
`./sandbox exec` takes seconds. Plan for two or three checks, not for guessing."""


def _budget(timeout: int) -> str:
    deadline = time.strftime("%H:%M %Z", time.localtime(time.time() + timeout))
    return _BUDGET.format(deadline=deadline, budget_min=timeout // 60)


def _candidates(operator: list[tuple[str, str, str]] | None) -> str:
    """The scored shortlist, each entry with its full card.

    The one-line definition is what the pool's scan matches on; the card is
    what the authors wrote for whoever builds the task: what the seed needs to
    have, how the harder version is constructed, what the verifier checks and
    how a shortcut is refused. The agent used to get the line only.
    """
    cands = list(operator or [])
    if not cands:
        return "    (none)"
    parts = []
    for i, (fam, op, defn) in enumerate(cands, 1):
        card = llm.operator_card(op)
        card_lines = "\n".join("       " + line for line in card.splitlines())
        parts.append(f"    {i}. {op} ({fam})\n       {defn}\n{card_lines}")
    return "\n".join(parts)


def _write_traces(pkg: Path, attempts: list[dict] | None, trajectory: str) -> None:
    # One file per attempt rather than all sixteen concatenated: the agent
    # reads one, forms a view, reads another. A single blob of 60k characters
    # is read once, from the top, and mostly skipped. Structured attempts are
    # rendered whole; a pre-rendered transcript is split back up as a fallback.
    (pkg / "traces").mkdir(exist_ok=True)
    chunks = ([_render_attempt(a) for a in attempts] if attempts
              else _split_attempts(trajectory))
    for i, chunk in enumerate(chunks, 1):
        (pkg / "traces" / f"attempt-{i:02d}.txt").write_text(chunk)


def _sandbox_down(work: Path) -> None:
    """Delete the container a session left running. Best effort."""
    pkg = work / "pkg"
    state = pkg / "run" / "sandbox.json"
    if not state.exists():
        return
    try:
        if json.loads(state.read_text()).get("status") == "down":
            return
    except ValueError:
        pass
    try:
        subprocess.run([str(pkg / "sandbox"), "down"], cwd=pkg, env=_codex_env(work),
                       capture_output=True, text=True, timeout=300)
    except Exception:  # noqa: BLE001 -- cleanup must not turn a verdict into a crash
        pass


def _last_check(pkg: Path) -> dict | None:
    """The last record `./sandbox check` appended, or None."""
    path = pkg / "run" / "checks.jsonl"
    if not path.exists():
        return None
    last = None
    for line in path.read_text().splitlines():
        if line.strip():
            last = line
    if not last:
        return None
    try:
        return json.loads(last)
    except ValueError:
        return None


def _agent_checked(pkg: Path) -> bool:
    """Whether the agent's last `./sandbox check` passed."""
    return (_last_check(pkg) or {}).get("verdict") == "pass"


def _require_checked(pkg: Path) -> None:
    """AGENTS.md promises that a rewrite which never passed `./sandbox check`
    is discarded whole. Until this, nothing enforced it: the caller's probe
    would find out at its own expense. Measured on wd-20260903b, 298 of 299
    folded sessions had the record anyway, so this bites rarely and keeps the
    promise true."""
    if not _agent_checked(pkg):
        raise RuntimeError("agent finished without a passing ./sandbox check "
                           "(run/checks.jsonl); the rewrite is discarded")


def _write_resources(pkg: Path, task: dict) -> None:
    """Tell the sandbox tool what size box training gives this task.

    `_resources` is the row's own daytona_cpu/mem_gb/disk_gb filled out with
    the trainer's fleet defaults, resolved by the loop. Without this file the
    tool opens the harness default box (2/4/6), which on this corpus is a size
    up from what training runs at (1/2/2): the agent's check would pass in a
    box the task never gets, and the fold would inherit the seed's size for a
    task that had outgrown it.
    """
    res = task.get("_resources")
    if not res:
        return
    (pkg / "run").mkdir(exist_ok=True)
    (pkg / "run" / "resources.json").write_text(json.dumps(res, sort_keys=True) + "\n")


def _collect(task: dict, pkg: Path, fmap: dict) -> dict:
    """Read the package back: the four round-tripping files plus everything new.

    Only those four travel in the task dict, so a fixture the agent created --
    an init.sql, a conf the new Dockerfile COPYs -- was written, validated in
    place, and then dropped on the way out when the directory was removed. The
    package that reached revalidation had a Dockerfile referring to files
    nobody carried back, which failed as `copy_source_missing` with the agent's
    own run reported as a pass. Bytes, not text: a fixture may be a binary.
    """
    # The agent may rewrite the verifier as the other corpus's form -- grading
    # always runs tests/test.sh, and a TW task carrying tests/test_state.py can
    # legitimately grow a test.sh and drop the helper -- so read back whichever
    # verifier is on disk now, not the path the source happened to carry, and
    # carry that path forward so the writeback and the row builder follow the
    # file the agent kept. A task that passed ./sandbox check with a switched
    # verifier used to crash here on the deleted file and be discarded whole.
    fmap = dict(fmap)
    if not (pkg / fmap["test_state_py"]).exists():
        alt = next((c for c in ev.VERIFIER_CANDIDATES if (pkg / c).exists()), None)
        if alt is None:
            raise RuntimeError(
                f"agent left no verifier on disk ({ev.VERIFIER_CANDIDATES})")
        fmap["test_state_py"] = alt
    out = {**task, **{key: (pkg / rel).read_text() for key, rel in fmap.items()}}
    out["_verifier_rel"] = fmap["test_state_py"]
    # What the reference solution cost in the agent's own container, from the
    # last check that passed. The caller sizes the rewritten row from this --
    # a measurement, never the agent's estimate -- and verifies at that size.
    chk = _last_check(pkg) or {}
    if chk.get("verdict") == "pass":
        out["_measured"] = chk.get("measured")
        out["_box"] = chk.get("resources")
        out["_at_max"] = bool(chk.get("at_max"))
    mapped = set(fmap.values())
    src_dir = task.get("_src_dir")
    out["_extra_files"] = {}
    for f in sorted(pkg.rglob("*")):
        if not f.is_file():
            continue
        rel = str(f.relative_to(pkg))
        top = rel.split("/", 1)[0]
        if rel in mapped or rel in SCAFFOLD:
            continue
        if top in ("run", "traces", "__pycache__"):
            continue
        src_file = Path(src_dir) / rel if src_dir else None
        if src_file is not None and src_file.is_file() and \
                src_file.read_bytes() == f.read_bytes():
            continue          # unchanged support file; already on disk there
        out["_extra_files"][rel] = f.read_bytes()
    return out


def _check_verdict(pkg: Path) -> None:
    verdict = pkg / "run" / "verdict.txt"
    if verdict.exists():
        text = verdict.read_text().strip()
        head = text.upper()
        # Both are legitimate endings, and both mean the same thing here:
        # leave the task alone. Giving up honestly has to be at least as
        # easy as passing, or the only way to finish is to make the check
        # weaker until it passes.
        if head.startswith("BLOCKED") or head.startswith("GIVE UP"):
            raise Blocked(text[:200])


def evolve_agentic(task: dict, job: str, trajectory: str = "",
                   observed: str = "", exit_code: int = 1,
                   operator: list[tuple[str, str, str]] | None = None,
                   attempts: list[dict] | None = None,
                   ) -> dict:
    """Run one agent session over the task package, with its container as a tool.

    The agent works in a copy of the package and reaches the task's own
    environment through ./sandbox: a container it can run commands in, run the
    reference solution in, grade, and rebuild fresh. The caller still
    revalidates afterwards; the agent's own pass is not the gate, it is what
    stops the agent from finishing on a rewrite it never ran.

    `job` is one of "harder", "easier", "repair". `attempts` are the rollouts
    as the signal carries them; `trajectory` is the pre-rendered fallback.
    Raises on failure; raises Blocked when the agent declined.
    """
    if not os.path.exists(CODEX_BIN):
        raise RuntimeError(f"codex binary not found at {CODEX_BIN}")
    with _trace_work(job, task) as work:
        pkg = work / "pkg"
        fmap = _lay_out(task, pkg)
        (pkg / "run").mkdir(exist_ok=True)
        _write_resources(pkg, task)
        _write_traces(pkg, attempts, trajectory)
        if observed:
            (pkg / "run" / "failure.txt").write_text(observed)
        shutil.copy2(SPEC, pkg / "AGENTS.md")
        shutil.copy2(SANDBOX, pkg / "sandbox")
        os.chmod(pkg / "sandbox", 0o755)

        solved = task.get("_solved", 0)
        attempts_n = task.get("_attempts", len(attempts) if attempts else 16)
        # `operator` is the scored shortlist, in score order, each entry
        # (family, operator_id, definition) -- the same order operator_shortlist
        # and pick_operator both return.
        cands = list(operator or [])
        allowed = {op: fam for fam, op, _ in cands}
        prompt = {
            "harder": _HARDER_JOB.format(solved=solved, attempts=attempts_n,
                                         candidates=_candidates(cands)),
            "easier": _EASIER_JOB.format(solved=solved, attempts=attempts_n),
            "repair": _REPAIR_JOB.format(exit_code=exit_code),
        }[job] + _budget(AGENT_TIMEOUT)

        try:
            p = _run_codex(work, prompt, timeout=AGENT_TIMEOUT)
        finally:
            _sandbox_down(work)

        _check_verdict(pkg)
        _require_checked(pkg)
        out = _collect(task, pkg, fmap)
        if all(out[key] == task[key] for key in fmap) and not out["_extra_files"]:
            raise RuntimeError(f"agent changed nothing (exit {p.returncode}): "
                               f"{p.stdout[-200:]}")
        if not out["instruction"].strip():
            raise RuntimeError("agent emptied the instruction")
        out["_hint"] = f"agent_{job}"
        out["_agent_validated"] = _agent_checked(pkg)
        if cands:
            # The diversity terms are not a counter -- they are recomputed each
            # round by rescanning the pool and reading the operator off every
            # task. A task folded back without provenance is invisible to that
            # scan, so the family balance drifts and nothing reports it. That is
            # why an unreadable declaration fails the session rather than
            # defaulting to the top candidate: a wrong operator on a folded task
            # is worse for the balance than no task, because it is counted.
            declared = (pkg / "run" / "operator.txt")
            chosen = (declared.read_text().strip().split()[0]
                      if declared.exists() and declared.read_text().strip()
                      else "")
            if chosen not in allowed:
                raise RuntimeError(
                    f"agent did not declare which axis it used "
                    f"(run/operator.txt={chosen!r}, offered={sorted(allowed)})")
            out["_operator"], out["_family"] = chosen, allowed[chosen]
        return _attach_trace(out, task, work)


def _session_id(work: Path) -> str:
    """The id of the session recorded under this trace's CODEX_HOME."""
    newest = None
    for f in (work / ".cxhome" / "sessions").rglob("*.jsonl"):
        if newest is None or f.stat().st_mtime > newest.stat().st_mtime:
            newest = f
    if newest is None:
        raise RuntimeError(f"no recorded session under {work / '.cxhome'}")
    with newest.open() as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "session_meta":
                sid = (rec.get("payload") or {}).get("id")
                if sid:
                    return str(sid)
    raise RuntimeError(f"no session_meta in {newest}")


def resume_agentic(task: dict, observed: str, exit_code: int = 1) -> dict:
    """Continue the session that wrote this task, with the caller's failure.

    The caller rebuilt the package and ran the reference solution against the
    verifier; it failed. A fresh repair session has to rediscover what the
    first one knew -- why the files look the way they do -- from the files
    alone. The first session is on disk (`codex exec resume`), so give it the
    failure instead. Raises on failure; raises Blocked when the agent declined.
    """
    if not os.path.exists(CODEX_BIN):
        raise RuntimeError(f"codex binary not found at {CODEX_BIN}")
    work = Path(task.get("_codex_trace_dir") or "")
    pkg = work / "pkg"
    if not pkg.is_dir():
        raise RuntimeError(f"no agent package to resume at {pkg}")
    sid = _session_id(work)
    fmap = ev.file_map(task)
    (pkg / "run").mkdir(exist_ok=True)
    _write_resources(pkg, task)
    (pkg / "run" / "failure.txt").write_text(observed or "(no output captured)")
    for stale in ("verdict.txt", "checks.jsonl"):
        (pkg / "run" / stale).unlink(missing_ok=True)
    meta_path = work / "trace.json"
    meta = json.loads(meta_path.read_text())
    meta.setdefault("repairs", []).append(
        {"session_id": sid, "status": "running", "exit_code": exit_code,
         "started_time_unix_ns": time.time_ns()})
    _write_json_atomic(meta_path, meta)
    repair = meta["repairs"][-1]
    try:
        prompt = _REPAIR_JOB.format(exit_code=exit_code) + _budget(AGENT_TIMEOUT)
        try:
            p = _run_codex(work, prompt, timeout=AGENT_TIMEOUT, resume=sid,
                           name=f"codex.repair{len(meta['repairs'])}")
        finally:
            _sandbox_down(work)
        _check_verdict(pkg)
        _require_checked(pkg)
        out = _collect(task, pkg, fmap)
        if all(out[key] == task[key] for key in fmap) and not out["_extra_files"]:
            raise RuntimeError(f"agent changed nothing on resume "
                               f"(exit {p.returncode}): {p.stdout[-200:]}")
        out["_repaired"] = "codex_resume"
        out["_agent_validated"] = _agent_checked(pkg)
        repair["status"] = "completed"
        return _attach_trace(out, task, work)
    except BaseException as exc:
        repair["status"] = "blocked" if isinstance(exc, Blocked) else "failed"
        repair["error"] = f"{type(exc).__name__}: {exc}"[:300]
        try:
            exc.codex_trace_dir = str(work)
        except Exception:  # noqa: BLE001 -- some exceptions refuse attributes
            pass
        raise
    finally:
        repair["finished_time_unix_ns"] = time.time_ns()
        try:
            _prune_private_home(work)
        except OSError:
            pass
        _write_json_atomic(meta_path, meta)
