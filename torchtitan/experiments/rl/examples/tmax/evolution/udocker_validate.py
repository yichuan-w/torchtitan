#!/usr/bin/env python3
"""Oracle validation with udocker on tridao — no cloud sandbox, no quota.

Why not Daytona: the free tier caps total sandbox disk at 30GiB (~2-3
concurrent) and build-failed sandboxes leak into that cap, which repeatedly
stalled the run for hours.

Why not apptainer: `--fakeroot` falls back to a root-mapped namespace on this
cluster (user not in /etc/subuid), which injects a `faked` binary linked
against glibc 2.33+. That works on ubuntu:22.04 but dies on the older bases
most tasks use (ubuntu:20.04, centos, debian 11).

udocker intercepts syscalls from *outside* the container (PRoot), so root
emulation is independent of the image's glibc. Verified on ubuntu:20.04
(2026-08-12): whoami=root, apt-get install works, /app and /etc writable,
network egress 200, changes persist across runs, volume binds work, and
containers run concurrently.

Per task: parse the Dockerfile -> create a container from its base -> replay
the RUN/COPY steps as a shell script -> run the entrypoint + solution ->
run the verifier -> record the verdict -> delete the container.

Usage (on tridao):
  python scripts/udocker_validate.py --workers 24 [--limit N]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAR = ROOT / "data" / "seed-dataset" / "data" / "tasks-00000.tar"
CTXFREE = ROOT / "data" / "seed-dataset-ctxfree"
RESULTS = ROOT / "results" / "oracle_validation_udocker.jsonl"
PRIOR = ROOT / "results" / "oracle_validation.jsonl"
WORK = Path(os.environ.get("ORACLE_WORK", "/scratch/gpfs/TRIDAO/al9080/oracle-work"))
LOGS = ROOT / "logs"

BUILD_TIMEOUT = 1200
SOLVE_TIMEOUT = 420
TEST_TIMEOUT = 420

log = logging.getLogger("udocker-oracle")
_write_lock = threading.Lock()


# --------------------------------------------------------------- conversion

def _logical_lines(dockerfile: str) -> list[str]:
    """Split a Dockerfile into logical instructions.

    Two multi-line forms, joined differently — conflating them is a silent
    disaster: joining a backslash continuation with a newline turns
    `RUN apt-get install -y \\ / python3` into two commands, so the install
    gets an empty package list and `python3` is then run as a command
    ("command not found"), which looks like a broken base image.

      - backslash continuation -> join with a SPACE (one shell command)
      - heredoc body           -> join with NEWLINES (the body is literal)
    """
    out: list[str] = []
    buf: list[str] = []          # raw lines of the instruction being built
    tag: str | None = None       # heredoc terminator we are waiting for
    saw_heredoc = False

    def flush() -> None:
        nonlocal buf, saw_heredoc
        if not buf:
            return
        if saw_heredoc:
            # Keep verbatim, backslashes and all: the body must stay literal,
            # and bash reads `\`+newline fine. Rewriting it is what corrupts it.
            out.append("\n".join(buf))
        elif len(buf) > 1:
            out.append(" ".join(x.strip().rstrip("\\").strip() for x in buf))
        else:
            out.append(buf[0])
        buf, saw_heredoc = [], False

    for raw in dockerfile.splitlines():
        if tag is not None:                      # inside a heredoc body
            buf.append(raw)
            if raw.strip() == tag:
                tag = None
                if not buf[-2].rstrip().endswith("\\"):
                    flush()
            continue
        # A heredoc can open on a continuation line, not just the first line of
        # the instruction — e.g. `RUN set -e && \` then `cat > f <<'SCRIPT'`.
        # Only checking the first line drops the body into the instruction
        # stream, where it gets executed as commands (unterminated quotes, etc).
        m = re.search(r"<<-?['\"]?(\w+)['\"]?", raw)
        if m and (buf or re.match(r"\s*(COPY|RUN|ADD)\b", raw, re.I)):
            tag = m.group(1)
            saw_heredoc = True
            buf.append(raw)
            continue
        buf.append(raw)
        if not raw.rstrip().endswith("\\"):
            flush()
    flush()
    return out


def parse_dockerfile(text: str) -> dict:
    """Return {base, steps[], env[], workdir, entrypoint, skipped[]}."""
    d: dict = {"base": None, "steps": [], "env": [], "workdir": "/",
               "entrypoint": None, "skipped": []}
    for line in _logical_lines(text):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        head = s.split(None, 1)[0].upper()
        rest = s[len(head):].strip()
        if head == "FROM":
            if d["base"] is None:
                d["base"] = re.sub(r"\s+AS\s+\S+$", "", rest, flags=re.I).strip()
            else:
                d["skipped"].append("multi-stage")
        elif head == "RUN":
            d["steps"].append(rest)
        elif head in ("COPY", "ADD"):
            # our context-free rewriter emits `COPY <<'TAG' /dest` heredocs;
            # as shell that must become `cat > /dest <<'TAG'`
            first = line.split("\n", 1)[0]
            m = re.match(r"\s*(?:COPY|ADD)\s+<<-?['\"]?(\w+)['\"]?\s+(\S+)",
                         first, re.I)
            if m:
                tag, dest = m.group(1), m.group(2)
                body = line.split("\n", 1)[1] if "\n" in line else ""
                d["steps"].append(
                    f"mkdir -p \"$(dirname {dest})\"\ncat > {dest} <<'{tag}'\n{body}")
            else:
                d["skipped"].append(f"{head}:{rest[:50]}")
        elif head == "ENV":
            if "=" in rest:
                for k, v in re.findall(r'(\w+)=("[^"]*"|\S+)', rest):
                    d["env"].append(f"export {k}={v}")
            else:
                p = rest.split(None, 1)
                if len(p) == 2:
                    d["env"].append(f'export {p[0]}="{p[1]}"')
        elif head == "WORKDIR":
            d["workdir"] = rest
            d["steps"].append(f"mkdir -p {rest}")
        elif head == "ENTRYPOINT":
            d["entrypoint"] = rest
        elif head in ("USER", "EXPOSE", "LABEL", "ARG", "VOLUME", "CMD",
                      "SHELL", "HEALTHCHECK", "STOPSIGNAL", "ONBUILD"):
            d["skipped"].append(head)
    return d


def entrypoint_cmd(ep: str | None) -> str | None:
    if not ep:
        return None
    if ep.startswith("["):
        try:
            parts = json.loads(ep)
        except json.JSONDecodeError:
            return None
        parts = [p for p in parts if p not in ("sleep", "infinity")]
        return " ".join(parts) if parts else None
    return ep


# ------------------------------------------------------------------ running

def load_tasks() -> dict[str, dict]:
    tasks: dict[str, dict] = {}
    with tarfile.open(TAR) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/")
            tid, rel = parts[1], "/".join(parts[2:])
            tasks.setdefault(tid, {})[rel] = tf.extractfile(m).read()
    status = {}
    with open(CTXFREE / "report.jsonl") as f:
        for line in f:
            r = json.loads(line)
            status[r["task_id"]] = r["status"]
    for tid, t in tasks.items():
        if status.get(tid) == "rewritten":
            t["environment/Dockerfile"] = (
                CTXFREE / "dockerfiles" / f"{tid}.Dockerfile").read_bytes()
        t["_ctx_status"] = status.get(tid, "unchanged")
    return tasks


def sh(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = "\n".join(l for l in (p.stdout + p.stderr).splitlines()
                        if not l.startswith(" *") and "STARTING" not in l
                        and "executing:" not in l)
        return p.returncode, out[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"


def udk(args: list[str], timeout: int) -> tuple[int, str]:
    return sh(["udocker", "--allow-root"] + args, timeout)


def validate_one(tid: str, t: dict) -> dict:
    rec = {"task_id": tid, "ctx_status": t["_ctx_status"], "runner": "udocker",
           "t_start": time.time()}
    if t["_ctx_status"].startswith("failed"):
        rec.update(status="skipped", reason="no context-free dockerfile")
        return rec

    cname = f"orc{tid.replace('tw_', '')}"
    host = WORK / tid
    try:
        shutil.rmtree(host, ignore_errors=True)
        (host / "oracle").mkdir(parents=True, exist_ok=True)
        for rel, data in t.items():
            if rel.startswith(("solution/", "tests/")):
                p = host / "oracle" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)

        spec = parse_dockerfile(t["environment/Dockerfile"].decode("utf-8", "replace"))
        if not spec["base"]:
            rec.update(status="skipped", reason="no FROM")
            return rec
        rec["base"] = spec["base"]

        udk(["rm", cname], 60)
        rc, out = udk(["create", f"--name={cname}", spec["base"]], 600)
        if rc != 0:
            rec.update(status="build_failed", stage="create", build_tail=out[-500:])
            return rec

        env = "\n".join(spec["env"])
        build_script = ("set -e\nexport DEBIAN_FRONTEND=noninteractive\n"
                        + env + "\n" + "\n".join(spec["steps"]))
        (host / "build.sh").write_text(build_script)

        t0 = time.time()
        vol = f"{host}:/oracle_host"
        rc, out = udk(["run", "--user=root", "-v", vol, cname,
                       "bash", "/oracle_host/build.sh"], BUILD_TIMEOUT)
        rec["build_s"] = round(time.time() - t0, 1)
        if rc != 0:
            rec.update(status="build_failed", stage="run-steps", build_tail=out[-700:])
            return rec

        ep = entrypoint_cmd(spec["entrypoint"])
        pre = f"({ep} >/tmp/_ep.log 2>&1 &); sleep 2; " if ep else ""
        wd = spec["workdir"] or "/"

        t0 = time.time()
        rc, out = udk(["run", "--user=root", "-v", vol, "-w", wd, cname, "bash", "-lc",
                       f"{env}\n{pre}cp -r /oracle_host/oracle /oracle 2>/dev/null; "
                       f"bash /oracle/solution/solve.sh"], SOLVE_TIMEOUT)
        rec["solve_s"] = round(time.time() - t0, 1)
        rec["solve_exit"] = rc

        rc2, out2 = udk(["run", "--user=root", "-v", vol, "-w", wd, cname, "bash", "-lc",
                         f"{env}\n{pre}bash /oracle/tests/test.sh"], TEST_TIMEOUT)
        rec["test_exit"] = rc2
        rec["status"] = "pass" if rc2 == 0 else "fail"
        if rc2 != 0:
            rec["test_tail"] = out2[-600:]
            rec["solve_tail"] = out[-400:]
    except Exception as e:  # noqa: BLE001
        rec.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
    finally:
        udk(["rm", cname], 120)
        shutil.rmtree(host, ignore_errors=True)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    LOGS.mkdir(exist_ok=True)
    RESULTS.parent.mkdir(exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(LOGS / "udocker_validate.log")])

    done = set()
    for f in (RESULTS, PRIOR):
        if f.exists():
            for line in f.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r.get("status") in ("pass", "fail", "skipped"):
                        done.add(r["task_id"])

    tasks = load_tasks()
    pending = [(tid, t) for tid, t in sorted(tasks.items()) if tid not in done]
    if args.limit:
        pending = pending[:args.limit]
    log.info("tasks %d total, %d judged, %d pending", len(tasks), len(done), len(pending))

    # Pre-pull unique bases serially: concurrent pulls of the same image race.
    bases = Counter()
    for tid, t in pending:
        spec = parse_dockerfile(t["environment/Dockerfile"].decode("utf-8", "replace"))
        if spec["base"]:
            bases[spec["base"]] += 1
    log.info("unique base images: %d (top: %s)", len(bases), bases.most_common(5))
    for i, (img, n) in enumerate(bases.most_common()):
        rc, _ = udk(["pull", img], 900)
        log.info("[pull %d/%d] %s (%d tasks) rc=%s", i + 1, len(bases), img, n, rc)

    counts: dict[str, int] = {}
    with open(RESULTS, "a") as out, ThreadPoolExecutor(args.workers) as pool:
        futs = {pool.submit(validate_one, tid, t): tid for tid, t in pending}
        for i, fut in enumerate(as_completed(futs)):
            rec = fut.result()
            rec["t_end"] = time.time()
            with _write_lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
            counts[rec["status"]] = counts.get(rec["status"], 0) + 1
            log.info("[%d/%d] %s -> %s (build %ss) %s", i + 1, len(pending),
                     rec["task_id"], rec["status"], rec.get("build_s", "-"),
                     dict(sorted(counts.items())))
    log.info("RUN DONE: %s", counts)


if __name__ == "__main__":
    main()
