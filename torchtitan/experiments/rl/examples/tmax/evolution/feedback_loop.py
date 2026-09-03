#!/usr/bin/env python3
"""The feedback pipeline: adjust a task pool from a solver's rollouts.

This is the training pipeline's task-adjustment stage, and it does one thing:
read a solver's rollouts and move each task toward the usable band per the
project algorithm:

    0 of k solved   easier, with a hint read off a failing trajectory
    k of k solved   harder, one operator, the RST way
    in between      already discriminating; leave it

It does NOT run rollouts. That is the training side's job — RL produces them on
the model being trained — and folding a solver into this stage would weld the
wrong model into it and blur the line the rest of this project draws: the data
side does not measure difficulty, it consumes the measurement. So the input is a
rollout file (`--rollouts`), and swapping the solver that produced it — GPT now,
Qwen once training returns its own — changes the input, not this loop.

To bootstrap before training rollouts exist, produce the file with
`solve_eval.py --keep-trace`: a GPT solve pass standing in for the training
model, in exactly the shape training will hand back.

Per task, given its rollout record:
  1. route on the solve count (above)
  2. re-validate whatever changed: build, oracle, null probe — an
     adjustment can break a task, and shipping a broken one is worse than not
     adjusting it. (This builds, but it does not solve: checking a task is
     well-formed is the data side's job; solving it is not.)
  3. write the adjusted package and a per-task record

Resumable (ids already in --results are skipped), observable (per-item log),
reproducible (the rollout that drove each decision is named in the record).

Usage:
  feedback_loop.py --rollouts results/solve_seed_trace.jsonl \\
      --pool data/synth-v24/round_1 --out data/feedback-r1 \\
      --results results/feedback_r1.jsonl [--workers 4]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import synth_loop as sl
import synth_client as llm
import evolve as ev
import derive_sizing as ds
import verifier_literals as vl

log = logging.getLogger("feedback")

# Daytona revalidation (hosts without docker, e.g. della). The probe runs in
# the training venv with the Daytona env sourced -- the same platform, harness
# and grading contract the rollouts use, so passing it is a STRONGER build+
# oracle check than the local docker shim it stands in for. Both paths are
# env-overridable; when either is absent this host simply has no build story
# and structural retunes are declined as before.
DAYTONA_VENV_PY = os.environ.get(
    "TRL_VENV_PY", "/scratch/gpfs/TRIDAO/al9080/titan-rl/bin/python")
DAYTONA_ENV_FILE = os.environ.get(
    "DAYTONA_ENV_FILE", os.path.expanduser("~/.config/daytona/env"))


_INFRA_RE = re.compile(
    r"Timeout|Bad ?Gateway|InternalServer|50[234]|[Cc]onnection|timed? ?out|"
    r"no stdout|TooManyRequests|429")


RESOURCE_KEYS = ("cpu", "mem_gb", "disk_gb")


def daytona_probe(work: Path, shortcut: str | None = None,
                  resources: dict | None = None,
                  require_paths: list[str] | None = None) -> dict | None:
    """Run daytona_revalidate.py on this package; None when unconfigured.

    `resources` is the box to run it in: the size the row is provisioned at in
    training, so an oracle pass here is a pass where the task will be run. A
    key left None falls to the harness default for this process's env.
    `require_paths` come back in the verdict as `paths_missing`: the ones the
    untouched workspace does not have.
    """
    # Three different things used to collapse into one silent `return None`, and
    # the caller turns None into "neither docker nor Daytona is configured here".
    # For a host that genuinely has no credentials that message is true. For a
    # tree where the script is merely missing it is a confident lie, and there is
    # no other trace: measured on della 2026-09-01, a vendoring commit moved this
    # script into ops/ while the resolver kept looking beside feedback_loop.py,
    # and 423 k/k evolutions -- every "make it harder" attempt of a 13-hour run,
    # ~1300 gpt-5.6 calls -- were rejected as unconfigured. Only simplify, whose
    # instruction-only edits skip the build gate, could still land, so the mix
    # drifted one way for half a day. An absent credential is a host's choice and
    # stays quiet; an absent script is this repo's bug and has to say so.
    script = Path(__file__).resolve().parent / "daytona_revalidate.py"
    if not script.exists():
        log.error("daytona_revalidate.py not found at %s -- structural retunes "
                  "cannot be revalidated and will ALL be rejected. This is a "
                  "packaging error, not a missing credential: the script must "
                  "sit beside feedback_loop.py (it imports pack_to_dataset as a "
                  "sibling).", script)
        return None
    if not (os.path.exists(DAYTONA_VENV_PY) and os.path.exists(DAYTONA_ENV_FILE)):
        log.info("Daytona probe unconfigured (venv=%s env_file=%s); structural "
                 "retunes stay unshipped on this host.",
                 os.path.exists(DAYTONA_VENV_PY),
                 os.path.exists(DAYTONA_ENV_FILE))
        return None
    cmd = ["bash", "-c", '. "$1" && shift && exec "$@"', "-",
           DAYTONA_ENV_FILE, DAYTONA_VENV_PY, str(script), str(work)]
    if shortcut:
        cmd += ["--shortcut", shortcut]
    for key in RESOURCE_KEYS:
        if (resources or {}).get(key) is not None:
            cmd += [f"--{key.replace('_', '-')}", str(resources[key])]
    for p in require_paths or ():
        cmd += ["--require-path", p]
    # One retry, only for infra-shaped failures. The platform is measurably
    # flaky (DaytonaTimeout/BadGateway bursts), and a probe that fails for the
    # platform's sake discards a perfectly good retune: in one window 79
    # oracle-failures + 25 wrapper errors threw away ~2/3 of the successful
    # evolve output. A genuine reject (oracle can't solve the rewrite) does not
    # match the pattern and still fails fast.
    last: dict = {"ok": False, "stage": "daytona_error", "why": "not run"}
    for attempt in (1, 2):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=2400)
            lines = p.stdout.strip().splitlines()
            if not lines:
                # The script died before its JSON verdict; splitlines()[-1]
                # used to raise a bare IndexError here and eat the real cause.
                last = {"ok": False, "stage": "daytona_error",
                        "why": (f"no stdout (exit {p.returncode}): "
                                f"{p.stderr.strip()[-160:]}")}
            else:
                last = json.loads(lines[-1])
        except Exception as e:  # noqa: BLE001
            last = {"ok": False, "stage": "daytona_error",
                    "why": f"{type(e).__name__}: {e}"[:200]}
        if last.get("ok") or not _INFRA_RE.search(str(last.get("why", ""))):
            return last
        if attempt == 1:
            time.sleep(20)
    return last


def image_tag(prefix: str, tid: str) -> str:
    """A Docker-legal image name. A repository name may not begin or end with a
    separator, and pool directory ids often end in a truncation underscore."""
    name = re.sub(r"[^a-z0-9_.-]", "", tid.lower()).strip("_.-")
    return f"{prefix}-{name or 'task'}"


def format_trace(attempts: list[dict], keep: int = 3) -> str:
    """Turn rollout transcripts into the text a hint is drawn from.

    Prefer failing attempts — a hint aims at where the agent got stuck, and a
    success shows nothing stuck. Several are kept: one attempt can fail in a way
    the others do not. Collection is lossless; simplify() trims at consumption.
    Falls back to a plain reward/turns summary when a rollout carried no
    transcript (a pass@k-only file), which routes fine but gives a vague hint.
    """
    graded = [a for a in attempts if str(a.get("reward")) in ("0", "1")]
    fails = [a for a in graded if str(a.get("reward")) == "0"]
    picks = (fails or graded or attempts)[:keep]
    out = []
    for a in picks:
        out.append(f"--- attempt reward={a.get('reward')} "
                   f"turns={a.get('turns')} ---")
        for step in a.get("transcript", []) or []:
            out.append(f"$ {step.get('cmd', '')}")
            out.append(f"  {str(step.get('out', ''))[:600]}")
        out.append(f"verifier tail: {str(a.get('test_tail', ''))[:400]}")
    return "\n".join(out)


CONTEXT_FILE_MAX = 256 * 1024


def context_text(work: Path) -> str:
    """Everything in the build context an agent could read inside the image:
    the files under environment/ other than the Dockerfile (which the audit
    already sees). A README the instruction points at lives here."""
    out = []
    root = work / "environment"
    if not root.is_dir():
        return ""
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.name != "Dockerfile" and f.stat().st_size <= CONTEXT_FILE_MAX:
            out.append(f.read_text(errors="replace"))
    return "\n".join(out)


def new_dark_paths(work: Path, task: dict, orig: dict) -> list[str]:
    """Paths the rewritten verifier requires that nothing the agent can read
    names, and that the seed's verifier did not already require unseen.

    Measured on wd-20260903b (299 agentic folds): the static audit alone flags
    100, but 72 of those name every path in a file the image ships (a README
    the instruction points at), which is the agent's favourite way to make a
    task harder and is discoverable. So the build context counts as visible.
    What is left still has to be checked against the container: a verifier
    that asserts /usr/bin/curl exists asks nothing of the agent. The probe
    does that part.
    """
    ctx = context_text(work)
    seen = {**task, "instruction": task["instruction"] + "\n" + ctx}
    before = set(sl.audit(orig)["dark_paths"])
    return [p for p in sl.audit(seen)["dark_paths"]
            if p not in before and ":" not in p and not any(c in p for c in "*?[")]


def _dark_paths_why(missing: list[str]) -> str:
    return ("The verifier requires paths that nothing an agent can see reveals: "
            "they are not named in the instruction, the Dockerfile or any file "
            "in the build context, and they do not exist in the untouched "
            "container: " + ", ".join(missing) + ". An agent that reads only the "
            "instruction and the container cannot know to create them. Name "
            "them where the agent will read them (the instruction, or a file in "
            "the image the instruction points at), or make the verifier stop "
            "depending on them; do not weaken what it checks otherwise.")


def seed_literals(task: dict, src_dir: Path) -> list[str]:
    """What the seed's verifier already depends on unseen, so a rewrite
    answers for the names it added and not for the seed's."""
    return vl.unseen(task["test_state_py"], vl.kind_of(ev._verifier_rel(task)),
                     vl.visible_text(src_dir, instruction=task["instruction"],
                                     dockerfile=task["dockerfile"]))


