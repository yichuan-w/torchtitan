#!/usr/bin/env python3
"""The container a task-evolution agent works in, one per session.

The agent's only check used to be `./validate`: a full image build, the
reference solution, the verifier -- minutes per call and nothing in between.
No way to run one command in the environment, read a log, or see which check
failed with the state still there; sessions spent twenty minutes polling two
of those and timed out. This hands the agent the container itself.

`./sandbox` in the package directory is a shell wrapper over this file:

    ./sandbox up            build the image, boot a container, seed the
                            workspace, start the entrypoint (minutes). The
                            box is the size training gives this task, from
                            run/resources.json; --max opens it at the
                            platform ceiling instead
    ./sandbox exec 'CMD'    run CMD inside, as root, from the image's workdir;
                            --timeout N (default 120 s); exits with CMD's code
    ./sandbox oracle        copy solution/ in, run solve.sh, grade with the
                            package's verifier; prints the reward. A package
                            with protected paths/commands (tests/
                            protected_paths.json, or the lists its row
                            carries) is digested right before solve.sh runs
                            and re-digested before the verifier; a difference
                            grades 0
    ./sandbox grade         grade the current state without running anything;
                            protected entries are held to the digests taken
                            at up
    ./sandbox reset         down, then up: a fresh container from the current
                            Dockerfile (--max as for up)
    ./sandbox check         reset; grade, which must FAIL (a verifier that
                            passes an untouched workspace pays for nothing);
                            oracle, which must pass; then the names audit:
                            every key, label or filename the verifier depends
                            on has to be stated where an agent can read it.
                            Prints VERDICT: pass|fail.
                            The oracle run is measured (memory peak, cpu
                            seconds, disk). A run the box cut short -- OOM
                            kill, disk full, out of time on its cores -- is
                            reported as such; --max runs the same check at
                            the platform ceiling, where nothing truncates
                            the reading.
    ./sandbox down          delete the container
    ./sandbox status

`up` starts a server process that holds the harness's sandbox context open --
the same boot path, heartbeat and teardown the training rollouts use -- and
answers requests over a unix socket; every other subcommand is a client that
sends one request and prints the answer. State lives in run/sandbox.json
beside the package, so the agent can read it and the harness can tear the
container down when the session ends. The socket is under /tmp because a unix
socket cannot be bound on the GPFS mount the package lives on; nothing durable
is written there.

oracle and grade re-read the package every time, so an edited verifier or
solution is picked up without a reset; an edited Dockerfile needs `reset`, and
so does an edited tests/protected_paths.json: grade refuses until the baseline
is retaken at up.
Every check's verdict is appended to run/checks.jsonl, which the caller reads
to learn whether the agent ever saw its own rewrite pass, and what the
reference solution cost when it did: that measurement, not a number anyone
wrote down, is what sizes the rewritten task's sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from derive_sizing import CEILING  # noqa: E402 -- stdlib-only sibling
import task_size as ts  # noqa: E402 -- stdlib-only sibling
import verifier_literals as vl  # noqa: E402 -- stdlib-only sibling

EXEC_TIMEOUT = 120
SOLVE_TIMEOUT = 900
BOOT_TIMEOUT = int(os.environ.get("TT_DAYTONA_CREATE_TIMEOUT", "900")) + 300


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _state_path(pkg: Path) -> Path:
    return pkg / "run" / "sandbox.json"


def _read_state(pkg: Path) -> dict:
    try:
        return json.loads(_state_path(pkg).read_text())
    except (OSError, ValueError):
        return {}


def _write_state(pkg: Path, state: dict) -> None:
    path = _state_path(pkg)
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming = path.with_suffix(".json.incoming")
    incoming.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    os.replace(incoming, path)


def _box(pkg: Path, at_max: bool) -> dict:
    """The size to open the container at.

    run/resources.json is written by the harness before the session: the size
    the row is provisioned at in training, so the agent works, and measures,
    where the task will actually run. Without it (a package driven by hand) the
    harness defaults apply, and the state says so. --max is the platform
    ceiling, for a solution that has outgrown the training box.
    """
    if at_max:
        return {**CEILING, "source": "ceiling"}
    try:
        got = json.loads((pkg / "run" / "resources.json").read_text())
    except (OSError, ValueError):
        return {"cpu": None, "mem_gb": None, "disk_gb": None,
                "source": "harness_default (no run/resources.json)"}
    return {"cpu": got.get("cpu"), "mem_gb": got.get("mem_gb"),
            "disk_gb": got.get("disk_gb"),
            "source": got.get("source") or "run/resources.json"}


def _pretest_of(pkg: Path) -> tuple[str, str] | None:
    """The row's pin hook, run/pretest.json, written by the harness before the
    session the way run/resources.json is: (check script, the environment
    identity its pins were captured against). None for a package driven by
    hand or a row that carries no check; the container then grades with the
    verifier alone, as training does for such a row."""
    try:
        got = json.loads((pkg / "run" / "pretest.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict) or not got.get("pre_test_sh"):
        return None
    return str(got["pre_test_sh"]), str(got.get("pretest_env_identity") or "")


def _box_str(box: dict) -> str:
    return (f"cpu={box.get('cpu')} mem_gb={box.get('mem_gb')} "
            f"disk_gb={box.get('disk_gb')}")


def _append_check(pkg: Path, record: dict) -> None:
    path = pkg / "run" / "checks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

def _request(state: dict, op: str, *, wait: int = 60, **fields) -> dict:
    sock_path = state.get("sock")
    if not sock_path:
        return {"ok": False, "error": "no sandbox is up (run ./sandbox up)"}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(wait + 60)
    try:
        s.connect(sock_path)
    except OSError as e:
        return {"ok": False, "error": f"sandbox server not reachable ({e}); "
                                      "run ./sandbox status, then ./sandbox up"}
    with s:
        s.sendall((json.dumps({"op": op, **fields}) + "\n").encode())
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            # The request is newline-terminated, so a server that has already
            # answered and closed (ENOTCONN here on BSD sockets) had all of
            # it; the answer is waiting to be read.
            pass
        chunks = []
        while True:
            try:
                b = s.recv(1 << 16)
            except socket.timeout:
                return {"ok": False, "error": f"no answer from the sandbox server "
                                              f"within {wait + 60}s"}
            if not b:
                break
            chunks.append(b)
    try:
        return json.loads(b"".join(chunks).decode())
    except ValueError:
        return {"ok": False, "error": "unreadable answer from the sandbox server"}


def _alive(state: dict) -> bool:
    return state.get("status") == "ready" and _request(state, "ping", wait=10).get("ok", False)


def cmd_status(pkg: Path) -> int:
    state = _read_state(pkg)
    if not state:
        print("no sandbox: run ./sandbox up")
        return 1
    live = _alive(state)
    print(f"status={state.get('status')} live={'yes' if live else 'no'} "
          f"sandbox_id={state.get('sandbox_id', '-')} pid={state.get('pid', '-')} "
          f"since={state.get('ready_at') or state.get('started_at', '-')} "
          f"box=[{_box_str(state.get('resources') or {})}]")
    return 0 if live else 1


def cmd_up(pkg: Path, at_max: bool = False) -> int:
    state = _read_state(pkg)
    if _alive(state):
        print(f"sandbox already up (id={state.get('sandbox_id')}, "
              f"box=[{_box_str(state.get('resources') or {})}]); "
              f"use ./sandbox reset for a fresh one")
        return 0
    box = _box(pkg, at_max)
    log = pkg / "run" / "sandbox.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    sock = tempfile.mktemp(prefix="evolve-sandbox-", suffix=".sock", dir="/tmp")
    _write_state(pkg, {"status": "booting", "sock": sock, "started_at": _now(),
                       "resources": box, "at_max": at_max})
    with log.open("a") as lf:
        lf.write(f"[{_now()}] up: booting a sandbox for {pkg} "
                 f"box=[{_box_str(box)}] ({box['source']})\n")
        lf.flush()
        # --pkg is a top-level option and has to precede the subcommand.
        argv = [sys.executable, str(Path(__file__).resolve()), "--pkg", str(pkg),
                "serve", "--sock", sock]
        for key, flag in (("cpu", "--cpu"), ("mem_gb", "--mem-gb"),
                          ("disk_gb", "--disk-gb")):
            if box.get(key) is not None:
                argv += [flag, str(box[key])]
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
            start_new_session=True, cwd=str(pkg))
    started = time.time()
    last_note = 0.0
    while True:
        state = _read_state(pkg)
        if state.get("status") == "ready":
            print(f"sandbox up: id={state.get('sandbox_id')} workdir={state.get('workdir')} "
                  f"box=[{_box_str(box)}] in {time.time() - started:.0f}s")
            return 0
        if state.get("status") == "failed" or proc.poll() is not None:
            print("sandbox failed to boot; run/sandbox.log ends with:")
            print(log.read_text(errors="replace")[-3000:])
            return 1
        if time.time() - started > BOOT_TIMEOUT:
            proc.kill()
            print(f"sandbox did not come up within {BOOT_TIMEOUT}s; "
                  f"run/sandbox.log ends with:")
            print(log.read_text(errors="replace")[-3000:])
            return 1
        if time.time() - last_note > 30:
            last_note = time.time()
            print(f"  building/booting... {time.time() - started:.0f}s", flush=True)
        time.sleep(2)


def cmd_down(pkg: Path) -> int:
    state = _read_state(pkg)
    if not state or state.get("status") == "down":
        print("no sandbox is up")
        return 0
    r = _request(state, "down", wait=120)
    pid = state.get("pid")
    deadline = time.time() + 180
    while pid and time.time() < deadline:
        try:
            os.kill(int(pid), 0)
        except OSError:
            break
        time.sleep(1)
    else:
        if pid:
            try:
                os.killpg(int(pid), signal.SIGKILL)
            except OSError:
                pass
    if state.get("sock"):
        Path(state["sock"]).unlink(missing_ok=True)
    _write_state(pkg, {**state, "status": "down", "down_at": _now()})
    print("sandbox down" + ("" if r.get("ok") else f" (server said: {r.get('error')})"))
    return 0


def cmd_exec(pkg: Path, cmd: str, timeout: int) -> int:
    state = _read_state(pkg)
    r = _request(state, "exec", wait=timeout, cmd=cmd, timeout=timeout)
    if not r.get("ok"):
        print(f"sandbox error: {r.get('error')}", file=sys.stderr)
        return 2
    sys.stdout.write(r.get("stdout", ""))
    if r.get("stderr"):
        sys.stderr.write(r["stderr"])
    sys.stdout.flush()
    sys.stderr.flush()
    code = int(r.get("code", 1))
    if code:
        print(f"[exit {code}]", file=sys.stderr)
    return code


def _measured_str(m: dict | None) -> str:
    m = m or {}
    return (f"mem_peak_mb={m.get('mem_peak_mb')} cpu_seconds={m.get('cpu_seconds')} "
            f"df_used_mb={m.get('df_used_mb')} solve_secs={m.get('solve_secs')}"
            + (f" oom_kill={m['oom_kill']}" if m.get("oom_kill", 0) > 0 else "")
            + (" disk_exhausted" if m.get("disk_exhausted") else ""))


def _print_oracle(r: dict) -> None:
    print(f"oracle: reward={r.get('reward')} solve_exit={r.get('solve_exit')}  "
          f"measured: {_measured_str(r.get('measured'))}")
    tail = r.get("tail") or ""
    if tail.strip():
        print("--- what the run printed (tail) ---")
        print(tail)


def cmd_oracle(pkg: Path, solve_timeout: int) -> int:
    state = _read_state(pkg)
    r = _request(state, "oracle", wait=solve_timeout + 600, solve_timeout=solve_timeout)
    if not r.get("ok"):
        print(f"sandbox error: {r.get('error')}", file=sys.stderr)
        return 2
    _print_oracle(r)
    return 0 if float(r.get("reward") or 0) >= 1.0 else 1


def cmd_grade(pkg: Path) -> int:
    state = _read_state(pkg)
    r = _request(state, "grade", wait=900)
    if not r.get("ok"):
        print(f"sandbox error: {r.get('error')}", file=sys.stderr)
        return 2
    print(f"grade: reward={r.get('reward')}")
    return 0 if float(r.get("reward") or 0) >= 1.0 else 1


def _starved(r: dict, solve_timeout: int) -> str:
    """Why a failed oracle run read the box rather than the task, or "".

    Mirrors daytona_revalidate.starved, which the server cannot be asked for
    here because the client side imports nothing but the stdlib.
    """
    m = r.get("measured") or {}
    if (m.get("oom_kill") or 0) > 0:
        return "memory"
    if m.get("disk_exhausted"):
        return "disk"
    if r.get("solve_exit") == 124 or (m.get("solve_secs") or 0) >= solve_timeout:
        return "time"
    return ""


def _verifier_rel(pkg: Path) -> str:
    return "tests/test_state.py" if (pkg / "tests" / "test_state.py").exists() else "tests/test.sh"


def _names_audit(pkg: Path) -> list[str]:
    """Names the verifier depends on that nothing the agent can read states,
    beyond what the seed's verifier already did (run/seed_literals.json, written
    by the harness at layout; absent for a package driven by hand)."""
    try:
        baseline = json.loads((pkg / "run" / "seed_literals.json").read_text())
    except (OSError, ValueError):
        baseline = []
    return vl.audit_package(pkg, _verifier_rel(pkg), baseline)


def _step_audit(pkg: Path) -> list[str]:
    """How far the rewrite sits above the seed, against the one-rung rule
    (run/seed_size.json, written by the harness at layout; absent for a
    package driven by hand, and then nothing is checked)."""
    try:
        seed = json.loads((pkg / "run" / "seed_size.json").read_text())
    except (OSError, ValueError):
        return []
    return ts.violations(seed, ts.size_of_package(pkg, _verifier_rel(pkg)))


def cmd_check(pkg: Path, solve_timeout: int, at_max: bool = False) -> int:
    """The whole verdict on a fresh container: rebuild, null probe, oracle.

    The oracle run is measured, and that measurement decides which box the
    caller's own probe runs in, so the box has to be one the reading describes
    the task in: the training size by default, the ceiling on --max. A run the
    box cut short is not a verdict on the task, it is a verdict on the box,
    and the output says so; whether to measure at the ceiling or make the
    solution need less is the agent's call, not this tool's.

    The container's counters are a high-water mark since boot, so the reading
    covers the entrypoint and the null-probe grade as well as the solution.
    That is a superset of what the seed campaign read (solution only) and it
    errs on the side the box has to hold anyway: the training sandbox runs the
    verifier too.
    """
    started = time.time()
    if _read_state(pkg).get("status") not in (None, "down"):
        cmd_down(pkg)
    if cmd_up(pkg, at_max=at_max) != 0:
        _append_check(pkg, {"time": _now(), "verdict": "fail", "stage": "boot",
                            "at_max": at_max})
        print("VERDICT: fail   stage=boot (the environment did not come up)")
        return 1
    state = _read_state(pkg)
    box = state.get("resources") or {}
    null = _request(state, "grade", wait=900)
    if not null.get("ok"):
        print(f"VERDICT: error   stage=null_probe ({null.get('error')})")
        return 2
    null_reward = float(null.get("reward") or 0)
    if null_reward >= 1.0:
        _append_check(pkg, {"time": _now(), "verdict": "fail", "stage": "null_probe",
                            "null_reward": null_reward, "resources": box,
                            "at_max": at_max})
        print(f"VERDICT: fail   stage=null_probe   null_reward={null_reward}")
        print("The verifier passes on the untouched workspace: it pays for nothing.\n"
              "Make it depend on work the solution has to do, then check again.")
        return 1
    r = _request(state, "oracle", wait=solve_timeout + 600, solve_timeout=solve_timeout)
    if not r.get("ok"):
        print(f"VERDICT: error   stage=oracle ({r.get('error')})")
        return 2
    reward = float(r.get("reward") or 0)
    oracle_ok = reward >= 1.0
    starved = "" if oracle_ok else _starved(r, solve_timeout)
    # The reference solution passing says the verifier and the solution agree.
    # It says nothing about whether an agent that reads only the instruction
    # could have; that is what the names audit asks, and it is part of the
    # verdict because the caller enforces the same rule.
    names = _names_audit(pkg)
    step = _step_audit(pkg)
    # The names audit is advice: it is printed and recorded, and the verdict
    # does not turn on it. Its precision was never measured before it gated,
    # and when it was (wd-20260904a, 464 rewrites) everything it flagged was a
    # false positive. The size rule is the gate; it was measured first.
    ok = oracle_ok and not step
    _append_check(pkg, {"time": _now(), "verdict": "pass" if ok else "fail",
                        "stage": "step_size" if oracle_ok and step else "oracle",
                        "reward": reward,
                        "solve_exit": r.get("solve_exit"), "null_reward": null_reward,
                        "resources": box, "at_max": at_max,
                        "measured": r.get("measured"), "starved": starved,
                        "dark_literals": names, "step_size": step,
                        "elapsed_s": round(time.time() - started)})
    if oracle_ok and step:
        print(f"VERDICT: fail   stage=step_size   reward={reward}   "
              f"took={time.time() - started:.0f}s   box=[{_box_str(box)}]")
        print(f"measured: {_measured_str(r.get('measured'))}")
        print("The reference solution passes, but " + ts.why(step))
        if names:
            print("Also worth a look, not a failure: " + vl.why(names))
        return 1
    print(f"VERDICT: {'pass' if ok else 'fail'}   reward={reward}   "
          f"solve_exit={r.get('solve_exit')}   null_reward={null_reward}   "
          f"took={time.time() - started:.0f}s   box=[{_box_str(box)}]")
    print(f"measured: {_measured_str(r.get('measured'))}")
    if names:
        print("Also, once the solution passes: " + vl.why(names))
    if step:
        print("Also, once the solution passes: " + ts.why(step))
    if ok and at_max:
        print("Passed at the ceiling: the task will be provisioned from what the "
              "reference solution measured (never below the seed's size). If that "
              "reading is close to the ceiling the task is unrunnable, not hard: "
              "shrink it.")
    if not ok:
        tail = r.get("tail") or ""
        print("--- what the run printed (tail) ---")
        print(tail if tail.strip() else "(empty)")
        if starved and at_max:
            print(f"\nOut of {starved} at the platform ceiling: the task needs more "
                  f"than any sandbox can have. Shrink what the solution has to do, "
                  f"then check again.")
        elif starved:
            print(f"\nThe reference solution ran out of {starved} in this box, which is "
                  f"the size training gives the task. Make the solution need less. "
                  f"Only if the harder task genuinely needs more than the seed had, "
                  f"./sandbox check --max [{_box_str(CEILING)}] measures it at the "
                  f"platform ceiling; that should be rare, and a reading close to the "
                  f"ceiling means unrunnable, not hard.")
        else:
            print("\nFix the task so the reference solution scores 1.0, then check again. "
                  "The container is still up: ./sandbox exec to look around.")
    return 0 if ok else 1  # nonzero on fail so `./sandbox check` reflects it


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

async def _serve(pkg: Path, sock: str, resources: dict | None = None) -> int:
    import asyncio

    import daytona_revalidate as dr
    pack = dr.pack

    def log(msg: str) -> None:
        print(f"[{_now()}] {msg}", file=sys.stderr, flush=True)

    state = {**_read_state(pkg), "status": "booting", "sock": sock,
             "pid": os.getpid(), "started_at": _now()}
    _write_state(pkg, state)
    # Read once at boot: the harness wrote it before the session, and the
    # agent editing its copy would only mislead its own check, never the
    # loop's probe, which reads the rewrite's own file.
    pretest = _pretest_of(pkg)
    try:
        row = pack.to_row(str(pkg), pretest=pretest)
    except Exception as e:  # noqa: BLE001 -- the package, not the platform
        log(f"package error: {type(e).__name__}: {e}")
        _write_state(pkg, {**state, "status": "failed",
                           "error": f"package_error: {type(e).__name__}: {e}"[:500]})
        return 2
    md = row["metadata"]
    workdir = md.get("workdir") or "/workspace"
    stop = asyncio.Event()
    lock = asyncio.Lock()

    box = {"cpu": None, "mem_gb": None, "disk_gb": md.get("daytona_disk_gb"),
           **{k: v for k, v in (resources or {}).items() if v is not None}}
    log(f"boot sandbox for {md['instance_id']} box=[{_box_str(box)}] "
        f"(harness: {dr._harness_provenance()})")
    n_paths = len(dr.protected_paths_of(md["tmax"]))
    n_cmds = len(dr.protected_cmds_of(md["tmax"]))
    if n_paths or n_cmds:
        # A row with protected entries grades by the integrity baseline and
        # never consults the pin hook.
        log(f"integrity baseline: {n_paths} paths, {n_cmds} cmds")
    elif pretest:
        tm = md["tmax"]
        stamped, episode = tm.get("pretest_env_identity"), tm.get("pretest_episode_env_identity")
        log(f"pin hook: stamped={stamped or '?'} episode={episode or '?'} -> "
            f"{'runs before the verifier' if stamped and stamped == episode else 'skipped: environment moved'}")
    try:
        async with dr.boot_agent_sandbox(
            md.get("image") or "",
            dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False,
            cpu=box["cpu"], memory=box["mem_gb"], disk_gb=box["disk_gb"],
        ) as sandbox:
            sb = dr._Root(sandbox)
            if md.get("entrypoint"):
                await dr._start_entrypoint(sb, md["entrypoint"], workdir=workdir)
            await dr.seed_workspace(sb, md["tmax"])
            # INTEGRITY BASELINE for `grade`: the state as booted, before any
            # request is served -- what a training episode's agent starts
            # from. None for a package without protected entries. The lists
            # are the boot-time ones; `grade` refuses if they change.
            boot_entries = dr.protected_entries_of(md["tmax"])
            boot_baseline = await dr.capture_baseline(
                sb, md["tmax"], workdir=workdir, timeout=EXEC_TIMEOUT)

            async def oracle(solve_timeout: int) -> dict:
                # Re-read the package: the agent edits solution/ and tests/
                # between calls and expects the current files to be judged.
                tmax = pack.to_row(str(pkg), pretest=pretest)["metadata"]["tmax"]
                sol_dir = pkg / "solution"
                if not (sol_dir / "solve.sh").exists():
                    return {"ok": False, "error": "package ships no solution/solve.sh"}
                for f in sorted(sol_dir.rglob("*")):
                    if f.is_file():
                        await sb.write_file(f"/solution/{f.relative_to(sol_dir)}",
                                            f.read_text(errors="replace"))
                # INTEGRITY BASELINE for the oracle: the state the reference
                # solution starts from, taken after solution/ is in and before
                # it runs, with the lists as the package declares them now.
                baseline = await dr.capture_baseline(
                    sb, tmax, workdir=workdir, timeout=EXEC_TIMEOUT)
                t0 = time.time()
                code, out, err = await sb.exec("bash /solution/solve.sh", check=False,
                                               timeout=solve_timeout)
                # Before grading, which starts processes of its own.
                measured = await dr.measure(sb, time.time() - t0,
                                            tail=(out or "") + (err or ""))
                reward = await dr.grade_tmax(sb, tmax, workdir=workdir,
                                             baseline_digests=baseline)
                return {"ok": True, "solve_exit": code, "reward": reward,
                        "measured": measured, "resources": box,
                        "tail": (out + "\n" + err)[-4000:]}

            async def handle(req: dict) -> dict:
                op = req.get("op")
                if op == "ping":
                    return {"ok": True, "sandbox_id": sandbox.sandbox_id}
                if op == "exec":
                    code, out, err = await sb.exec(
                        str(req.get("cmd", "")), check=False,
                        timeout=int(req.get("timeout") or EXEC_TIMEOUT))
                    return {"ok": True, "code": code, "stdout": out, "stderr": err}
                if op == "oracle":
                    return await oracle(int(req.get("solve_timeout") or SOLVE_TIMEOUT))
                if op == "grade":
                    tmax = pack.to_row(str(pkg), pretest=pretest)["metadata"]["tmax"]
                    if dr.protected_entries_of(tmax) != boot_entries:
                        return {"ok": False,
                                "error": "the protected lists changed since up; "
                                         "run ./sandbox reset to take a fresh baseline"}
                    reward = await dr.grade_tmax(sb, tmax, workdir=workdir,
                                                 baseline_digests=boot_baseline)
                    return {"ok": True, "reward": reward}
                if op == "down":
                    stop.set()
                    return {"ok": True}
                return {"ok": False, "error": f"unknown op {op!r}"}

            async def on_connect(reader, writer):
                line = await reader.readline()
                try:
                    req = json.loads(line.decode() or "{}")
                except ValueError:
                    req = {}
                log(f"request: {req.get('op')} "
                    f"{str(req.get('cmd', ''))[:120]!r}".rstrip())
                t0 = time.time()
                async with lock:
                    try:
                        resp = await handle(req)
                    except Exception as e:  # noqa: BLE001 -- report, keep serving
                        resp = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
                log(f"  -> {'ok' if resp.get('ok') else 'error'} "
                    f"{'code=' + str(resp['code']) if 'code' in resp else ''}"
                    f"{' reward=' + str(resp['reward']) if 'reward' in resp else ''} "
                    f"({time.time() - t0:.1f}s)")
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
                writer.close()

            server = await asyncio.start_unix_server(on_connect, path=sock)
            _write_state(pkg, {**state, "status": "ready", "ready_at": _now(),
                               "sandbox_id": sandbox.sandbox_id, "workdir": workdir,
                               "resources": {**(state.get("resources") or {}), **box}})
            log(f"ready: sandbox {sandbox.sandbox_id}, serving on {sock}")
            await stop.wait()
            server.close()
            await server.wait_closed()
            log("down requested; deleting the sandbox")
    except Exception as e:  # noqa: BLE001 -- the boot itself failed
        log(f"boot failed: {type(e).__name__}: {e}")
        _write_state(pkg, {**_read_state(pkg), "status": "failed",
                           "error": f"{type(e).__name__}: {e}"[:500]})
        return 1
    _write_state(pkg, {**_read_state(pkg), "status": "down", "down_at": _now()})
    log("sandbox deleted")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pkg", default=".", help="the task package directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    max_help = ("open the container at the platform ceiling (%(cpu)d vCPU / "
                "%(mem_gb)d GiB / %(disk_gb)d GiB) instead of the training size"
                % CEILING)
    p = sub.add_parser("up")
    p.add_argument("--max", action="store_true", help=max_help)
    sub.add_parser("down")
    p = sub.add_parser("reset")
    p.add_argument("--max", action="store_true", help=max_help)
    sub.add_parser("status")
    sub.add_parser("grade")
    p = sub.add_parser("exec")
    p.add_argument("command")
    p.add_argument("--timeout", type=int, default=EXEC_TIMEOUT)
    p = sub.add_parser("oracle")
    p.add_argument("--solve-timeout", type=int, default=SOLVE_TIMEOUT)
    p = sub.add_parser("check")
    p.add_argument("--solve-timeout", type=int, default=SOLVE_TIMEOUT)
    p.add_argument("--max", action="store_true", help=max_help)
    p = sub.add_parser("serve")
    p.add_argument("--sock", required=True)
    p.add_argument("--cpu", type=int)
    p.add_argument("--mem-gb", type=int)
    p.add_argument("--disk-gb", type=int)
    args = ap.parse_args()
    pkg = Path(args.pkg).resolve()

    if args.cmd == "serve":
        import asyncio
        sys.exit(asyncio.run(_serve(pkg, args.sock, {
            "cpu": args.cpu, "mem_gb": args.mem_gb, "disk_gb": args.disk_gb})))
    if args.cmd == "up":
        sys.exit(cmd_up(pkg, at_max=args.max))
    if args.cmd == "down":
        sys.exit(cmd_down(pkg))
    if args.cmd == "reset":
        cmd_down(pkg)
        sys.exit(cmd_up(pkg, at_max=args.max))
    if args.cmd == "status":
        sys.exit(cmd_status(pkg))
    if args.cmd == "exec":
        sys.exit(cmd_exec(pkg, args.command, args.timeout))
    if args.cmd == "oracle":
        sys.exit(cmd_oracle(pkg, args.solve_timeout))
    if args.cmd == "grade":
        sys.exit(cmd_grade(pkg))
    if args.cmd == "check":
        sys.exit(cmd_check(pkg, args.solve_timeout, at_max=args.max))


if __name__ == "__main__":
    main()
