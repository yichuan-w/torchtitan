#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The container a task-evolution agent works in, one per session.

The agent's only check used to be `./validate`: a full image build, the
reference solution, the verifier -- minutes per call and nothing in between.
No way to run one command in the environment, read a log, or see which check
failed with the state still there; sessions spent twenty minutes polling two
of those and timed out. This hands the agent the container itself.

`./sandbox` in the package directory is a shell wrapper over this file:

    ./sandbox up            build the image, boot a container, seed the
                            workspace, start the entrypoint. The box is the
                            size training gives this task, from
                            run/resources.json; --max opens it at the
                            platform ceiling instead. The harness runs
                            `up --detach` as each session starts, and exec,
                            grade and oracle wait for a boot in progress or
                            start one, so an agent never has to run it
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
                            oracle, which must pass; then an advisory names
                            audit for possible unstated requirements.
                            Prints VERDICT: pass|fail.
                            The oracle run is measured (memory peak, cpu
                            seconds, disk). A run the box cut short -- OOM
                            kill, disk full, out of time on its cores -- is
                            reported as such; --max runs the same check at
                            the platform ceiling, where nothing truncates
                            the reading.
    ./sandbox probes        replay run/verifier-probes (correct.sh and every
                            wrong-N.sh its contract.json declares) the way the
                            caller will: each in its own fresh container, all
                            at once, exec then grade. Prints one line per
                            script; the agent's own container is left as is
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
oracle and check reuse an earlier passing result when everything they read
(_inputs_digest: environment/, solution/, tests/, task.toml, the pin hook, the
harness code) is byte-identical and the box and timeout match, print that they
did, and record it; --fresh forces a rerun. A non-passing run with the same
inputs drops the earlier pass. The entries live in run/result-cache.json.
Every check's verdict is appended to run/checks.jsonl, which the caller reads
to learn whether the agent ever saw its own rewrite pass, and what the
reference solution cost when it did: that measurement, not a number anyone
wrote down, is what sizes the rewritten task's sandbox.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import task_size as ts  # noqa: E402 -- stdlib-only sibling
import verifier_literals as vl  # noqa: E402 -- stdlib-only sibling

from derive_sizing import CEILING  # noqa: E402 -- stdlib-only sibling

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
        return {
            "cpu": None,
            "mem_gb": None,
            "disk_gb": None,
            "source": "harness_default (no run/resources.json)",
        }
    return {
        "cpu": got.get("cpu"),
        "mem_gb": got.get("mem_gb"),
        "disk_gb": got.get("disk_gb"),
        "source": got.get("source") or "run/resources.json",
    }


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
    return (
        f"cpu={box.get('cpu')} mem_gb={box.get('mem_gb')} "
        f"disk_gb={box.get('disk_gb')}"
    )


def _append_check(pkg: Path, record: dict) -> None:
    path = pkg / "run" / "checks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# result reuse
# --------------------------------------------------------------------------

# Passing oracle/check outcomes, keyed by a digest of everything the run reads.
# The harness deletes it wherever an independent revalidation failed.
REUSE_FILE = "result-cache.json"
# Package entries no oracle or grade reads: the harness's own files, and the
# instruction (it reaches the row only as the agent's prompt).
_UNREAD = ("AGENTS.md", "sandbox", "run", "traces", "instruction.md")
# run/ files the server reads at boot or per request; the box is compared apart.
_RUN_INPUTS = ("pretest.json",)
_HARNESS_FILES = (
    "agent_sandbox.py",
    "daytona_revalidate.py",
    "terminus_validation.py",
    "pack_to_dataset.py",
    "recorded_solution.py",
    "../grading.py",
    "../integrity_baseline.py",
    "../prepare_rts_data.py",
    "../prepare_tmax_data.py",
)


def _reuse_enabled() -> bool:
    return os.environ.get("EVOLVE_SANDBOX_REUSE", "1") != "0"


def _hash_tree(h, root: Path, rel: str, seen: set[str]) -> None:
    """Feed every entry under ``root`` into ``h``: path, mode, symlink target,
    and content. A symlinked directory is walked through once."""
    real = os.path.realpath(root)
    if real in seen:
        h.update(f"loop {rel}\0".encode())
        return
    seen.add(real)
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        crel = f"{rel}/{child.name}" if rel else child.name
        if (not rel and child.name in _UNREAD) or child.name == "__pycache__":
            continue
        if child.is_symlink():
            h.update(f"link {crel} {os.readlink(child)}\0".encode())
        try:
            st = child.stat()
        except OSError:
            h.update(f"dangling {crel}\0".encode())
            continue
        if child.is_dir():
            h.update(f"dir {crel}\0".encode())
            _hash_tree(h, child, crel, seen)
        elif not stat.S_ISREG(st.st_mode):
            # A FIFO or device would block or never end; its type is enough.
            h.update(f"special {crel} {st.st_mode:o}\0".encode())
        else:
            h.update(f"file {crel} {st.st_mode & 0o777:o} {st.st_size}\0".encode())
            with child.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)


def _inputs_digest(pkg: Path, solve_timeout: int) -> str | None:
    """What decides an oracle or check outcome, as one hash: every package
    file except _UNREAD (environment/ with its Dockerfile and context,
    solution/, tests/, task.toml, anything else the agent added), the pin hook
    and protected lists under run/, the solve timeout, and the harness code
    that builds, runs and grades."""
    import hashlib

    h = hashlib.sha256()
    h.update(f"solve_timeout {solve_timeout}\0".encode())
    here = Path(__file__).resolve().parent
    for name in _HARNESS_FILES:
        p = here / name
        h.update(f"harness {name}\0".encode())
        if p.is_file():
            h.update(hashlib.sha256(p.read_bytes()).digest())
    for name in _RUN_INPUTS:
        p = pkg / "run" / name
        h.update(f"run {name}\0".encode())
        if p.is_file():
            h.update(p.read_bytes())
    try:
        if pkg.is_dir():
            _hash_tree(h, pkg, "", set())
    except OSError:
        # Unreadable or changing under us: no reuse, and nothing remembered.
        return None
    return h.hexdigest()