def new_dark_literals(work: Path, task: dict, baseline) -> list[str]:
    """Key-, label- and filename-shaped names the rewritten verifier depends
    on that the instruction, the Dockerfile and the build context never
    state. The same defect as new_dark_paths for strings that are not paths:
    the report key the policy has to guess, the line label the regex anchors
    on. Five of eight hardened tasks reviewed on wd-20260903b failed on this
    with the work otherwise done."""
    return vl.unseen(task["test_state_py"], vl.kind_of(ev._verifier_rel(task)),
                     vl.visible_text(work, instruction=task["instruction"],
                                     dockerfile=task["dockerfile"]), baseline)


def revalidate(work: Path, image: str, tid: str, task: dict,
               orig: dict | None = None, changed: list[str] | None = None,
               resources: dict | None = None, baseline=None) -> dict:
    """After an adjustment: still builds, still self-consistent, and the
    verifier still fails an untouched workspace. Building here checks the task
    is well-formed; it never runs the solver.

    The null probe replaced an LLM-guessed shortcut. Of the 148 rewrites that
    probe rejected in one week, 29 had "passed" on `cd /app` or `mkdir -p` --
    a verifier green before anything is done, which this probe catches for
    free -- and 100 on the expected artifact printf'd into place by a reader
    who had seen the solution and the verifier. A policy that has seen neither
    cannot write that answer, so those were tasks fit to train on, thrown away
    at a chat call and a sandbox each. The hackability question moved into the
    agent's session, where it has the container and can try for itself.

    Instruction-only fast path. When the only file the retune touched is the
    instruction, nothing that affects the build, the verifier, or the reference
    solution moved: the package still builds and its oracle still passes exactly
    as when it entered the pool, so rebuilding to re-confirm that is the loop's
    most expensive step spent on a known answer — and for an SWE package, the
    one flaminio can least afford. What an instruction edit CAN introduce is
    instruction<->verifier drift, so that is what gets checked: a newly-leaked
    verifier path, or a path the instruction stopped revealing that the verifier
    still needs. Judged before/after, so an SWE test.sh that always references
    repo internals is not mistaken for a fresh dark path."""
    if changed == ["instruction"] and orig is not None:
        if not task["instruction"].strip():
            return {"ok": False, "stage": "empty", "why": "instruction emptied"}
        before, after = sl.audit(orig), sl.audit(task)
        new_leaks = [x for x in after["leaks"] if x not in before["leaks"]]
        new_dark = [p for p in after["dark_paths"] if p not in before["dark_paths"]]
        if new_leaks or new_dark:
            return {"ok": False, "stage": "audit",
                    "why": f"leaks={new_leaks} dark={new_dark}"[:200]}
        return {"ok": True, "fast_path": "instruction_only"}
    # A structural change (a stage cut, the verifier tightened) has to be re-run
    # to be trusted, and that needs a build. On a host without docker -- della,
    # where the evolution loop runs beside the training -- the build runs on
    # Daytona instead: same platform, harness and grading contract as the
    # training rollouts, so an oracle pass there is trust earned on the very
    # environment the task will be solved in. Only when neither docker nor the
    # Daytona probe is available does the change stay unshipped.
    if not shutil.which("docker"):
        # In the box the row will be provisioned at. The probe used to open the
        # harness default (2/4/6) whatever the row said, so a task that fit
        # there and not in its training box (1/2/2 on this corpus) passed here
        # and was starved there, and the timeout read back as "too hard".
        #
        # The same container answers whether the verifier's unseen paths are
        # preconditions or artifacts: a structural rewrite that makes the
        # verifier demand a file the task never names passed the oracle (the
        # reference solution knows the name) and then failed every rollout.
        # That was the instruction-only fast path's audit, never applied here.
        dark = new_dark_paths(work, task, orig) if orig is not None else []
        # Static, so it costs nothing to ask before the probe; reported with
        # whichever failure comes first, so one repair round sees everything.
        names = new_dark_literals(work, task, baseline or ()) if orig is not None else []
        dv = daytona_probe(work, resources=resources, require_paths=dark)
        if dv is None:
            return {"ok": False, "stage": "no_docker",
                    "why": "structural change needs a build; neither docker "
                           "nor Daytona is configured here"}
        also = ("\n\nAlso: " + vl.why(names)) if names else ""
        if not dv.get("ok"):
            return {"ok": False, "stage": dv.get("stage", "daytona"),
                    "why": str(dv.get("why") or f"reward={dv.get('reward')} "
                               f"solve_exit={dv.get('solve_exit')}")[:200] + also,
                    "literals": names, "tail": dv.get("tail", ""),
                    "solve_exit": dv.get("solve_exit"), "measured": dv.get("measured"),
                    "resources": dv.get("resources")}
        missing = dv.get("paths_missing") or []
        if missing:
            return {"ok": False, "stage": "dark_paths", "paths": missing,
                    "why": _dark_paths_why(missing) + also, "literals": names,
                    "solve_exit": dv.get("solve_exit"),
                    "measured": dv.get("measured"), "resources": dv.get("resources")}
        if names:
            return {"ok": False, "stage": "dark_literals", "literals": names,
                    "why": vl.why(names), "solve_exit": dv.get("solve_exit"),
                    "measured": dv.get("measured"), "resources": dv.get("resources")}
        null = daytona_probe(work, shortcut=":", resources=resources) or {}
        if null.get("passed"):
            return {"ok": False, "stage": "null_pass",
                    "why": "verifier passes on the untouched workspace"}
        return {"ok": True, "fast_path": "daytona_oracle",
                "reward": dv.get("reward"), "measured": dv.get("measured"),
                "resources": dv.get("resources")}
    sl.sh(["docker", "rmi", "-f", image], 300)
    ok, tail = sl.build_image(work, image)
    if not ok:
        return {"ok": False, "stage": "build", "why": tail[-200:]}
    oracle = sl.oracle_check(work, image, tid)
    if not oracle.get("ok"):
        return {"ok": False, "stage": "oracle",
                "why": oracle.get("test_tail", "")[-200:]}
    null = sl.shortcut_check(work, image, tid, ":")
    if null.get("passed"):
        return {"ok": False, "stage": "null_pass",
                "why": "verifier passes on the untouched workspace"}
    return {"ok": True}


