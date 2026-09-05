#!/usr/bin/env python3
"""The feedback pipeline: adjust one task from a solver's rollouts.

This is the training pipeline's task-adjustment stage, and it does one thing:
read a solver's rollouts and move the task toward the usable band per the
project algorithm:

    0 of k solved   easier, with a hint read off a failing trajectory
    k of k solved   harder, one operator, the RST way
    in between      already discriminating; no signal is emitted

It does NOT run rollouts. That is the training side's job — RL produces them on
the model being trained — and folding a solver into this stage would weld the
wrong model into it and blur the line the rest of this project draws: the data
side does not measure difficulty, it consumes the measurement. So the input is
a signal and the rollout records it names, and swapping the solver that
produced them changes the input, not this stage.

`process_one` works in one rewrite directory (LAYOUT.md): the loop has copied
the input revision to `package/` and hardlinked the rollout records under
`package/traces/`; this rewrites the package in place, re-validates whatever
changed — build, oracle, null probe: an adjustment can break a task, and
shipping a broken one is worse than not adjusting it — and returns the verdict.
The loop writes the record and decides what happens to the package.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import synth_loop as sl
import synth_client as llm
import evolve as ev
import derive_sizing as ds
import task_size as ts
import verifier_literals as vl
from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

log = logging.getLogger("feedback")

# Daytona revalidation (hosts without docker, e.g. della). The probe runs in
# the training venv with the Daytona env sourced -- the same platform, harness
# and grading contract the rollouts use, so passing it is a STRONGER build+
# oracle check than the local docker shim it stands in for. The interpreter is
# the venv's when TRL_VENV names one, else the one this process runs in; when
# the credential file is absent this host simply has no build story and
# structural retunes are declined as before.
DAYTONA_VENV_PY = (str(Path(os.environ["TRL_VENV"]) / "bin" / "python")
                   if os.environ.get("TRL_VENV") else sys.executable)
DAYTONA_ENV_FILE = os.environ.get(
    "DAYTONA_ENV_FILE", os.path.expanduser("~/.config/daytona/env"))


_INFRA_RE = re.compile(
    r"Timeout|Bad ?Gateway|InternalServer|50[234]|[Cc]onnection|timed? ?out|"
    r"no stdout|TooManyRequests|429")


RESOURCE_KEYS = ("cpu", "mem_gb", "disk_gb")


def daytona_probe(work: Path, shortcut: str | None = None,
                  resources: dict | None = None,
                  require_paths: list[str] | None = None,
                  pretest_file: Path | None = None) -> dict | None:
    """Run daytona_revalidate.py on this package; None when unconfigured.

    `resources` is the box to run it in: the size the row is provisioned at in
    training, so an oracle pass here is a pass where the task will be run. A
    key left None falls to the harness default for this process's env.
    `require_paths` come back in the verdict as `paths_missing`: the ones the
    untouched workspace does not have. `pretest_file` is the rewrite's
    pretest.json, the row's pin hook: the probe grades with it, as training
    does, so a solution that disturbs a pinned reference fails here and not
    only in every rollout afterwards.
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
    if pretest_file is not None:
        cmd += ["--pretest-file", str(pretest_file)]
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


def read_traces(rewrite: layout.RewriteDir) -> list[tuple[dict, list[dict]]]:
    """The rollout records the loop hardlinked under the package, in attempt
    order: (rollout header, turns) each, as rollout_record reads them."""
    out = []
    for p in sorted(rewrite.traces.glob("attempt-*.jsonl")):
        try:
            out.append(rollout_record.read_record(p))
        except (OSError, ValueError) as e:
            log.warning("unreadable trace %s: %s", p, e)
    return out