def _box_key(box: dict | None) -> list:
    return [(box or {}).get(k) for k in ("cpu", "mem_gb", "disk_gb")]


def _read_reuse(pkg: Path) -> list[dict]:
    try:
        got = json.loads((pkg / "run" / REUSE_FILE).read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(got, list):
        return []
    return [
        e
        for e in got
        if isinstance(e, dict) and isinstance(e.get("oracle"), dict) and "time" in e
    ]


def _write_reuse(pkg: Path, entries: list[dict]) -> None:
    """Best effort: the cache only saves time, so a failed write (a concurrent
    sandbox command in the same package) must not turn a verdict into a crash."""
    path = pkg / "run" / REUSE_FILE
    incoming = path.with_suffix(f".json.{os.getpid()}.incoming")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_text(json.dumps(entries[-20:], sort_keys=True) + "\n")
        os.replace(incoming, path)
    except OSError:
        incoming.unlink(missing_ok=True)


def _remember(pkg: Path, entry: dict) -> None:
    _write_reuse(pkg, [*_read_reuse(pkg), {"time": _now(), **entry}])


def _forget(pkg: Path, digest: str | None) -> None:
    """A run with these inputs did not pass: no earlier pass with the same
    inputs may stand in for a rerun."""
    if digest is None:
        return
    entries = _read_reuse(pkg)
    kept = [e for e in entries if e.get("digest") != digest]
    if len(kept) != len(entries):
        _write_reuse(pkg, kept)


def _reusable_check(pkg: Path, digest: str, box: dict, at_max: bool) -> dict | None:
    """The newest passing check with these inputs, at this box."""
    for e in reversed(_read_reuse(pkg)):
        if (
            e.get("kind") == "check"
            and e.get("digest") == digest
            and bool(e.get("at_max")) == at_max
            and e.get("requested_box") == _box_key(box)
        ):
            return e
    return None


def _reusable_oracle(pkg: Path, digest: str, state: dict) -> dict | None:
    """The newest passing oracle run with these inputs in a container of the
    current one's size: a check's (a fresh container), or an earlier oracle in
    this very container."""
    box = _box_key(state.get("resources"))
    sandbox_id = state.get("sandbox_id") if state.get("status") == "ready" else None
    for e in reversed(_read_reuse(pkg)):
        if e.get("digest") != digest or _box_key(e.get("resources")) != box:
            continue
        if e.get("kind") == "check" or (
            e.get("kind") == "oracle" and sandbox_id and e.get("sandbox_id") == sandbox_id
        ):
            return e
    return None


def _oracle_summary(r: dict) -> dict:
    keys = (
        "reward",
        "solve_exit",
        "measured",
        "grading",
        "replay",
        "fast_replay_used",
        "fast_replay_fallback",
        "fast_replay_first",
    )
    return {k: r.get(k) for k in keys} | {"tail": (r.get("tail") or "")[-2000:]}


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
        return {
            "ok": False,
            "error": f"sandbox server not reachable ({e}); "
            "run ./sandbox status, then ./sandbox up",
        }
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
                return {
                    "ok": False,
                    "error": f"no answer from the sandbox server "
                    f"within {wait + 60}s",
                }
            if not b:
                break
            chunks.append(b)
    try:
        return json.loads(b"".join(chunks).decode())
    except ValueError:
        return {"ok": False, "error": "unreadable answer from the sandbox server"}


def _alive(state: dict) -> bool:
    return state.get("status") == "ready" and _request(state, "ping", wait=10).get(
        "ok", False
    )


def cmd_status(pkg: Path) -> int:
    state = _read_state(pkg)
    if not state:
        print("no sandbox: run ./sandbox up")
        return 1
    live = _alive(state)
    print(
        f"status={state.get('status')} live={'yes' if live else 'no'} "
        f"sandbox_id={state.get('sandbox_id', '-')} pid={state.get('pid', '-')} "
        f"since={state.get('ready_at') or state.get('started_at', '-')} "
        f"box=[{_box_str(state.get('resources') or {})}]"
    )
    return 0 if live else 1


def cmd_size(pkg: Path) -> int:
    seed = pkg / "run" / "seed_size.json"
    print(
        json.dumps({
            "current": ts.size_of_package(pkg, _verifier_rel(pkg)),
            "seed": json.loads(seed.read_text()) if seed.exists() else None,
            "violations": _step_audit(pkg),
        })
    )
    return 0


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return False
    return True


def _booting(state: dict) -> bool:
    """A server is still bringing this package's container up."""
    return state.get("status") == "booting" and _pid_alive(state.get("pid"))


def _start(pkg: Path, at_max: bool) -> subprocess.Popen:
    """Start the server that boots the container, and return without waiting.

    The server runs in its own session, so it outlives whatever started it;
    run/sandbox.json says how far it got.
    """
    box = _box(pkg, at_max)
    log = pkg / "run" / "sandbox.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    # The socket lives in /tmp by default because AF_UNIX paths are capped at
    # 108 bytes and a package path is longer. EVOLVE_SOCK_DIR moves it: on a
    # shared login node /tmp filled up with other users' files (2026-09-14)
    # and every boot failed ENOSPC at bind(); /dev/shm is node-local and short.
    sock = tempfile.mktemp(prefix="evolve-sandbox-", suffix=".sock",
                           dir=os.environ.get("EVOLVE_SOCK_DIR") or "/tmp")
    _write_state(
        pkg,
        {
            "status": "booting",
            "sock": sock,
            "started_at": _now(),
            "resources": box,
            "at_max": at_max,
        },
    )
    with log.open("a") as lf:
        lf.write(
            f"[{_now()}] up: booting a sandbox for {pkg} "
            f"box=[{_box_str(box)}] ({box['source']})\n"
        )
        lf.flush()
        # --pkg is a top-level option and has to precede the subcommand.
        argv = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--pkg",
            str(pkg),
            "serve",
            "--sock",
            sock,
        ]
        for key, flag in (
            ("cpu", "--cpu"),
            ("mem_gb", "--mem-gb"),
            ("disk_gb", "--disk-gb"),
        ):
            if box.get(key) is not None:
                argv += [flag, str(box[key])]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=lf,
            stderr=lf,
            start_new_session=True,
            cwd=str(pkg),
        )
    # The server writes its pid too, but only once it has started; recording
    # it here leaves no moment in which a second `up` cannot see the boot.
    _write_state(pkg, {**_read_state(pkg), "pid": proc.pid})
    return proc