def _record_codex_traces(rec: dict, value) -> None:
    """Attach durable Codex work directories to the per-signal audit record."""
    if isinstance(value, BaseException):
        paths = [getattr(value, "codex_trace_dir", "")]
    elif isinstance(value, dict):
        paths = value.get("_codex_trace_dirs") or [value.get("_codex_trace_dir", "")]
    else:
        paths = []
    current = rec.setdefault("codex_trace_dirs", [])
    for path in paths:
        if path and path not in current:
            current.append(path)


def provision(measured: dict | None, floor: dict | None, *, box: dict | None = None,
              at_max: bool = False, by: str = "") -> dict | None:
    """A size for the rewritten row from one measurement, and where it came from.

    `floor` is what training gave the seed (the row's own daytona_* filled out
    with the fleet default); `measured` is what the reference solution cost in
    one container, read from its counters; `box` is the container that run
    was in and `by` names who ran it. The result is max(floor, measured) on
    every axis: a measurement sizes the row, and never below the seed, because
    a harder version whose counters read lower is one run's reading, not
    evidence the seed was oversized. With no measurement the floor stands
    (what the fold inherited before there was a measurement); with neither
    there is nothing to write and the row keeps following the fleet default.

    Called twice per rewrite. The agent's own `./sandbox check` reading picks
    the box the loop's probe runs in; the probe's reading, taken in a
    container the agent never touched, is what the row is provisioned from.
    """
    floor = {k: v for k, v in (floor or {}).items()
             if k in RESOURCE_KEYS and v is not None}
    m = measured or {}
    sized = None
    if any(m.get(k) is not None for k in ("mem_peak_mb", "df_used_mb", "cpu_seconds")):
        sized = ds.size_from_oracle(m.get("mem_peak_mb"), m.get("df_used_mb"),
                                    m.get("cpu_seconds"))
    if sized is None and not floor:
        return None
    size = dict(floor)
    if sized:
        for k in RESOURCE_KEYS:
            size[k] = max(floor.get(k) or 0, sized[k])
    return {**size, "source": f"measured:{by}" if sized else "inherited",
            "floor": floor, "sized": sized, "measured": m or None,
            "box": box, "at_max": bool(at_max)}