def format_trace(records: list[tuple[dict, list[dict]]], keep: int = 3) -> str:
    """Turn rollout records into the text a chat hint is drawn from.

    Prefer failing attempts — a hint aims at where the agent got stuck, and a
    success shows nothing stuck. Several are kept: one attempt can fail in a way
    the others do not. The records are whole; the trimming here is for the
    chat prompt, which has a budget the files do not.
    """
    def reward(head: dict) -> float | None:
        try:
            return float(head.get("reward"))
        except (TypeError, ValueError):
            return None

    graded = [r for r in records if reward(r[0]) in (0.0, 1.0)]
    fails = [r for r in graded if reward(r[0]) == 0.0]
    picks = (fails or graded or records)[:keep]
    out = []
    for head, turns in picks:
        out.append(f"--- attempt reward={head.get('reward')} "
                   f"turns={head.get('turns')} ---")
        for t in turns:
            typed = "".join(t.get("keystrokes") or [t.get("raw", "")])
            out.append(f"$ {typed.rstrip()}")
            out.append(f"  {str(t.get('output', ''))[:600]}")
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


def _kind(task: dict) -> str:
    return "python" if ev._verifier_rel(task).endswith(".py") else "shell"


def revalidate(work: Path, image: str, tid: str, task: dict,
               orig: dict | None = None, changed: list[str] | None = None,
               resources: dict | None = None, baseline=None,
               pretest_file: Path | None = None) -> dict:
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
        # One rung above the seed, by the size rule task_size.py documents;
        # the agent's own check applies it first, this is the backstop.
        step = (ts.violations(ts.size_of(orig["solve_sh"], orig["test_state_py"], _kind(orig)),
                              ts.size_of(task["solve_sh"], task["test_state_py"], _kind(task)))
                if orig is not None else [])
        dv = daytona_probe(work, resources=resources, require_paths=dark,
                           pretest_file=pretest_file)
        if dv is None:
            return {"ok": False, "stage": "no_docker",
                    "why": "structural change needs a build; neither docker "
                           "nor Daytona is configured here"}
        also = (("\n\nAlso: " + vl.why(names)) if names else "") + \
               (("\n\nAlso: " + ts.why(step)) if step else "")
        if not dv.get("ok"):
            return {"ok": False, "stage": dv.get("stage", "daytona"),
                    "why": str(dv.get("why") or f"reward={dv.get('reward')} "
                               f"solve_exit={dv.get('solve_exit')}")[:200] + also,
                    "literals": names, "tail": dv.get("tail", ""),
                    "solve_exit": dv.get("solve_exit"), "measured": dv.get("measured"),
                    "resources": dv.get("resources")}
        missing = dv.get("paths_missing") or []
        # The two audits of what the verifier demands unseen are advice, not
        # a verdict. Measured on wd-20260904a over 464 rewrites, the names
        # audit flagged 130 literals without its seed baseline and none with
        # it, and every one of the 130 was a false positive (fstab column
        # names, language keywords, environment variables, a jupyter output
        # line); the paths audit rejected nothing across 621 signals. A gate
        # with no measured precision has no business discarding a session, so
        # both ride along in the record for whoever reads it and for the
        # agent's next prompt, and the size rule below stays the gate.
        advice = {"dark_paths": missing, "dark_literals": names}
        if step:
            return {"ok": False, "stage": "step_size", "step": step, "why": ts.why(step),
                    "advice": advice, "solve_exit": dv.get("solve_exit"),
                    "measured": dv.get("measured"), "resources": dv.get("resources")}
        null = daytona_probe(work, shortcut=":", resources=resources,
                             pretest_file=pretest_file) or {}
        if null.get("passed"):
            return {"ok": False, "stage": "null_pass",
                    "why": "verifier passes on the untouched workspace"}
        return {"ok": True, "fast_path": "daytona_oracle", "advice": advice,
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


def verdicts_of(v: dict | None) -> dict:
    """The revalidation verdict as rewrite.json carries it (LAYOUT.md): what
    the oracle said, and the two audits and the size rule as lists."""
    v = v or {}
    advice = v.get("advice") or {}
    stage = v.get("stage")
    if v.get("ok"):
        oracle = "skipped" if v.get("fast_path") == "instruction_only" else "pass"
    elif stage == "step_size":
        oracle = "pass"                     # the solution passed; the size did not
    elif stage in ("daytona_oracle", "oracle", "build", "null_pass"):
        oracle = "fail"
    elif stage in ("daytona_error", "no_docker"):
        oracle = "error"
    else:
        oracle = "skipped" if v else None   # audit/empty: nothing was built
    return {"oracle": oracle,
            "dark_paths": list(advice.get("dark_paths") or v.get("paths_missing") or []),
            "dark_literals": list(advice.get("dark_literals") or v.get("literals") or []),
            "step": list(v.get("step") or [])}


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


def _size_from_probe(rec: dict, verdict: dict, floor: dict | None) -> None:
    """After a probe that passed and measured: provision the row from its
    counters rather than the agent's. The agent can edit its copy of the
    sandbox tool; it cannot reach the probe's container. The size travels in
    the record; the loop writes it into rewrite.json and the folded row."""
    if verdict.get("ok") and verdict.get("measured"):
        rec["resources"] = provision(
            verdict["measured"], floor, box=verdict.get("resources"), by="loop_probe")


def _evolve_retrying_the_filter(ec, rec: dict, tid: str, rewrite: layout.RewriteDir,
                                agent_task: dict, shortlist) -> dict:
    """One agent session, retried when the provider's classifier stops it.

    The cybersecurity classifier fires late in a session whose context carries
    a reverse-engineering task's own material, and probabilistically: over the
    three seeds it has ever hit, 11 of 21 sessions were stopped and 10 ran
    to the end. A stopped session says nothing about the task, so a fresh one
    is started -- fresh, not resumed, since the flagged context is exactly what
    a resume would carry back. At the measured rate two retries take the
    per-task failure rate from about a half to about one in seven.
    """
    for attempt in range(1, ec.CYBER_RETRIES + 2):
        try:
            return ec.evolve_agentic(rewrite, agent_task, "harder", operator=shortlist)
        except ec.Filtered:
            rec["cyber_filtered"] = attempt
            if attempt > ec.CYBER_RETRIES:
                raise
            log.info("%s: the provider's cybersecurity classifier stopped the session "
                     "(attempt %d of %d); starting a fresh one", tid, attempt,
                     ec.CYBER_RETRIES + 1)


def _write_back(work: Path, new: dict) -> None:
    """The four files that round-trip through the task dict, at the paths the
    rewrite left them. For the agentic arms this rewrites what the agent
    already wrote; for the chat arm it is the write."""
    for key, rel in ev.file_map(new).items():
        dest = work / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(new[key])


def _changed(task: dict, new: dict) -> list[str]:
    """What moved against the input revision: the round-tripping files by key
    and every other file by path. A package whose only edit was a new fixture
    must not take the instruction-only fast path."""
    changed = [key for key in ev.file_map(new) if new[key] != task[key]]
    for rel in new.get("_support_changed") or []:
        if rel not in changed:
            changed.append(rel)
    return changed


def _done(rec: dict, status: str, *, stage: str | None = None,
          reason: str | None = None) -> dict:
    """In place, so what the `finally` below adds (usage, timing) is on the
    dict the caller holds."""
    rec.update({"status": status, "stage": stage, "reason": (reason or "")[:300]})
    return rec


def process_one(rewrite: layout.RewriteDir, signal: dict, *, job: str,
                seed_dir: Path, resources: dict | None = None,
                history: tuple[dict, dict] | None = None) -> dict:
    """Retune one task in its rewrite directory, from the signal that asked.

    `job` is "harder" (an all-pass group) or "easier" (all-fail). `seed_dir`
    is the revision the package was copied from, kept for the before/after
    audits. `resources` is the box training gives the task: the row's
    daytona_* filled out with the fleet default. The agent works in it, the
    reference solution is measured in it, and the rewrite is provisioned from
    that measurement (never below it) and revalidated at the resulting size.
    `history` is (used_ops, used_fams) over every accepted rewrite, the
    diversity terms the operator shortlist is scored with.

    Returns the record the loop writes into rewrite.json: `status` is
    accepted, rejected, blocked, failed or kept; `stage` and `reason` say
    why; `verdicts` and `resources` are as LAYOUT.md gives them.
    """
    tid = signal["task"]
    solved = int(signal.get("solved") or 0)
    graded = int(signal.get("total") or len(signal.get("attempts") or []))
    work = rewrite.package
    image = image_tag("fb", tid)
    # Retune arm, selectable per run. "chat" (default): one gpt-5.6 call with
    # the trace in the prompt. "codex": agentic, full traces as files +
    # AGENTS.md role (evolve_codex). "none": structural only, ignore the
    # transcript (the no-rollout-info mode). All three feed the SAME
    # downstream leak/dark audit -- only the writing differs.
    arm = os.environ.get("SWE_RETUNE_AGENT", "chat")
    used_ops, used_fams = history or ({}, {})
    rec: dict = {"task": tid, "job": job, "arm": arm, "solved": solved, "graded": graded,
                 "verdicts": verdicts_of(None), "resources": None, "t_start": time.time()}
    mark = dict(llm.USAGE)
    try:
        if not graded:
            return _done(rec, "failed", stage="ungraded",
                         reason="the signal carries no graded attempt")
        ec = None
        if arm == "codex":
            import evolve_codex as ec  # noqa: PLC0415 -- optional arm, faked in tests

        task = ev.load(work)
        task["_task_id"] = tid
        task["_seed_dir"] = str(seed_dir)
        task["_solved"], task["_attempts"] = solved, graded
        task["_resources"] = resources
        # The row's pin hook, as the loop snapshotted it beside rewrite.json
        # (None for a row without one). The agent's tool gets a copy under
        # run/ to grade the way training does; the loop's probe reads the
        # snapshot itself, which the session cannot reach.
        task["_pretest"] = layout.read_pretest(rewrite.pretest)
        pretest_file = rewrite.pretest if task["_pretest"] else None
        # The names the seed's verifier already depended on unseen, taken from
        # the input revision before anything here is rewritten.
        baseline = seed_literals(task, seed_dir)
        rec["action"] = "simplify" if job == "easier" else "evolve"

        if job == "easier":                                   # 0/k -> easier
            # SWE_SIMPLIFY_HINT selects how much guidance a simplify may write
            # into the instruction (none|vague|specific). Default is vague:
            # the specific level bakes "where to look" hints into hundreds of
            # instructions, and the holdout experiment showed the policy learns
            # hint-following that does not transfer to unhinted tasks.
            hint_lvl = os.environ.get("SWE_SIMPLIFY_HINT", "vague")
            if arm == "codex":
                try:
                    new = ec.simplify_codex(rewrite, task, solved=solved, attempts=graded,
                                            hint=hint_lvl)
                except Exception as e:  # noqa: BLE001 -- the task stays as it is
                    return _done(rec, "failed", stage="agent",
                                 reason=f"{type(e).__name__}: {e}")
            else:
                trace = "" if arm == "none" else format_trace(read_traces(rewrite))
                new = ev.simplify(task, solved=solved, attempts=graded, trajectory=trace,
                                  hint=("none" if arm == "none" else hint_lvl))
            rec["hint"] = new.get("_hint")
        else:                                                 # k/k -> harder
            # Which axis to evolve along is not the agent's call. The choice is
            # scored against the whole pool -- L(o) for whether this seed has a
            # foothold at all, D(f) for family balance, P(o) for how often the
            # operator has been used -- and letting a model that sees only this
            # one task pick from all forty collapses the pool onto whichever
            # transformation is easiest to write. The scan also raises Blocked
            # when the seed supports nothing, which is worth knowing BEFORE
            # spending a session and two container builds on it.
            try:
                shortlist = llm.operator_shortlist(
                    {"task_id": tid, "instruction": task["instruction"],
                     "dockerfile": task["dockerfile"],
                     "solution": task["solve_sh"], "env_files": {}}, used_ops, used_fams)
            except llm.Blocked as e:
                return _done(rec, "blocked", stage="operator", reason=str(e))
            # The head of the list is what the chat operator below gets, since
            # it cannot choose; the agent gets the whole list and reports back
            # which one it used.
            fam, operator, definition = shortlist[0]
            rec["operator"], rec["family"] = operator, fam
            if arm == "codex":
                # No chat fallback. Measured over 434 agent sessions, every
                # fallback followed a timeout or a "verifier weakened" verdict
                # that was itself wrong (the heuristic counted test functions
                # while the spec asked for four roles), and neither is a thing
                # one chat call does better; it only put the weaker method's
                # output into the fold as if the agent had written it. A
                # failed session leaves the task as it was, and says why.
                try:
                    new = _evolve_retrying_the_filter(ec, rec, tid, rewrite, task, shortlist)
                    rec["hint"] = new.get("_hint")
                    rec["agent_validated"] = new.get("_agent_validated")
                except ec.Blocked as e:
                    # It read the package and said the axis does not fit, or
                    # that it cannot be made harder honestly. Take the answer:
                    # falling through to the chat operator asks a weaker method
                    # the same question, which is what offering the exit was
                    # meant to avoid.
                    return _done(rec, "kept", stage="agent", reason=str(e))
                except Exception as e:  # noqa: BLE001 -- the task stays as it is
                    return _done(rec, "failed", stage="agent",
                                 reason=f"{type(e).__name__}: {e}")
            else:
                try:
                    new = ev.evolve(task, seed_id=tid, operator=operator)
                except llm.Blocked as e:
                    return _done(rec, "blocked", stage="operator", reason=str(e))
            rec["operator"], rec["family"] = (new.get("_operator", operator),
                                              new.get("_family", fam))

        _write_back(work, new)
        changed = _changed(task, new)
        box = _probe_box(new, resources)
        rec["resources"] = box
        v = revalidate(work, image, tid, new, orig=task, changed=changed,
                       resources=box, baseline=baseline, pretest_file=pretest_file)
        rec["revalidate"] = v
        _size_from_probe(rec, v, resources)
        if (
            not v["ok"]
            and rec["action"] == "evolve"
            and v.get("stage") in ("daytona_oracle", "step_size")
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
            if arm == "codex":
                # Back to the session that wrote the files, with the failure it
                # never saw. A fresh repair session, chat or agentic, has to
                # rediscover from the files alone why they look the way they
                # do; the one that wrote them is on disk and can be resumed.
                try:
                    if new.get("_session"):
                        fixed = ec.resume_agentic(rewrite, new, tail, code)
                    else:
                        fixed = ec.repair_oracle_codex(rewrite, new, tail, code)
                except ec.Blocked as e:
                    log.info("%s oracle repair declined: %s", tid, str(e)[:200])
                except Exception as e:  # noqa: BLE001 -- the failed verdict stands
                    log.warning("%s oracle repair failed: %s", tid, str(e)[:200])
            else:
                try:
                    fixed = ev.repair_oracle(new, tail, code)
                except Exception:  # noqa: BLE001 -- repair is best-effort
                    fixed = None
            if fixed is not None:
                repaired = [k for k in ev.file_map(fixed) if fixed[k] != new[k]]
                support = list(fixed.get("_support_changed") or [])
                if repaired or set(support) != set(new.get("_support_changed") or []):
                    _write_back(work, fixed)
                    box = _probe_box(fixed, resources)
                    rec["resources"] = box
                    changed = _changed(task, fixed)
                    v2 = revalidate(work, image, tid, fixed, orig=task, changed=changed,
                                    resources=box, baseline=baseline,
                                    pretest_file=pretest_file)
                    _size_from_probe(rec, v2, resources)
                    rec["oracle_repair"] = {"files": repaired + support, "ok": v2["ok"]}
                    v = v2
                    rec["revalidate"] = v2
        rec["changed"] = changed
        rec["verdicts"] = verdicts_of(v)
        if not v["ok"]:
            return _done(rec, "rejected", stage=v.get("stage", "revalidate"),
                         reason=v.get("why", ""))
        return _done(rec, "accepted", stage=v.get("fast_path", "ok"))
    except Exception as e:  # noqa: BLE001
        return _done(rec, "failed", stage="error", reason=f"{type(e).__name__}: {e}")
    finally:
        rec["usage"] = llm.usage_since(mark)
        rec["t_end"] = time.time()
        # The instruction-only fast path builds nothing, so there is no image to
        # remove; and on a docker-less host the call itself would raise out of
        # the finally and mask the real result. Clean up only when docker is
        # actually present.
        if shutil.which("docker"):
            sl.sh(["docker", "rmi", "-f", image], 300)