def cmd_up(pkg: Path, at_max: bool = False, detach: bool = False) -> int:
    """Boot the container, or wait for the boot already in progress.

    The harness runs ``up --detach`` as a session starts, so the container
    comes up while the agent reads; the agent never has to run ``up`` itself.
    """
    state = _read_state(pkg)
    if _alive(state):
        print(
            f"sandbox already up (id={state.get('sandbox_id')}, "
            f"box=[{_box_str(state.get('resources') or {})}]); "
            f"use ./sandbox reset for a fresh one"
        )
        return 0
    if _booting(state):
        if detach:
            print(f"sandbox already booting (server pid {state['pid']})")
            return 0
        print(f"sandbox already booting (server pid {state['pid']}); waiting for it")
        return _wait_ready(pkg, int(state["pid"]), state.get("resources") or {})
    proc = _start(pkg, at_max)
    if detach:
        print(f"sandbox booting (server pid {proc.pid}); commands wait for it")
        return 0
    return _wait_ready(pkg, proc.pid, _read_state(pkg).get("resources") or {}, proc)


def _ready_state(pkg: Path) -> dict | None:
    """The state of a container that is up, booting one first if need be.

    exec, grade and oracle need a container, so they wait for the one that is
    booting, or boot one when there is none, rather than asking the agent to.
    Progress goes to stderr: exec's stdout is the command's own. None if the
    boot failed; what failed is already printed.
    """
    state = _read_state(pkg)
    if state.get("status") == "ready":
        return state
    if _booting(state):
        code = _wait_ready(pkg, int(state["pid"]), state.get("resources") or {},
                           out=sys.stderr)
    else:
        print("no sandbox up; booting one", file=sys.stderr, flush=True)
        proc = _start(pkg, bool(state.get("at_max")))
        code = _wait_ready(pkg, proc.pid, _read_state(pkg).get("resources") or {},
                           proc, out=sys.stderr)
    return _read_state(pkg) if code == 0 else None


def _wait_ready(
    pkg: Path,
    pid: int,
    box: dict,
    proc: subprocess.Popen | None = None,
    out=None,
) -> int:
    """Wait for the server `pid` to report ready. ``proc`` is given when this
    process started it, so an exited child is seen as such rather than as a
    zombie that still answers kill(pid, 0)."""
    out = out or sys.stdout
    log = pkg / "run" / "sandbox.log"
    started = time.time()
    last_note = 0.0
    while True:
        state = _read_state(pkg)
        if state.get("status") == "ready":
            print(
                f"sandbox up: id={state.get('sandbox_id')} workdir={state.get('workdir')} "
                f"box=[{_box_str(box)}] in {time.time() - started:.0f}s",
                file=out,
            )
            return 0
        gone = proc.poll() is not None if proc else not _pid_alive(pid)
        if state.get("status") == "failed" or gone:
            print("sandbox failed to boot; run/sandbox.log ends with:", file=out)
            print(log.read_text(errors="replace")[-3000:], file=out)
            return 1
        if time.time() - started > BOOT_TIMEOUT:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
            print(
                f"sandbox did not come up within {BOOT_TIMEOUT}s; "
                f"run/sandbox.log ends with:",
                file=out,
            )
            print(log.read_text(errors="replace")[-3000:], file=out)
            return 1
        if time.time() - last_note > 30:
            last_note = time.time()
            print(f"  building/booting... {time.time() - started:.0f}s", file=out,
                  flush=True)
        # A cached image boots in ~1 s; a 2 s poll added a second per boot,
        # and every probe replay boots one container per control.
        time.sleep(0.5)


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
        time.sleep(0.2)
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
    state = _ready_state(pkg)
    if state is None:
        return 2
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
    return (
        f"mem_peak_mb={m.get('mem_peak_mb')} cpu_seconds={m.get('cpu_seconds')} "
        f"df_used_mb={m.get('df_used_mb')} solve_secs={m.get('solve_secs')}"
        + (f" oom_kill={m['oom_kill']}" if m.get("oom_kill", 0) > 0 else "")
        + (" disk_exhausted" if m.get("disk_exhausted") else "")
    )


def _print_replay(r: dict) -> None:
    replay = r.get("replay")
    if replay:
        print(
            f"recorded solution replay: waited {replay['waited_sec']:.0f}s of "
            f"{replay['recorded_wait_sec']:.0f}s recorded between turns "
            f"({'fast' if r.get('fast_replay_used') else 'recorded timing'})"
        )
    if r.get("fast_replay_fallback"):
        first = r.get("fast_replay_first") or {}
        print(
            f"(the fast replay got reward={first.get('reward')} "
            f"solve_exit={first.get('solve_exit')}; this result is the "
            "recorded-timing rerun's)"
        )