def _probe_box(new: dict, floor: dict | None) -> dict | None:
    """Where to run the loop's probe: the seed's box, raised to what the agent's
    last passing check measured. The agent's reading picks the box only."""
    return provision(new.get("_measured"), floor, box=new.get("_box"),
                     at_max=bool(new.get("_at_max")), by="agent_check")


def _size_from_probe(work: Path, rec: dict, verdict: dict, floor: dict | None) -> None:
    """After a probe that passed and measured: provision the row from its
    counters rather than the agent's. The agent can edit its copy of the
    sandbox tool; it cannot reach the probe's container."""
    if verdict.get("ok") and verdict.get("measured"):
        _write_provision(work, rec, provision(
            verdict["measured"], floor, box=verdict.get("resources"), by="loop_probe"))


def _write_provision(work: Path, rec: dict, size: dict | None) -> None:
    """Leave the size beside the package, where the fold reads it.

    The daytona_* keys do not live in a package (pack.to_row has no source for
    them), so the fold has to be told. A file in the package directory is a
    record that travels with the rewrite; a value passed in memory is not.
    None removes one left by an earlier attempt on the same package.
    """
    rec["resources"] = size
    path = work / ".resources.json"
    if size is None:
        path.unlink(missing_ok=True)
        return
    path.write_text(json.dumps(size, sort_keys=True) + "\n")


