#!/usr/bin/env python3
"""The container a task-evolution agent works in, one per session.

The agent's only check used to be `./validate`: a full image build, the
reference solution, the verifier -- minutes per call and nothing in between.
No way to run one command in the environment, read a log, or see which check
failed with the state still there; sessions spent twenty minutes polling two
of those and timed out. This hands the agent the container itself.

`./sandbox` in the package directory is a shell wrapper over this file:

    ./sandbox up            build the image, boot a container, seed the
                            workspace, start the entrypoint (minutes)
    ./sandbox exec 'CMD'    run CMD inside, as root, from the image's workdir;
                            --timeout N (default 120 s); exits with CMD's code
    ./sandbox oracle        copy solution/ in, run solve.sh, grade with the
                            package's verifier; prints the reward
    ./sandbox grade         grade the current state without running anything
    ./sandbox reset         down, then up: a fresh container from the current
                            Dockerfile
    ./sandbox check         reset; grade, which must FAIL (a verifier that
                            passes an untouched workspace pays for nothing);
                            oracle, which must pass. Prints VERDICT: pass|fail.
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
solution is picked up without a reset; an edited Dockerfile needs `reset`.
Every check's verdict is appended to run/checks.jsonl, which the caller reads
to learn whether the agent ever saw its own rewrite pass.
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
        s.shutdown(socket.SHUT_WR)
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
          f"since={state.get('ready_at') or state.get('started_at', '-')}")
    return 0 if live else 1


def cmd_up(pkg: Path) -> int:
    state = _read_state(pkg)
    if _alive(state):
        print(f"sandbox already up (id={state.get('sandbox_id')}); "
              f"use ./sandbox reset for a fresh one")
        return 0
    log = pkg / "run" / "sandbox.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    sock = tempfile.mktemp(prefix="evolve-sandbox-", suffix=".sock", dir="/tmp")
    _write_state(pkg, {"status": "booting", "sock": sock, "started_at": _now()})
    with log.open("a") as lf:
        lf.write(f"[{_now()}] up: booting a sandbox for {pkg}\n")
        lf.flush()
        # --pkg is a top-level option and has to precede the subcommand.
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--pkg", str(pkg),
             "serve", "--sock", sock],
            stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
            start_new_session=True, cwd=str(pkg))
    started = time.time()
    last_note = 0.0
    while True:
        state = _read_state(pkg)
        if state.get("status") == "ready":
            print(f"sandbox up: id={state.get('sandbox_id')} workdir={state.get('workdir')} "
                  f"in {time.time() - started:.0f}s")
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


def _print_oracle(r: dict) -> None:
    print(f"oracle: reward={r.get('reward')} solve_exit={r.get('solve_exit')}")
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


def cmd_check(pkg: Path, solve_timeout: int) -> int:
    """The whole verdict on a fresh container: rebuild, null probe, oracle."""
    started = time.time()
    if _read_state(pkg).get("status") not in (None, "down"):
        cmd_down(pkg)
    if cmd_up(pkg) != 0:
        _append_check(pkg, {"time": _now(), "verdict": "fail", "stage": "boot"})
        print("VERDICT: fail   stage=boot (the environment did not come up)")
        return 1
    state = _read_state(pkg)
    null = _request(state, "grade", wait=900)
    if not null.get("ok"):
        print(f"VERDICT: error   stage=null_probe ({null.get('error')})")
        return 2
    null_reward = float(null.get("reward") or 0)
    if null_reward >= 1.0:
        _append_check(pkg, {"time": _now(), "verdict": "fail", "stage": "null_probe",
                            "null_reward": null_reward})
        print(f"VERDICT: fail   stage=null_probe   null_reward={null_reward}")
        print("The verifier passes on the untouched workspace: it pays for nothing.\n"
              "Make it depend on work the solution has to do, then check again.")
        return 1
    r = _request(state, "oracle", wait=solve_timeout + 600, solve_timeout=solve_timeout)
    if not r.get("ok"):
        print(f"VERDICT: error   stage=oracle ({r.get('error')})")
        return 2
    reward = float(r.get("reward") or 0)
    ok = reward >= 1.0
    _append_check(pkg, {"time": _now(), "verdict": "pass" if ok else "fail",
                        "stage": "oracle", "reward": reward,
                        "solve_exit": r.get("solve_exit"), "null_reward": null_reward,
                        "elapsed_s": round(time.time() - started)})
    print(f"VERDICT: {'pass' if ok else 'fail'}   reward={reward}   "
          f"solve_exit={r.get('solve_exit')}   null_reward={null_reward}   "
          f"took={time.time() - started:.0f}s")
    if not ok:
        tail = r.get("tail") or ""
        print("--- what the run printed (tail) ---")
        print(tail if tail.strip() else "(empty)")
        print("\nFix the task so the reference solution scores 1.0, then check again. "
              "The container is still up: ./sandbox exec to look around.")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# server
# --------------------------------------------------------------------------

async def _serve(pkg: Path, sock: str) -> int:
    import asyncio

    import daytona_revalidate as dr
    pack = dr.pack

    def log(msg: str) -> None:
        print(f"[{_now()}] {msg}", file=sys.stderr, flush=True)

    state = {**_read_state(pkg), "status": "booting", "sock": sock,
             "pid": os.getpid(), "started_at": _now()}
    _write_state(pkg, state)
    try:
        row = pack.to_row(str(pkg))
    except Exception as e:  # noqa: BLE001 -- the package, not the platform
        log(f"package error: {type(e).__name__}: {e}")
        _write_state(pkg, {**state, "status": "failed",
                           "error": f"package_error: {type(e).__name__}: {e}"[:500]})
        return 2
    md = row["metadata"]
    workdir = md.get("workdir") or "/workspace"
    stop = asyncio.Event()
    lock = asyncio.Lock()

    log(f"boot sandbox for {md['instance_id']} (harness: {dr._harness_provenance()})")
    try:
        async with dr.boot_agent_sandbox(
            md.get("image") or "",
            dockerfile=md.get("dockerfile") or None,
            build_context=md.get("build_context") or None,
            install_claude=False,
            disk_gb=md.get("daytona_disk_gb"),
        ) as sandbox:
            sb = dr._Root(sandbox)
            if md.get("entrypoint"):
                await dr._start_entrypoint(sb, md["entrypoint"], workdir=workdir)
            await dr.seed_workspace(sb, md["tmax"])

            async def oracle(solve_timeout: int) -> dict:
                # Re-read the package: the agent edits solution/ and tests/
                # between calls and expects the current files to be judged.
                tmax = pack.to_row(str(pkg))["metadata"]["tmax"]
                sol_dir = pkg / "solution"
                if not (sol_dir / "solve.sh").exists():
                    return {"ok": False, "error": "package ships no solution/solve.sh"}
                for f in sorted(sol_dir.rglob("*")):
                    if f.is_file():
                        await sb.write_file(f"/solution/{f.relative_to(sol_dir)}",
                                            f.read_text(errors="replace"))
                code, out, err = await sb.exec("bash /solution/solve.sh", check=False,
                                               timeout=solve_timeout)
                reward = await dr.grade_tmax(sb, tmax, workdir=workdir)
                return {"ok": True, "solve_exit": code, "reward": reward,
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
                    tmax = pack.to_row(str(pkg))["metadata"]["tmax"]
                    reward = await dr.grade_tmax(sb, tmax, workdir=workdir)
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
                               "sandbox_id": sandbox.sandbox_id, "workdir": workdir})
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
    sub.add_parser("up")
    sub.add_parser("down")
    sub.add_parser("reset")
    sub.add_parser("status")
    sub.add_parser("grade")
    p = sub.add_parser("exec")
    p.add_argument("command")
    p.add_argument("--timeout", type=int, default=EXEC_TIMEOUT)
    p = sub.add_parser("oracle")
    p.add_argument("--solve-timeout", type=int, default=SOLVE_TIMEOUT)
    p = sub.add_parser("check")
    p.add_argument("--solve-timeout", type=int, default=SOLVE_TIMEOUT)
    p = sub.add_parser("serve")
    p.add_argument("--sock", required=True)
    args = ap.parse_args()
    pkg = Path(args.pkg).resolve()

    if args.cmd == "serve":
        import asyncio
        sys.exit(asyncio.run(_serve(pkg, args.sock)))
    if args.cmd == "up":
        sys.exit(cmd_up(pkg))
    if args.cmd == "down":
        sys.exit(cmd_down(pkg))
    if args.cmd == "reset":
        cmd_down(pkg)
        sys.exit(cmd_up(pkg))
    if args.cmd == "status":
        sys.exit(cmd_status(pkg))
    if args.cmd == "exec":
        sys.exit(cmd_exec(pkg, args.command, args.timeout))
    if args.cmd == "oracle":
        sys.exit(cmd_oracle(pkg, args.solve_timeout))
    if args.cmd == "grade":
        sys.exit(cmd_grade(pkg))
    if args.cmd == "check":
        sys.exit(cmd_check(pkg, args.solve_timeout))


if __name__ == "__main__":
    main()