def _print_oracle(r: dict) -> None:
    print(
        f"oracle: reward={r.get('reward')} solve_exit={r.get('solve_exit')}  "
        f"measured: {_measured_str(r.get('measured'))}"
    )
    _print_replay(r)
    tail = r.get("tail") or ""
    if tail.strip():
        print("--- what the run printed (tail) ---")
        print(tail)


def _passed(r: dict) -> bool:
    return float(r.get("reward") or 0) >= 1.0 and r.get("solve_exit") == 0


def _with_recorded_rerun(
    pkg: Path, r: dict, solve_timeout: int, at_max: bool, null_probe: bool
) -> dict:
    """Rerun a failed fast replay at the recorded timing; that run is the verdict.

    A recorded solution replays fast by default (terminus_validation): each
    turn starts once the terminal is idle. A fast replay that fails is run
    again on a fresh container -- after the same null probe, for check -- at
    the recorded timing, so fast replay can never reject a recording that
    passes as recorded.
    """
    fast = bool((r.get("replay") or {}).get("fast_used"))
    if not r.get("ok") or _passed(r) or not fast:
        return {**r, "fast_replay_used": fast, "fast_replay_fallback": False}
    print(
        "fast replay of the recorded solution did not pass; rerunning it on a "
        "fresh container at the recorded timing",
        flush=True,
    )
    first = {k: r.get(k) for k in ("reward", "solve_exit")}
    cmd_down(pkg)
    if cmd_up(pkg, at_max=at_max) != 0:
        return {"ok": False, "error": "no environment for the recorded-timing rerun"}
    state = _read_state(pkg)
    null_reward = None
    if null_probe:
        null = _request(state, "grade", wait=900)
        if not null.get("ok"):
            return {"ok": False, "error": f"null probe: {null.get('error')}"}
        null_reward = float(null.get("reward") or 0)
    r = _request(
        state,
        "oracle",
        wait=solve_timeout + 600,
        solve_timeout=solve_timeout,
        fast=False,
    )
    return {
        **r,
        "fast_replay_used": False,
        "fast_replay_fallback": True,
        "fast_replay_first": first,
        **({"rerun_null_reward": null_reward} if null_reward is not None else {}),
    }


def cmd_oracle(pkg: Path, solve_timeout: int, fresh: bool = False) -> int:
    digest = _inputs_digest(pkg, solve_timeout) if _reuse_enabled() else None
    hit = None if fresh or digest is None else _reusable_oracle(
        pkg, digest, _read_state(pkg)
    )
    if hit is not None:
        where = (
            "the passing ./sandbox check"
            if hit["kind"] == "check"
            else "the passing ./sandbox oracle in this container"
        )
        print(
            f"oracle: REUSED the result of {where} at {hit['time']}: solution/, "
            f"tests/, environment/ and task.toml are byte-identical to that run. "
            f"Nothing ran, and the container is as you left it, so a following "
            f"`grade` grades that state, not a fresh solve. "
            f"./sandbox oracle --fresh replays it."
        )
        _print_oracle(hit["oracle"])
        return 0
    state = _ready_state(pkg)
    if state is None:
        return 2
    r = _request(state, "oracle", wait=solve_timeout + 600, solve_timeout=solve_timeout)
    r = _with_recorded_rerun(
        pkg, r, solve_timeout, bool(state.get("at_max")), null_probe=False
    )
    if not r.get("ok"):
        print(f"sandbox error: {r.get('error')}", file=sys.stderr)
        return 2
    _print_oracle(r)
    if r.get("fast_replay_fallback"):
        state = _read_state(pkg)
    if digest is not None:
        if _passed(r):
            _remember(
                pkg,
                {
                    "kind": "oracle",
                    "digest": digest,
                    "sandbox_id": state.get("sandbox_id"),
                    "resources": state.get("resources"),
                    "oracle": _oracle_summary(r),
                },
            )
        else:
            _forget(pkg, digest)
    return 0 if _passed(r) else 1


def cmd_grade(pkg: Path) -> int:
    state = _ready_state(pkg)
    if state is None:
        return 2
    r = _request(state, "grade", wait=900)
    if not r.get("ok"):
        print(f"sandbox error: {r.get('error')}", file=sys.stderr)
        return 2
    (pkg / "run" / "last-grade.json").write_text(json.dumps(r) + "\n")
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
    return (
        "tests/test_state.py"
        if (pkg / "tests" / "test_state.py").exists()
        else "tests/test.sh"
    )


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
    return ts.violations(
        seed,
        ts.size_of_package(pkg, _verifier_rel(pkg)),
        require_growth=seed.get("require_growth", True),
    )