def process_one(rollout: dict, src_dir: Path, out_root: Path,
                resources: dict | None = None) -> dict:
    """Retune one task from its rollout signal.

    `resources` is the box training gives the task: the row's daytona_* filled
    out with the fleet default. The agent works in it, the reference solution
    is measured in it, and the rewrite is provisioned from that measurement
    (never below it) and revalidated at the resulting size.
    """
    tid = rollout["task_id"]
    solved = rollout.get("solved", 0)
    graded = rollout.get("graded", len([r for r in rollout.get("rewards", [])
                                        if str(r) in ("0", "1")]))
    attempts = rollout.get("attempts", [])
    rec: dict = {"task_id": tid, "t_start": time.time(),
                 "solved": solved, "graded": graded,
                 "rollout_source": str(rollout.get("_source", ""))}
    work = out_root / tid
    image = image_tag("fb", tid)
    mark = dict(llm.USAGE)
    try:
        if not graded:
            return {**rec, "status": "ungraded",
                    "why": "rollout produced no graded attempt"}
        if not src_dir.exists() or not (src_dir / "instruction.md").exists():
            return {**rec, "status": "no_pool_dir",
                    "why": f"no task package at {src_dir}"}
        if work.exists():
            shutil.rmtree(work)
        shutil.copytree(src_dir, work)

        task = ev.load(work)
        # Each concurrent invocation gets a unique directory name; write the
        # stable task ID to trace.json so the directory remains attributable.
        task["_task_id"] = tid
        # The names the seed's verifier already depended on unseen, taken from
        # the pool copy before anything here is rewritten.
        baseline = seed_literals(task, src_dir)
        if solved == 0:                                   # 0/k -> easier
            # Retune arm, selectable per run. "chat" (default): one gpt-5.6 call
            # with the trace in the prompt. "codex": agentic, full traces as
            # files + AGENTS.md role (evolve_codex). "none": structural only,
            # ignore the transcript (the no-rollout-info mode). All three feed
            # the SAME downstream leak/dark audit -- only the writing differs.
            arm = os.environ.get("SWE_RETUNE_AGENT", "chat")
            trace = format_trace(attempts)
            # SWE_SIMPLIFY_HINT selects how much guidance a simplify may write
            # into the instruction (none|vague|specific). Default is now vague:
            # the specific level bakes "where to look" hints into hundreds of
            # instructions, and the holdout experiment showed the policy learns
            # hint-following that does not transfer to unhinted tasks.
            hint_lvl = os.environ.get("SWE_SIMPLIFY_HINT", "vague")
            if arm == "codex":
                try:
                    import evolve_codex as ec
                    new = ec.simplify_codex(task, solved=0, attempts=graded,
                                            trajectory=trace, hint=hint_lvl)
                    _record_codex_traces(rec, new)
                except Exception as e:  # noqa: BLE001 -- the task stays as it is
                    _record_codex_traces(rec, e)
                    return {**rec, "status": "agent_failed", "action": "simplify",
                            "why": f"{type(e).__name__}: {e}"[:200]}
            else:
                new = ev.simplify(task, solved=0, attempts=graded,
                                  trajectory=("" if arm == "none" else trace),
                                  hint=("none" if arm == "none" else hint_lvl))
            rec["action"], rec["hint"] = "simplify", new.get("_hint")
        elif solved == graded:                            # k/k -> harder
            # Which axis to evolve along is not the agent's call. The choice is
            # scored against the whole pool -- L(o) for whether this seed has a
            # foothold at all, D(f) for family balance, P(o) for how often the
            # operator has been used -- and letting a model that sees only this
            # one task pick from all forty collapses the pool onto whichever
            # transformation is easiest to write. The scan also raises Blocked
            # when the seed supports nothing, which is worth knowing BEFORE
            # spending a session and two container builds on it.
            uo, uf = ev.history_from_pool(
                [p for p in out_root.glob("*") if p.is_dir()])
            try:
                shortlist = llm.operator_shortlist(
                    {"task_id": tid, "instruction": task["instruction"],
                     "dockerfile": task["dockerfile"],
                     "solution": task["solve_sh"], "env_files": {}}, uo, uf)
            except llm.Blocked as e:
                return {**rec, "status": "evolve_blocked", "action": "evolve",
                        "why": str(e)[:200]}
            # The head of the list is what the chat operator below gets, since
            # it cannot choose; the agent gets the whole list and reports back
            # which one it used.
            fam, operator, definition = shortlist[0]
            rec["operator"], rec["family"] = operator, fam

            if os.environ.get("SWE_RETUNE_AGENT", "chat") == "codex":
                # No chat fallback. Measured over 434 agent sessions, every
                # fallback followed a timeout or a "verifier weakened" verdict
                # that was itself wrong (the heuristic counted test functions
                # while the spec asked for four roles), and neither is a thing
                # one chat call does better; it only put the weaker method's
                # output into the fold as if the agent had written it. A
                # failed session leaves the task as it was, and says why.
                try:
                    import evolve_codex as ec
                    from evolve_codex import Blocked as ec_Blocked
                    agent_task = {**task, "_solved": solved, "_attempts": graded,
                                  "_resources": resources}
                    new = ec.evolve_agentic(agent_task, "harder", attempts=attempts,
                                            operator=shortlist)
                    _record_codex_traces(rec, new)
                    rec["action"], rec["hint"] = "evolve", new.get("_hint")
                    rec["agent_validated"] = new.get("_agent_validated")
                except ec_Blocked as e:
                    _record_codex_traces(rec, e)
                    # It read the package and said the axis does not fit, or
                    # that it cannot be made harder honestly. Take the answer:
                    # falling through to the chat operator asks a weaker method
                    # the same question, which is what offering the exit was
                    # meant to avoid.
                    rec["action"] = "keep"
                    return {**rec, "status": "kept", "why": str(e)[:200],
                            "out_dir": str(work)}
                except Exception as e:  # noqa: BLE001 -- the task stays as it is
                    _record_codex_traces(rec, e)
                    return {**rec, "status": "agent_failed", "action": "evolve",
                            "why": f"{type(e).__name__}: {e}"[:200]}
            else:
                new = None

            if new is None:
                try:
                    new = ev.evolve(task, seed_id=tid, operator=operator)
                except llm.Blocked as e:
                    return {**rec, "status": "evolve_blocked",
                            "action": "evolve", "why": str(e)[:200]}
                rec["action"] = "evolve"
            rec["operator"], rec["family"] = (new.get("_operator", operator),
                                              new.get("_family", fam))
        else:                                             # in band -> keep
            rec["action"] = "keep"
            return {**rec, "status": "kept", "out_dir": str(work)}

        changed = [key for key in ev.file_map(new) if new[key] != task[key]]
        for key, rel in ev.file_map(new).items():
            (work / rel).write_text(new[key])
        # Files the agent added or edited outside the four that round-trip. The
        # agent validated the package WITH them, so writing only the four here
        # ships something it never ran: a Dockerfile whose COPY sources are not
        # in the build context. Their paths count as changes too, or a package
        # whose only edit was a new fixture takes the instruction-only fast path
        # and never rebuilds.
        for rel, blob in (new.get("_extra_files") or {}).items():
            dest = work / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            if rel not in changed:
                changed.append(rel)
        if rec["action"] == "evolve":
            (work / ".provenance.json").write_text(json.dumps(
                {"operator": new.get("_operator"),
                 "family": new.get("_family"), "parent": tid}))

        box = _probe_box(new, resources)
        _write_provision(work, rec, box)
        v = revalidate(work, image, tid, new, orig=task, changed=changed,
                       resources=box, baseline=baseline)
        rec["revalidate"] = v
        _size_from_probe(work, rec, v, resources)
        if (
            not v["ok"]
            and rec["action"] == "evolve"
            and v.get("stage") in ("daytona_oracle", "dark_paths", "dark_literals")
        ):
            # The structural operator regenerates instruction, solution and
            # verifier together, and the hard part is making the three agree:
            # a sampled package had a 537-line solve.sh and a 518-line
            # test_state.py, and the solution did not pass its own verifier
            # (reward=0.0, solve_exit=1). About 60% of TW evolutions die this way,
            # which is why the k/k direction stalled once SWE left the mix.
            #
            # synthesize already runs a blind oracle-repair pass, but it only
            # reads the files -- it has never seen the task execute. This run
            # has: the verdict carries the real exit code and output tail. Feed
            # that back for one more repair and revalidate again. Repairing the
            # task is the point; rewriting the instruction instead would leave
            # the verifier as weak as it was.
            # What the agent gets to read: the verdict's own diagnosis first
            # (a run the box starved, paths the verifier demands unseen), then
            # whatever the run printed. A tail alone showed "No space left on
            # device" without saying the box was the training size.
            tail = "\n\n".join(s for s in (v.get("why"), v.get("tail")) if s)
            code = int(v["solve_exit"]) if v.get("solve_exit") is not None else 1
            fixed = None
            if os.environ.get("SWE_RETUNE_AGENT", "chat") == "codex":
                # Back to the session that wrote the files, with the failure it
                # never saw. A fresh repair session, chat or agentic, has to
                # rediscover from the files alone why they look the way they
                # do; the one that wrote them is on disk and can be resumed.
                try:
                    import evolve_codex as ec
                    from evolve_codex import Blocked as ec_Blocked
                    if new.get("_codex_trace_dir"):
                        fixed = ec.resume_agentic(new, tail, code)
                    else:
                        fixed = ec.repair_oracle_codex(new, tail, code)
                    _record_codex_traces(rec, fixed)
                except ec_Blocked as e:
                    _record_codex_traces(rec, e)
                    log.info("%s oracle repair declined: %s", tid, str(e)[:200])
                except Exception as e:  # noqa: BLE001 -- the failed verdict stands
                    _record_codex_traces(rec, e)
                    log.warning("%s oracle repair failed: %s", tid, str(e)[:200])
            else:
                try:
                    fixed = ev.repair_oracle(new, tail, code)
                except Exception:  # noqa: BLE001 -- repair is best-effort
                    fixed = None
            if fixed is not None:
                repaired = [k for k in ev.file_map(fixed) if fixed[k] != new[k]]
                extra = fixed.get("_extra_files") or {}
                if repaired or extra:
                    for key, rel in ev.file_map(fixed).items():
                        (work / rel).write_text(fixed[key])
                    for rel, blob in extra.items():
                        dest = work / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(blob)
                    box = _probe_box(fixed, resources)
                    _write_provision(work, rec, box)
                    v2 = revalidate(work, image, tid, fixed, orig=task,
                                    changed=[k for k in ev.file_map(fixed)
                                             if fixed[k] != task[k]] + list(extra),
                                    resources=box, baseline=baseline)
                    _size_from_probe(work, rec, v2, resources)
                    rec["oracle_repair"] = {"files": repaired + list(extra),
                                            "ok": v2["ok"]}
                    if v2["ok"]:
                        rec["revalidate"] = v2
                        return {**rec, "status": "ok", "out_dir": str(work)}
                    v = v2
                    rec["revalidate"] = v2
        if not v["ok"]:
            return {**rec, "status": f"revalidate_{v['stage']}_failed",
                    "why": v.get("why", "")}
        return {**rec, "status": "ok", "out_dir": str(work)}
    except Exception as e:  # noqa: BLE001
        return {**rec, "status": "error", "why": f"{type(e).__name__}: {e}"[:200]}
    finally:
        rec["usage"] = llm.usage_since(mark)
        rec["t_end"] = time.time()
        # The instruction-only fast path builds nothing, so there is no image to
        # remove; and on a docker-less host the call itself would raise out of
        # the finally and mask the real result. Clean up only when docker is
        # actually present.
        if shutil.which("docker"):
            sl.sh(["docker", "rmi", "-f", image], 300)