def cmd_check(
    pkg: Path, solve_timeout: int, at_max: bool = False, fresh: bool = False
) -> int:
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

    A check whose inputs (_inputs_digest), box and timeout are byte-identical
    to an earlier passing one reuses that run's null probe and oracle and says
    so; ``fresh`` reruns it. The size and names audits are always recomputed.
    """
    started = time.time()
    digest = _inputs_digest(pkg, solve_timeout) if _reuse_enabled() else None
    requested = _box(pkg, at_max)
    hit = None if fresh or digest is None else _reusable_check(
        pkg, digest, requested, at_max
    )
    if hit is not None:
        # The same bytes, box and timeout already passed reset + null probe +
        # oracle in a fresh container; the audits below are recomputed.
        print(
            f"check: REUSED the passing check from {hit['time']}: solution/, "
            f"tests/, environment/ and task.toml are byte-identical to that run, "
            f"at the same box and --solve-timeout. Nothing was rebuilt or "
            f"replayed and the container was left as it is. "
            f"./sandbox check --fresh reruns it."
        )
        box, r = hit.get("resources") or {}, hit["oracle"]
        null_reward = float(hit.get("null_reward") or 0)
        return _check_verdict(
            pkg, started, solve_timeout, at_max, box, null_reward, r,
            reused_from=hit["time"],
        )
    print("check: rebuilding the container, then grading the untouched "
          "workspace and running the reference solution; the verdict line "
          "comes last")
    if _read_state(pkg).get("status") not in (None, "down"):
        cmd_down(pkg)
    if cmd_up(pkg, at_max=at_max) != 0:
        _append_check(
            pkg, {"time": _now(), "verdict": "fail", "stage": "boot", "at_max": at_max}
        )
        print("VERDICT: fail   stage=boot (the environment did not come up)")
        return 1
    state = _read_state(pkg)
    box = state.get("resources") or {}
    print("check: grading the untouched workspace (null probe)")
    null = _request(state, "grade", wait=900)
    if not null.get("ok"):
        print(f"VERDICT: error   stage=null_probe ({null.get('error')})")
        return 2
    null_reward = float(null.get("reward") or 0)
    if null_reward >= 1.0:
        return _null_probe_fail(pkg, digest, null_reward, box, at_max)
    print(f"check: running the reference solution (up to {solve_timeout} s)")
    r = _request(state, "oracle", wait=solve_timeout + 600, solve_timeout=solve_timeout)
    r = _with_recorded_rerun(pkg, r, solve_timeout, at_max, null_probe=True)
    if not r.get("ok"):
        print(f"VERDICT: error   stage=oracle ({r.get('error')})")
        return 2
    if r.get("fast_replay_fallback"):
        # The verdict is the rerun's, on its own container and null probe.
        state = _read_state(pkg)
        box = state.get("resources") or {}
        null_reward = float(r.get("rerun_null_reward") or 0)
        if null_reward >= 1.0:
            return _null_probe_fail(pkg, digest, null_reward, box, at_max)
    if digest is not None:
        if _passed(r):
            _remember(
                pkg,
                {
                    "kind": "check",
                    "digest": digest,
                    "at_max": at_max,
                    "requested_box": _box_key(requested),
                    "sandbox_id": state.get("sandbox_id"),
                    "resources": box,
                    "null_reward": null_reward,
                    "oracle": _oracle_summary(r),
                },
            )
        else:
            _forget(pkg, digest)
    return _check_verdict(pkg, started, solve_timeout, at_max, box, null_reward, r)


def _null_probe_fail(
    pkg: Path, digest: str | None, null_reward: float, box: dict, at_max: bool
) -> int:
    _append_check(
        pkg,
        {
            "time": _now(),
            "verdict": "fail",
            "stage": "null_probe",
            "null_reward": null_reward,
            "resources": box,
            "at_max": at_max,
        },
    )
    _forget(pkg, digest)
    print(f"VERDICT: fail   stage=null_probe   null_reward={null_reward}")
    print(
        "The verifier passes on the untouched workspace: it pays for nothing.\n"
        "Make it depend on work the solution has to do, then check again."
    )
    return 1


def _check_verdict(
    pkg: Path,
    started: float,
    solve_timeout: int,
    at_max: bool,
    box: dict,
    null_reward: float,
    r: dict,
    *,
    reused_from: str | None = None,
) -> int:
    """Record and print a check's verdict from its null probe and oracle run."""
    reward = float(r.get("reward") or 0)
    oracle_ok = _passed(r)
    starved = "" if oracle_ok else _starved(r, solve_timeout)
    # The reference solution passing says the verifier and the solution agree.
    # It says nothing about whether an agent that reads only the instruction
    # could have; the names audit suggests possible mismatches for review.
    names = _names_audit(pkg)
    step = _step_audit(pkg)
    # The names audit is advice: it is printed and recorded, and the verdict
    # does not turn on it. Its precision was never measured before it gated,
    # and when it was (wd-20260904a, 464 rewrites) everything it flagged was a
    # false positive. The size rule is the gate; it was measured first.
    ok = oracle_ok and not step
    _append_check(
        pkg,
        {
            "time": _now(),
            "verdict": "pass" if ok else "fail",
            "stage": "step_size" if oracle_ok and step else "oracle",
            "reward": reward,
            "solve_exit": r.get("solve_exit"),
            "grading": r.get("grading"),
            "null_reward": null_reward,
            "resources": box,
            "at_max": at_max,
            "measured": r.get("measured"),
            "starved": starved,
            "dark_literals": names,
            "step_size": step,
            "elapsed_s": round(time.time() - started),
            **({"reused_from": reused_from} if reused_from else {}),
            "fast_replay_used": r.get("fast_replay_used"),
            "fast_replay_fallback": r.get("fast_replay_fallback"),
        },
    )
    if oracle_ok and step:
        print(
            f"VERDICT: fail   stage=step_size   reward={reward}   "
            f"took={time.time() - started:.0f}s   box=[{_box_str(box)}]"
        )
        print(f"measured: {_measured_str(r.get('measured'))}")
        _print_replay(r)
        print("The reference solution passes, but " + ts.why(step))
        if names:
            print("Also worth a look, not a failure: " + vl.why(names))
        return 1
    print(
        f"VERDICT: {'pass' if ok else 'fail'}   reward={reward}   "
        f"solve_exit={r.get('solve_exit')}   null_reward={null_reward}   "
        f"took={time.time() - started:.0f}s   box=[{_box_str(box)}]"
    )
    print(f"measured: {_measured_str(r.get('measured'))}")
    _print_replay(r)
    if names:
        print("Also, once the solution passes: " + vl.why(names))
    if step:
        print("Also, once the solution passes: " + ts.why(step))
    if ok and at_max:
        print(
            "Passed at the ceiling: the task will be provisioned from what the "
            "reference solution measured (never below the seed's size). If that "
            "reading is close to the ceiling the task is unrunnable, not hard: "
            "shrink it."
        )
    if not ok:
        tail = r.get("tail") or ""
        print("--- what the run printed (tail) ---")
        print(tail if tail.strip() else "(empty)")
        grading = r.get("grading") or {}
        print(f"--- verifier output (exit {grading.get('exit_code')}) ---")
        print(grading.get("output_tail") or "(unavailable)")
        if starved and at_max:
            print(
                f"\nOut of {starved} at the platform ceiling: the task needs more "
                f"than any sandbox can have. Shrink what the solution has to do, "
                f"then check again."
            )
        elif starved:
            print(
                f"\nThe reference solution ran out of {starved} in this box, which is "
                f"the size training gives the task. Make the solution need less. "
                f"Only if the harder task genuinely needs more than the seed had, "
                f"./sandbox check --max [{_box_str(CEILING)}] measures it at the "
                f"platform ceiling; that should be rare, and a reading close to the "
                f"ceiling means unrunnable, not hard."
            )
        else:
            print(
                "\nFix the task so the reference solution scores 1.0, then check again. "
                "The container is still up: ./sandbox exec to look around."
            )
    return 0 if ok else 1  # nonzero on fail so `./sandbox check` reflects it


def cmd_probes(pkg: Path, timeout: int) -> int:
    """The caller's own replay of the saved controls, run by the agent.

    Checking N controls one after another in the agent's container costs N
    reset/exec/grade rounds in series; this is the same parallel replay the
    caller runs at the end, so what passes here is what the caller will see.
    """
    import verifier_probes as vp  # noqa: PLC0415 -- stdlib-only sibling

    log = pkg / "run" / "verifier-probe-results.jsonl"
    offset = log.stat().st_size if log.exists() else 0
    started = time.time()
    code = 0
    try:
        vp.verify_probes(pkg, dict(os.environ), timeout, down_own=False)
    except vp.SemanticProbeContract as e:
        print(f"probes: contract problem, nothing replayed: {e}")
        return 2
    except vp.SemanticProbeMisses as e:
        code = 1
        print(f"probes: FAIL   {e}")
    except Exception as e:  # noqa: BLE001 -- setup/transport errors abort the replay
        code = 2
        print(f"probes: error   {type(e).__name__}: {e}")
    cases: dict[str, dict] = {}
    if log.exists():
        with log.open() as f:
            f.seek(offset)
            for line in f:
                rec = json.loads(line)
                case = rec.get("case")
                if not case or case == "cleanup":
                    continue
                got = cases.setdefault(case, {})
                if rec.get("phase") == "setup" and rec.get("status") == "finished":
                    got["exec_exit"] = rec.get("returncode")
                elif rec.get("phase") == "grade_diagnostics":
                    got["reward"] = (rec.get("result") or {}).get("reward")
    for case in sorted(cases, key=lambda c: (c != "correct", c)):
        got = cases[case]
        want = "1.0" if case == "correct" else "< 1.0"
        print(
            f"{case:10} exec_exit={got.get('exec_exit', '-')} "
            f"reward={got.get('reward', '-')} (want {want})"
        )
    if code == 0:
        print(f"probes: pass   {len(cases)} controls in {time.time() - started:.0f}s")
    print(f"per-step record: {log.relative_to(pkg)}")
    return code


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------