def load_rollouts(path: Path) -> dict:
    """task_id -> rollout record, from a solve_eval (or training-side) file."""
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("task_id") and r.get("status") in (None, "solved", "unsolved"):
            r["_source"] = path.name
            out[r["task_id"]] = r
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollouts", required=True,
                    help="a solver's rollouts (solve_eval --keep-trace, or the "
                         "training side's returned trace); one task per line")
    ap.add_argument("--pool", required=True,
                    help="directory holding each task's package, by task id")
    ap.add_argument("--out", default="data/feedback-r1")
    ap.add_argument("--results", required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(Path(args.results).with_suffix(".log")),
                  logging.StreamHandler()])

    rollouts = load_rollouts(Path(args.rollouts))
    done = set()
    out_path = Path(args.results)
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["task_id"])
    todo = [tid for tid in rollouts if tid not in done]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    pool = Path(args.pool)
    log.info("rollouts %d tasks, %d already done, workers=%d",
             len(todo), len(done), args.workers)

    counts: dict[str, int] = {}
    actions: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex, \
            open(out_path, "a") as fh:
        futs = [ex.submit(process_one, rollouts[t], pool / t, out_root)
                for t in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            actions[rec.get("action", "-")] = actions.get(rec.get("action", "-"), 0) + 1
            log.info("[%d/%d] %s solved=%s/%s -> %s (%s) | %s",
                     n, len(todo), rec["task_id"], rec.get("solved"),
                     rec.get("graded"), rec.get("action", "-"), rec["status"],
                     counts)
    log.info("done. status=%s actions=%s", counts, actions)


if __name__ == "__main__":
    main()