async def _serve(pkg: Path, sock: str, resources: dict | None = None) -> int:
    import asyncio

    import daytona_revalidate as dr
    from recorded_solution import ACTION_PATH, parse_actions, solution_path

    pack = dr.pack

    def log(msg: str) -> None:
        print(f"[{_now()}] {msg}", file=sys.stderr, flush=True)

    state = {
        **_read_state(pkg),
        "status": "booting",
        "sock": sock,
        "pid": os.getpid(),
        "started_at": _now(),
    }
    _write_state(pkg, state)
    # Read once at boot: the harness wrote it before the session, and the
    # agent editing its copy would only mislead its own check, never the
    # loop's probe, which reads the rewrite's own file.
    pretest = _pretest_of(pkg)
    # The row's protected lists ride in the same run/pretest.json; a package
    # file the agent writes overrides them inside to_row, as at the fold.
    protected = pack.Protected.from_pretest_file(pkg / "run" / "pretest.json")
    try:
        row = pack.to_row(str(pkg), pretest=pretest, protected=protected)
    except Exception as e:  # noqa: BLE001 -- the package, not the platform
        log(f"package error: {type(e).__name__}: {e}")
        _write_state(
            pkg,
            {
                **state,
                "status": "failed",
                "error": f"package_error: {type(e).__name__}: {e}"[:500],
            },
        )
        return 2
    md = row["metadata"]
    workdir = md.get("workdir") or "/workspace"
    stop = asyncio.Event()
    lock = asyncio.Lock()

    box = {
        "cpu": None,
        "mem_gb": None,
        "disk_gb": md.get("daytona_disk_gb"),
        **{k: v for k, v in (resources or {}).items() if v is not None},
    }
    log(
        f"boot sandbox for {md['instance_id']} box=[{_box_str(box)}] "
        f"(harness: {dr._harness_provenance()})"
    )
    n_paths = len(dr.protected_paths_of(md["tmax"]))
    n_cmds = len(dr.protected_cmds_of(md["tmax"]))
    if n_paths or n_cmds:
        # A row with protected entries grades by the integrity baseline and
        # never consults the pin hook.
        log(f"integrity baseline: {n_paths} paths, {n_cmds} cmds")
    elif pretest:
        tm = md["tmax"]
        stamped, episode = (
            tm.get("pretest_env_identity"),
            tm.get("pretest_episode_env_identity"),
        )
        log(
            f"pin hook: stamped={stamped or '?'} episode={episode or '?'} -> "
            f"{'runs before the verifier' if stamped and stamped == episode else 'skipped: environment moved'}"
        )
    try:
        async with dr.boot_agent_sandbox(
            md.get("image") or "",
            dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False,
            cpu=box["cpu"],
            memory=box["mem_gb"],
            disk_gb=box["disk_gb"],
            # Booted as the rollouter and the final gate boot it (TB4 GPU/compose).
            **dr.harbor_env.boot_kwargs(md),
        ) as sandbox:
            sb = dr._Root(sandbox)
            if md.get("entrypoint"):
                await dr._start_entrypoint(sb, md["entrypoint"], workdir=workdir)
            await dr.seed_workspace(sb, md["tmax"])
            await dr.harbor_env.run_healthcheck(sb, md.get("healthcheck"))
            agent_sb = await dr.as_agent(sandbox, sb, md)
            # INTEGRITY BASELINE for `grade`: the state as booted, before any
            # request is served -- what a training episode's agent starts
            # from. None for a package without protected entries. The lists
            # are the boot-time ones; `grade` refuses if they change.
            boot_entries = dr.protected_entries_of(md["tmax"])
            boot_baseline = await dr.capture_baseline(
                sb, md["tmax"], workdir=workdir, timeout=EXEC_TIMEOUT
            )

            async def oracle(solve_timeout: int, fast: bool | None = None) -> dict:
                # Re-read the package: the agent edits solution/ and tests/
                # between calls and expects the current files to be judged.
                tmax = pack.to_row(str(pkg), pretest=pretest, protected=protected)[
                    "metadata"
                ]["tmax"]
                sol_dir = pkg / "solution"
                try:
                    srel = solution_path(pkg)
                except FileNotFoundError as exc:
                    return {"ok": False, "error": str(exc)}
                actions = (
                    parse_actions((pkg / srel).read_text())
                    if srel == ACTION_PATH
                    else None
                )
                for f in sorted(sol_dir.rglob("*")):
                    if f.is_file():
                        await sb.write_file(
                            f"/solution/{f.relative_to(sol_dir)}",
                            f.read_text(errors="replace"),
                        )
                # INTEGRITY BASELINE for the oracle: the state the reference
                # solution starts from, taken after solution/ is in and before
                # it runs, with the lists as the package declares them now.
                baseline = await dr.capture_baseline(
                    sb, tmax, workdir=workdir, timeout=EXEC_TIMEOUT
                )
                t0 = time.time()
                execution = await dr.run_reference(
                    agent_sb,
                    "bash /solution/solve.sh",
                    solve_timeout,
                    **(
                        {"actions": actions, "fast": fast}
                        if actions is not None
                        else {}
                    ),
                )
                code, out, err = (
                    execution[key] for key in ("solve_exit", "stdout", "stderr")
                )
                # Before grading, which starts processes of its own.
                measured = await dr.measure(
                    sb, time.time() - t0, tail=(out or "") + (err or "")
                )
                grading = {}
                reward = (
                    await dr.grade_tmax(
                        sb,
                        tmax,
                        workdir=workdir,
                        baseline_digests=baseline,
                        diagnostics=grading,
                    )
                    if execution["submitted"]
                    else 0.0
                )
                return {
                    "ok": True,
                    "solve_exit": code,
                    "reward": reward,
                    "grading": grading,
                    "execution_harness": "terminus",
                    "terminal": execution["terminal"],
                    "transcript": execution["transcript"],
                    "replay": execution.get("replay"),
                    "pane_error": execution.get("pane_error"),
                    "measured": measured,
                    "resources": box,
                    "tail": (out + "\n" + err)[-4000:],
                }

            async def handle(req: dict) -> dict:
                op = req.get("op")
                if op == "ping":
                    return {"ok": True, "sandbox_id": sandbox.sandbox_id}
                if op == "exec":
                    code, out, err = await sb.exec(
                        str(req.get("cmd", "")),
                        check=False,
                        timeout=int(req.get("timeout") or EXEC_TIMEOUT),
                    )
                    return {"ok": True, "code": code, "stdout": out, "stderr": err}
                if op == "oracle":
                    return await oracle(
                        int(req.get("solve_timeout") or SOLVE_TIMEOUT),
                        req.get("fast"),
                    )
                if op == "grade":
                    tmax = pack.to_row(str(pkg), pretest=pretest, protected=protected)[
                        "metadata"
                    ]["tmax"]
                    if dr.protected_entries_of(tmax) != boot_entries:
                        return {
                            "ok": False,
                            "error": "the protected lists changed since up; "
                            "run ./sandbox reset to take a fresh baseline",
                        }
                    grading = {}
                    reward = await dr.grade_tmax(
                        sb,
                        tmax,
                        workdir=workdir,
                        baseline_digests=boot_baseline,
                        diagnostics=grading,
                    )
                    return {"ok": True, "reward": reward, "grading": grading}
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
                log(
                    f"request: {req.get('op')} "
                    f"{str(req.get('cmd', ''))[:120]!r}".rstrip()
                )
                t0 = time.time()
                async with lock:
                    try:
                        resp = await handle(req)
                    except Exception as e:  # noqa: BLE001 -- report, keep serving
                        resp = {
                            "ok": False,
                            "error": f"{type(e).__name__}: {e}"[:500],
                            **getattr(e, "validation", {}),
                        }
                log(
                    f"  -> {'ok' if resp.get('ok') else 'error'} "
                    f"{'code=' + str(resp['code']) if 'code' in resp else ''}"
                    f"{' reward=' + str(resp['reward']) if 'reward' in resp else ''} "
                    f"({time.time() - t0:.1f}s)"
                )
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
                writer.close()

            server = await asyncio.start_unix_server(on_connect, path=sock)
            _write_state(
                pkg,
                {
                    **state,
                    "status": "ready",
                    "ready_at": _now(),
                    "sandbox_id": sandbox.sandbox_id,
                    "workdir": workdir,
                    "resources": {**(state.get("resources") or {}), **box},
                },
            )
            log(f"ready: sandbox {sandbox.sandbox_id}, serving on {sock}")
            await stop.wait()
            server.close()
            await server.wait_closed()
            log("down requested; deleting the sandbox")
    except Exception as e:  # noqa: BLE001 -- the boot itself failed
        log(f"boot failed: {type(e).__name__}: {e}")
        _write_state(
            pkg,
            {
                **_read_state(pkg),
                "status": "failed",
                "error": f"{type(e).__name__}: {e}"[:500],
            },
        )
        return 1
    _write_state(pkg, {**_read_state(pkg), "status": "down", "down_at": _now()})
    log("sandbox deleted")
    return 0


def _one_long_op(pkg: Path, op: str) -> None:
    """Run check, oracle and reset one at a time in this package.

    check and reset take the container down and boot another, so a second one
    started while the first runs destroys the container the first is grading
    in, and the first then reports a failure of its own making. Agents start a
    second one when their exec tool returns before the first has printed (a
    codex exec call returns at about 30 s whatever wait it asked for). The
    second waits for the first instead, and then runs (or reuses its result).
    It waits rather than refuses so that the harness's own check, run after a
    session, is never turned away by a command the session left running. The
    lock is held until this process exits; the kernel releases it if it dies."""
    path = pkg / "run" / "sandbox-op.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        held = os.pread(fd, 200, 0).decode(errors="replace").strip()
        print(
            f"./sandbox {op}: waiting for the one already running ({held}) to "
            "finish; starting now would reset the container it is using",
            flush=True,
        )
        fcntl.flock(fd, fcntl.LOCK_EX)
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"./sandbox {op}, pid {os.getpid()}, since {_now()}".encode(), 0)
    _held_locks.append(fd)


_held_locks: list[int] = []


def main() -> None:
    # Each line reaches the caller as it is printed: an agent whose exec call
    # returns before the command ends sees how far it got, not an empty pipe.
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pkg", default=".", help="the task package directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    max_help = (
        "open the container at the platform ceiling (%(cpu)d vCPU / "
        "%(mem_gb)d GiB / %(disk_gb)d GiB) instead of the training size" % CEILING
    )
    p = sub.add_parser("up")
    p.add_argument("--max", action="store_true", help=max_help)
    p.add_argument(
        "--detach",
        action="store_true",
        help="start the boot and return; exec, grade and oracle wait for it",
    )
    sub.add_parser("down")
    p = sub.add_parser("reset")
    p.add_argument("--max", action="store_true", help=max_help)
    sub.add_parser("status")
    sub.add_parser("size", help="report local size bounds without starting a container")
    sub.add_parser("grade")
    p = sub.add_parser("exec")
    p.add_argument("command")
    p.add_argument("--timeout", type=int, default=EXEC_TIMEOUT)
    fresh_help = "rerun even when an earlier passing result has byte-identical inputs"
    p = sub.add_parser("oracle")
    p.add_argument("--solve-timeout", type=int, default=SOLVE_TIMEOUT)
    p.add_argument("--fresh", action="store_true", help=fresh_help)
    p = sub.add_parser("check")
    p.add_argument("--solve-timeout", type=int, default=SOLVE_TIMEOUT)
    p.add_argument("--max", action="store_true", help=max_help)
    p.add_argument("--fresh", action="store_true", help=fresh_help)
    p = sub.add_parser("probes")
    p.add_argument(
        "--timeout",
        type=int,
        default=SOLVE_TIMEOUT,
        help="per step of each replay: reset, exec, grade (default %(default)s s)",
    )
    p = sub.add_parser("serve")
    p.add_argument("--sock", required=True)
    p.add_argument("--cpu", type=int)
    p.add_argument("--mem-gb", type=int)
    p.add_argument("--disk-gb", type=int)
    args = ap.parse_args()
    pkg = Path(args.pkg).resolve()

    if args.cmd == "serve":
        import asyncio

        sys.exit(
            asyncio.run(
                _serve(
                    pkg,
                    args.sock,
                    {"cpu": args.cpu, "mem_gb": args.mem_gb, "disk_gb": args.disk_gb},
                )
            )
        )
    if args.cmd == "up":
        sys.exit(cmd_up(pkg, at_max=args.max, detach=args.detach))
    if args.cmd == "down":
        sys.exit(cmd_down(pkg))
    if args.cmd in ("check", "oracle", "reset"):
        _one_long_op(pkg, args.cmd)
    if args.cmd == "reset":
        cmd_down(pkg)
        sys.exit(cmd_up(pkg, at_max=args.max))
    if args.cmd == "status":
        sys.exit(cmd_status(pkg))
    if args.cmd == "size":
        sys.exit(cmd_size(pkg))
    if args.cmd == "exec":
        sys.exit(cmd_exec(pkg, args.command, args.timeout))
    if args.cmd == "oracle":
        sys.exit(cmd_oracle(pkg, args.solve_timeout, fresh=args.fresh))
    if args.cmd == "grade":
        sys.exit(cmd_grade(pkg))
    if args.cmd == "check":
        sys.exit(
            cmd_check(pkg, args.solve_timeout, at_max=args.max, fresh=args.fresh)
        )
    if args.cmd == "probes":
        sys.exit(cmd_probes(pkg, args.timeout))


if __name__ == "__main__":
    main()
