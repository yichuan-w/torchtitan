#!/usr/bin/env python3
"""The evolve loop: signals under runs/*/signals -> one rewrite each -> the mix.

The trainer writes one signal per zero-variance group into its run directory:
a task the policy solved 0/k (too hard, make it easier) or k/k (too easy, make
it harder). This loop reads those signals, rewrites each task the feedback way
in a directory of its own, and folds what survives revalidation into the live
mix as a new version that TMaxDataset hot-reloads. It runs on the same machine
as the training that feeds it. LAYOUT.md is the contract; every path here
comes from layout.py.

A round:
  1. discover: every signal file under runs/*/signals whose id has no ledger
     line (or whose latest line is `deferred` while that direction is on).
     Nothing under a run directory is ever moved or deleted; the ledger is
     the loop's memory.
  2. choose: one signal per task, the newest whose `rev` is the task's
     current revision. Two rewrites of one revision in one round would be
     siblings racing for the same r<N+1>, and a signal about a revision that
     is no longer the task's measured a policy input that is gone; both get a
     `superseded` line and the reason.
  3. handle, concurrently across tasks: copy r<rev> to the rewrite's
     package/, hardlink the rollout records under package/traces/, run
     feedback_loop.process_one there, record the verdict in rewrite.json.
  4. fold: for every accepted rewrite, strip the harness files, rename
     package/ to r<N+1>/, rebuild the row and publish one new mix version for
     the round. Then the lineage lines, then the ledger line, last.
  5. rebuild status.json from the ledger and every task's files, and commit
     the records (never packages, sessions or traces) to the audit repo.

Observable (one log line per signal, one per fold), resumable (a signal with
no ledger line is handled again; a rewrite the loop died inside is marked by
finalize_interrupted_traces), reproducible (a rewrite directory holds the
input revision it started from, the records it read, and every session).

Usage:
  evolve_ondella.py --once                         one round, then exit
  evolve_ondella.py --interval 120 --workers 16    continuous
  evolve_ondella.py --once --dry --only <task>     handle, publish nothing
  evolve_ondella.py --signal <run>/<task>--g<N>    replay one handled signal (dry)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evolve_codex as ec       # noqa: E402
import feedback_loop as fb      # noqa: E402
import pack_to_dataset as pack  # noqa: E402
import synth_operators as ops   # noqa: E402
from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402

log = logging.getLogger("evolve")

# The 0/k -> easier branch is switchable, because the ratchet it drives only
# turns one way. A simplify is accepted almost every time -- the revalidation
# asks whether solve.sh still passes, and rewriting the instruction cannot break
# solve.sh -- while an evolve has to survive a rebuilt verifier. Measured on
# this corpus: 693 accepted simplifies against 335 accepted evolves in a week,
# and 814 against 26 in an earlier window, with the on-mix solve rate climbing
# while the fixed eval stayed flat. Off, the too-hard tail freezes instead of
# being loosened, and the only signals that move a task are the ones asking
# for more difficulty. A deferred signal is replayed when the switch turns on.
SIMPLIFY_ENABLED = os.environ.get("SWE_EVOLVE_SIMPLIFY", "1").lower() not in (
    "0", "false", "no")
# Where a seed package comes from, under $TRL_BASE/data/sources/<corpus>/tasks.
SOURCE_CORPORA = ("swe-extract", "tw-extract", "tmax-extract")
# What a seed copy leaves behind: backups of the pre-canary-strip instruction
# are not part of the task and would show the agent text the pool deliberately
# removed; the other two are records of an older loop's, not the package's.
SEED_IGNORE = shutil.ignore_patterns("*.bak-*", ".provenance.json", ".resources.json",
                                     "__pycache__", ".git")
SIGNAL_KEYS = ("task", "rev", "run", "group", "direction", "solved", "total", "attempts")
# An unreadable signal younger than this may still be being written; LAYOUT
# has the trainer rename it into place, so this is belt and braces.
FRESH_SEC = 60


def _env_int(name: str) -> int | None:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else None


# The fleet default: what a row declaring no daytona_* of its own gets in
# training. Read from this process's env the way the harness reads it, so it
# is the trainer's only when the launcher carries the trainer's TT_DAYTONA_*
# across; main() logs what it resolved to. On the live mix every row declares
# all three (663/663 on wd-20260903b), so this is the fallback for a rebuilt
# mix, not the common path.
FLEET = {"cpu": _env_int("TT_DAYTONA_CPU"), "mem_gb": _env_int("TT_DAYTONA_MEM_GB"),
         "disk_gb": _env_int("TT_DAYTONA_DISK_GB")}
ROW_KEYS = {"cpu": "daytona_cpu", "mem_gb": "daytona_mem_gb",
            "disk_gb": "daytona_disk_gb"}


def declared_resources(mix: Path) -> dict[str, dict]:
    """Per row of the mix, the daytona_* it declares, keyed by instance id."""
    out: dict[str, dict] = {}
    if not mix.exists():
        return out
    for ln in open(mix):
        if not ln.strip():
            continue
        md = json.loads(ln).get("metadata") or {}
        iid = md.get("instance_id")
        if iid:
            out[iid] = {k: md[rk] for k, rk in ROW_KEYS.items() if rk in md}
    return out


def training_box(tid: str, declared: dict[str, dict] | None) -> dict:
    """The size training gives this task: the row's own values, the fleet
    default where the row declares nothing, None where neither says (the
    harness default then applies, and the source names that)."""
    own = (declared or {}).get(tid) or {}
    box = {k: own[k] if own.get(k) is not None else FLEET[k] for k in ROW_KEYS}
    if all(own.get(k) is not None for k in ROW_KEYS):
        src = "row"
    elif any(box[k] is None for k in ROW_KEYS):
        src = "row+fleet_default+harness_default" if own else "harness_default"
    else:
        src = "row+fleet_default" if own else "fleet_default"
    return {**box, "source": src}


# --------------------------------------------------------------------------
# One loop per root
# --------------------------------------------------------------------------

LOCK_HEARTBEAT_SEC = 30
# A contender on another node treats a heartbeat older than this as a dead
# holder. Three beats: one missed beat is a stalled GPFS write, not a death.
LOCK_STALE_SEC = 3 * LOCK_HEARTBEAT_SEC


def _heartbeat(fd: int) -> None:
    while True:
        time.sleep(LOCK_HEARTBEAT_SEC)
        try:
            os.utime(fd)
        except OSError:
            pass


def acquire_singleton(lock_path: Path) -> int:
    """Hold the one-loop-per-root lock for the life of this process.

    The loop is a singleton by contract -- two instances over one root handle
    the same signals, send the same k/k task to Codex twice and fold over
    each other's mix -- but nothing enforced it. There are several ways to
    launch it (restart_evolve.sh, the training launcher, a hand-typed
    command); only one stops the previous instance first. Measured
    2026-09-02: nine launches in one day, two instances alive at once for 27
    minutes. Guarding every launcher is whack-a-mole; the process guards
    itself.

    Two layers, because no single primitive reaches across nodes here:

    * flock, for the node the loop runs on. The kernel drops it when the
      holder dies, however it dies, so a SIGKILLed loop never leaves a stale
      lock. flock rather than fcntl: a POSIX record lock is released the
      moment its process closes any fd on the file, which a stray read of the
      lock file would do silently.
    * a heartbeat, for other nodes. Neither flock nor fcntl held across nodes
      on this GPFS mount (measured 2026-09-02: a holder on della-tridao did
      not stop a contender on della-gpu), so the holder touches the file's
      mtime every LOCK_HEARTBEAT_SEC and a contender that finds another host
      named in the file with a heartbeat under LOCK_STALE_SEC refuses. A dead
      holder on another node clears itself after LOCK_STALE_SEC; nothing has
      to be removed by hand.

    Test modes (--once, --only, --dry) take it too: a hand round over a live
    loop's root collides the same way. The returned fd must stay open.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 4096, 0).decode(errors="replace").strip()
        os.close(fd)
        raise SystemExit(
            f"evolve_ondella: another instance already runs over "
            f"{lock_path.parent} ({holder or 'holder unknown'}). Stop it "
            f"first -- restart_evolve.sh does -- rather than start a second."
        ) from None
    host = socket.gethostname()
    holder = os.pread(fd, 4096, 0).decode(errors="replace").strip()
    age = time.time() - os.fstat(fd).st_mtime
    if holder and f"host={host} " not in holder and age < LOCK_STALE_SEC:
        os.close(fd)
        raise SystemExit(
            f"evolve_ondella: another instance runs over {lock_path.parent} "
            f"on a different node, heartbeat {age:.0f}s old ({holder}). Stop "
            f"it there; if it is already dead, its heartbeat goes stale after "
            f"{LOCK_STALE_SEC}s and this start will go through."
        )
    os.ftruncate(fd, 0)
    os.write(fd, (f"host={host} pid={os.getpid()} "
                  f"started={time.strftime('%Y-%m-%dT%H:%M:%S%z')} "
                  f"argv={' '.join(sys.argv)}\n").encode())
    threading.Thread(target=_heartbeat, args=(fd,), daemon=True,
                     name="evolve-lock-heartbeat").start()
    return fd


# --------------------------------------------------------------------------
# Discovery: the ledger is the loop's memory
# --------------------------------------------------------------------------

@dataclass
class Signal:
    """One signal file as the loop sees it. `data` is None when it did not
    parse; `junk` then says why."""

    run: layout.Run
    path: Path
    sid: str
    data: dict | None = None
    junk: str | None = None

    @property
    def task(self) -> str:
        if self.data:
            return str(self.data["task"])
        return self.path.stem.rpartition("--g")[0] or self.path.stem

    @property
    def group(self) -> int | None:
        if self.data:
            return self.data.get("group")
        tail = self.path.stem.rpartition("--g")[2]
        return int(tail) if tail.isdigit() else None


class NoSeed(Exception):
    """The task has no r0 and no source corpus carries it."""


def load_ledger(root: layout.Root) -> dict[str, dict]:
    """The latest ledger line per signal id."""
    latest: dict[str, dict] = {}
    for line in layout.read_jsonl(root.evolution.ledger):
        sid = line.get("signal")
        if sid:
            latest[sid] = line
    return latest


def _pending(root: layout.Root, ledger: dict[str, dict]) -> list[tuple[layout.Run, Path, str]]:
    """Every signal file the ledger does not close: no line at all, or a
    latest line of `deferred` while the easier direction is on. The id is
    the run and the file's stem, which is what layout.signal_id spells."""
    out = []
    for run in root.run_dirs():
        for p in run.signal_files():
            sid = f"{run.name}/{p.stem}"
            last = ledger.get(sid)
            if last is None or (SIMPLIFY_ENABLED and last.get("outcome") == "deferred"):
                out.append((run, p, sid))
    return out


def read_signal(path: Path) -> tuple[dict | None, str | None]:
    """(data, None) for a signal; (None, why) for junk; (None, None) for a
    file too young to condemn."""
    try:
        data = json.loads(path.read_text())
        missing = [k for k in SIGNAL_KEYS if k not in data]
        if missing:
            return None, f"signal lacks {missing}"
        if data["direction"] not in ("harder", "easier"):
            return None, f"unknown direction {data['direction']!r}"
        # Empty when the trainer runs with SWE_ROLLOUT_RECORDS=0: the group's
        # verdict still stands, the agent just has no traces to read.
        if not isinstance(data["attempts"], list):
            return None, "attempts is not a list"
        int(data["rev"])
        return data, None
    except Exception as e:  # noqa: BLE001 -- anything unreadable is junk
        try:
            fresh = time.time() - path.stat().st_mtime < FRESH_SEC
        except OSError:
            return None, None
        if fresh:
            return None, None
        return None, f"unreadable: {type(e).__name__}: {e}"[:200]


def discover(root: layout.Root, ledger: dict[str, dict]) -> list[Signal]:
    found = []
    for run, p, sid in _pending(root, ledger):
        data, junk = read_signal(p)
        if data is None and junk is None:
            log.warning("signal %s does not parse yet (fresh, retrying)", sid)
            continue
        found.append(Signal(run, p, sid, data, junk))
    return found


def choose(root: layout.Root, signals: list[Signal]
           ) -> tuple[list[Signal], list[tuple[Signal, str, Signal | None]]]:
    """One signal per task: the newest whose rev is the task's current one.

    Returns the picks and the rest as (signal, why, the pick it yields to);
    a signal about a revision that is no longer current yields to nothing.
    """
    by_task: dict[str, list[Signal]] = {}
    for s in signals:
        by_task.setdefault(s.task, []).append(s)
    picks, rest = [], []
    for tid, items in sorted(by_task.items()):
        current = root.evolution.task(tid).latest_rev() or 0
        items.sort(key=lambda s: (str(s.data.get("created", "")), s.path.name))
        matching = [s for s in items if int(s.data["rev"]) == current]
        pick = matching[-1] if matching else None
        for s in items:
            if s is pick:
                continue
            if int(s.data["rev"]) != current:
                rest.append((s, f"rev {s.data['rev']} is not the task's current rev {current}", None))
            else:
                rest.append((s, f"newer signal {pick.sid} covers rev {current}", pick))
        if pick is not None:
            picks.append(pick)
    return picks, rest


# --------------------------------------------------------------------------
# Handling one signal
# --------------------------------------------------------------------------

def materialize_r0(root: layout.Root, task: layout.TaskDir, tid: str) -> Path:
    """r0 is the seed package, copied once from whichever corpus carries it.
    Copied whole into r0.incoming and renamed, so a crash mid-copy cannot
    leave a half package that a later round would evolve."""
    for corpus in SOURCE_CORPORA:
        src = root.data / "sources" / corpus / "tasks" / tid
        if (src / "instruction.md").exists():
            break
    else:
        raise NoSeed(f"no seed package for {tid} under data/sources/"
                     f"{{{','.join(SOURCE_CORPORA)}}}/tasks")
    dest = task.rev(0)
    incoming = dest.with_name("r0.incoming")
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.copytree(src, incoming, ignore=SEED_IGNORE)
    os.rename(incoming, dest)
    log.info("%s: r0 materialized from %s", tid, src.relative_to(root.path))
    return dest


def _new_rewrite_dir(task: layout.TaskDir, job: str) -> layout.RewriteDir:
    t = time.time()
    rw = task.rewrite(job, layout.stamp(t))
    while rw.path.exists():
        t += 1
        rw = task.rewrite(job, layout.stamp(t))
    return rw


def _compact_resources(p: dict | None) -> dict | None:
    """The provisioned size as rewrite.json and the row carry it."""
    if not p:
        return None
    return {**{k: p.get(k) for k in ROW_KEYS}, "source": p.get("source"),
            "measured": p.get("measured")}


def handle(root: layout.Root, sig: Signal, *, declared: dict[str, dict],
           history: tuple[dict, dict], dry: bool = False) -> dict:
    """One signal, one rewrite directory, one verdict in rewrite.json.

    An accepted rewrite stays `running` on disk until the fold renames its
    package: the rewrite is not done until the revision exists, and a loop
    that dies in between leaves a record finalize_interrupted_traces marks
    rather than an `accepted` with no revision behind it.
    """
    d = sig.data
    tid, rev = str(d["task"]), int(d["rev"])
    job = "harder" if d["direction"] == "harder" else "easier"
    task = root.evolution.task(tid)
    src = task.rev(rev)
    if not src.exists():
        if rev != 0:
            raise NoSeed(f"r{rev} does not exist under {task.path.relative_to(root.path)}")
        src = materialize_r0(root, task, tid)
    rewrite = _new_rewrite_dir(task, job)
    rewrite.path.mkdir(parents=True)
    meta = {
        "task": tid, "job": job, "signal": sig.sid, "input_rev": rev,
        "started": layout.stamp(), "finished": None, "status": "running",
        "operator": None, "arm": os.environ.get("SWE_RETUNE_AGENT", "chat"),
        "verdicts": None, "resources": None, "result_rev": None, "sessions": [],
    }
    if dry:
        meta["dry"] = True
    layout.write_json_atomic(rewrite.meta, meta)
    try:
        shutil.copytree(src, rewrite.package)
        run_dir = root.run(str(d["run"])).path
        for i, rel in enumerate(d["attempts"], 1):
            layout.link_or_copy(run_dir / rel, rewrite.traces / f"attempt-{i:02d}.jsonl")
        rec = fb.process_one(rewrite, d, job=job, seed_dir=src,
                             resources=training_box(tid, declared), history=history)
    except Exception as e:  # noqa: BLE001 -- the rewrite records its own failure
        rec = {"status": "failed", "stage": "setup", "reason": f"{type(e).__name__}: {e}"[:300]}
    for key in ("operator", "family", "hint", "stage", "reason", "verdicts", "changed",
                "oracle_repair", "agent_validated", "cyber_filtered", "usage"):
        if key in rec:
            meta[key] = rec[key]
    meta["resources"] = _compact_resources(rec.get("resources"))
    meta["sessions"] = [f"sessions/{s.path.name}" for s in rewrite.session_dirs()]
    status = rec.get("status") or "failed"
    if status == "accepted" and not dry:
        meta["status"] = "running"
    else:
        meta["status"] = status
        meta["finished"] = layout.stamp()
    layout.write_json_atomic(rewrite.meta, meta)
    return {"signal": sig, "rewrite": rewrite, "meta": meta, "status": status}


def strip_harness(pkg: Path) -> None:
    """Take out what the harness put in, before a package becomes a revision."""
    for name in ec.HARNESS:
        p = pkg / name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
    for d in list(pkg.rglob("__pycache__")):
        shutil.rmtree(d, ignore_errors=True)


def _rewrite_ref(root: layout.Root, rewrite: layout.RewriteDir) -> str:
    return str(rewrite.path.relative_to(root.evolution.path))


def _ledger_line(root: layout.Root, sig: Signal, outcome: str, **extra) -> None:
    d = sig.data or {}
    layout.append_jsonl(root.evolution.ledger, {
        "stamp": layout.stamp(), "signal": sig.sid, "task": sig.task,
        "rev": d.get("rev"), "run": sig.run.name, "group": sig.group,
        "direction": d.get("direction"), "outcome": outcome, **extra,
    })


def _close(root: layout.Root, h: dict, *, dry: bool) -> None:
    """The lineage index line, then the ledger line, last: a signal is closed
    only once everything about its rewrite is on disk."""
    if dry:
        return
    rewrite, meta = h["rewrite"], h["meta"]
    task = root.evolution.task(meta["task"])
    layout.append_jsonl(task.lineage, {
        "stamp": layout.stamp(), "event": "rewrite", "rewrite": f"rewrites/{rewrite.path.name}",
        "job": meta["job"], "input_rev": meta["input_rev"], "status": meta["status"],
    })
    _ledger_line(root, h["signal"], "handled", rewrite=_rewrite_ref(root, rewrite))


def _finish(h: dict, status: str, *, stage: str, reason: str) -> None:
    meta = h["meta"]
    meta.update({"status": status, "stage": stage, "reason": reason[:300],
                 "finished": layout.stamp()})
    layout.write_json_atomic(h["rewrite"].meta, meta)
    h["status"] = status


def fold(root: layout.Root, accepted: list[dict]) -> int | None:
    """Every accepted rewrite of the round into one new mix version.

    The row is rebuilt from the package (pack.to_row, so a folded row is
    indistinguishable from a freshly prepared one) and provisioned at
    max(what the seed had, what the reference solution measured): the
    daytona_* keys do not live in a package, so a folded row would otherwise
    arrive without them and fall back to the fleet default -- on this corpus
    1 CPU against a measured 2, a task starved into reading as too hard.
    Without a measurement the replaced row's values carry across.

    A task no longer in the mix is not re-added: it was taken out
    deliberately, and a new label lands at the END of the file, which
    rotates the held-out slice. Replace only.
    """
    if not accepted:
        return None
    mix = root.mix
    rows: dict[str, str] = {}
    order: list[str] = []
    for ln in mix.live.read_text().splitlines():
        if ln.strip():
            iid = json.loads(ln)["metadata"]["instance_id"]
            rows[iid] = ln
            order.append(iid)
    folded = []
    for h in accepted:
        meta, rewrite = h["meta"], h["rewrite"]
        tid, n = meta["task"], int(meta["input_rev"])
        task = root.evolution.task(tid)
        target = task.rev(n + 1)
        if tid not in rows:
            _finish(h, "rejected", stage="not_in_mix", reason="no longer in the mix")
            continue
        if target.exists():
            # Cannot happen while one loop runs: choose() hands out one
            # signal per task per round. A revision is never overwritten.
            _finish(h, "failed", stage="fold", reason=f"{target.name} already exists")
            continue
        try:
            strip_harness(rewrite.package)
            # The directory is `package` now and `r<N>` once renamed, so the
            # row's identity is passed rather than read off the name.
            row = pack.to_row(str(rewrite.package), task_id=tid)
        except Exception as e:  # noqa: BLE001 -- one malformed package, not the round
            _finish(h, "failed", stage="fold", reason=f"{type(e).__name__}: {e}")
            continue
        old_md = json.loads(rows[tid])["metadata"]
        sized = meta.get("resources") or {}
        for key, rk in ROW_KEYS.items():
            if sized.get(key) is not None:
                row["metadata"][rk] = sized[key]
            elif rk not in row["metadata"] and rk in old_md:
                row["metadata"][rk] = old_md[rk]
        row["metadata"]["rev"] = n + 1
        os.rename(rewrite.package, target)
        rows[tid] = json.dumps(row, ensure_ascii=False)
        folded.append((h, n + 1, {rk: row["metadata"].get(rk) for rk in ROW_KEYS.values()}))
    if not folded:
        return None
    version, path = mix.publish([rows[iid] for iid in order])
    for h, to_rev, res in folded:
        meta, rewrite = h["meta"], h["rewrite"]
        meta.update({"status": "accepted", "result_rev": to_rev, "finished": layout.stamp()})
        layout.write_json_atomic(rewrite.meta, meta)
        h["status"] = "accepted"
        layout.append_jsonl(root.evolution.task(meta["task"]).lineage, {
            "stamp": layout.stamp(), "event": "fold", "from_rev": meta["input_rev"],
            "to_rev": to_rev, "mix_version": version,
            "rewrite": f"rewrites/{rewrite.path.name}",
        })
        log.info("fold %s: r%d -> r%d, %s (%s)", meta["task"], meta["input_rev"], to_rev,
                 " ".join(f"{k}={v}" for k, v in res.items()),
                 (meta.get("resources") or {}).get("source") or "inherited")
    log.info("published mix v%04d (%d rows, %d folded) -> %s",
             version, len(order), len(folded), path.name)
    return version


# --------------------------------------------------------------------------
# What the loop rebuilds from its files
# --------------------------------------------------------------------------

def _rewrite_metas(root: layout.Root):
    for task in root.evolution.task_dirs():
        for rw in task.rewrite_dirs():
            try:
                meta = json.loads(rw.meta.read_text())
            except (OSError, ValueError):
                continue
            if meta.get("dry"):
                continue
            yield task, rw, meta


def operator_history(root: layout.Root) -> tuple[dict, dict]:
    """(used_ops, used_fams) over every accepted rewrite.

    What the diversity terms D(f) and P(o) need is the pool's current
    composition, not a log of past calls. Each accepted rewrite records the
    operator it was made with, so the distribution is read back off disk
    rather than tracked in parallel -- which also means it survives a
    restart: rescan and the counts are exactly what they were.
    """
    fam_of = {op: fam for fam, members in ops.OPERATORS.items() for op in members}
    used_ops: dict[str, int] = {}
    used_fams: dict[str, int] = {}
    for _task, _rw, meta in _rewrite_metas(root):
        op = meta.get("operator")
        if meta.get("status") != "accepted" or not op:
            continue
        used_ops[op] = used_ops.get(op, 0) + 1
        fam = fam_of.get(op)
        if fam:
            used_fams[fam] = used_fams.get(fam, 0) + 1
    return used_ops, used_fams


def rebuild_status(root: layout.Root) -> dict:
    """status.json from the ledger and every task's rewrite files. No counter
    is carried over; losing the file loses nothing."""
    ledger = load_ledger(root)
    by_outcome: dict[str, int] = {}
    for line in ledger.values():
        by_outcome[line.get("outcome", "?")] = by_outcome.get(line.get("outcome", "?"), 0) + 1
    rewrites = {"running": 0, "accepted": 0, "blocked": 0, "failed": 0, "kept": 0}
    rejected: dict[str, int] = {}
    for _task, _rw, meta in _rewrite_metas(root):
        st = meta.get("status")
        if st == "rejected":
            stage = meta.get("stage") or "unknown"
            rejected[stage] = rejected.get(stage, 0) + 1
        elif st == "interrupted":
            rewrites["failed"] += 1
        elif st in rewrites:
            rewrites[st] += 1
    live = root.mix.live_version()
    status = {
        "updated": layout.stamp(),
        "mix_version": live[0] if live else None,
        "pending": len(_pending(root, ledger)),
        "handled": by_outcome.get("handled", 0),
        "deferred": by_outcome.get("deferred", 0),
        "junk": by_outcome.get("junk", 0),
        "superseded": by_outcome.get("superseded", 0),
        "rewrites_running": rewrites["running"],
        "accepted": rewrites["accepted"],
        "rejected": rejected,
        "blocked": rewrites["blocked"],
        "failed": rewrites["failed"],
        "kept": rewrites["kept"],
    }
    layout.write_json_atomic(root.evolution.status, status)
    return status


def _snapshot_lineage(root: layout.Root, note: str) -> None:
    """Commit the loop's records so the history of every task survives.

    The loop is the research object, and analysing it needs the lineage
    (r0 -> r1 -> r2 ...) with the verdicts that produced it. A git repository
    whose metadata sits in evolution/.git and whose work tree is the root
    keeps every round as a commit of the ledger, status.json, every task's
    lineage.jsonl and rewrite.json, and the mix manifests -- and nothing
    else: packages, sessions and traces hold verifiers, solutions and
    transcripts, and the paths are named one by one rather than `add -A`ed.
    Failures never break the round -- versioning is an audit trail, not a
    dependency.
    """
    git_dir = root.evolution.path / ".git"
    base = ["git", f"--git-dir={git_dir}", f"--work-tree={root.path}"]
    # cwd is the work tree, so the pathspecs below are relative to its top.
    run = dict(cwd=str(root.path), check=True, capture_output=True, text=True)
    try:
        if not git_dir.exists():
            subprocess.run(base + ["init", "-q"], **run)
            subprocess.run(base + ["config", "core.worktree", str(root.path)], **run)
        ev = root.evolution.path
        paths = [ev / "ledger.jsonl", ev / "status.json"]
        paths += ev.glob("tasks/*/lineage.jsonl")
        paths += ev.glob("tasks/*/rewrites/*/rewrite.json")
        paths += root.mix.history.glob("*.manifest.json")
        spec = "\n".join(str(p.relative_to(root.path)) for p in paths if p.exists()) + "\n"
        subprocess.run(base + ["add", "--pathspec-from-file=-"], input=spec, **run)
        r = subprocess.run(
            base + ["-c", "user.email=evolve@terminal-rl", "-c", "user.name=evolve-loop",
                    "commit", "-q", "-m", note],
            cwd=str(root.path), capture_output=True, text=True)
        if r.returncode == 0:
            log.info("lineage snapshot committed: %s", note)
    except Exception as e:  # noqa: BLE001 -- never fail a round on archiving
        log.warning("lineage snapshot failed: %s", e)


# --------------------------------------------------------------------------
# A round
# --------------------------------------------------------------------------

def _replay_signal(root: layout.Root, sid: str) -> Signal:
    """The one signal `--signal` names, ledger or no ledger."""
    run_name, _, stem = sid.partition("/")
    run = root.run(run_name)
    path = run.signals / f"{stem}.json"
    if not path.exists():
        raise SystemExit(f"no signal file at {path}")
    data, junk = read_signal(path)
    if data is None:
        raise SystemExit(f"{sid}: {junk or 'does not parse'}")
    return Signal(run, path, sid, data, None)


def run_round(root: layout.Root, *, only: str | None = None, limit: int | None = None,
              workers: int = 8, dry: bool = False, signal: str | None = None) -> dict:
    ledger = load_ledger(root)
    # A replay is inspection: it must not fold a rewrite of a revision the
    # task has moved past, or close a signal the ledger already closed.
    dry = dry or bool(signal)
    result: dict = {"handled": 0, "accepted": 0, "deferred": 0, "junk": 0,
                    "superseded": 0, "counts": {}, "mix_version": None}
    if signal:
        picks, rest = [_replay_signal(root, signal)], []
        found: list[Signal] = []
    else:
        found = discover(root, ledger)
        if only:
            found = [s for s in found if s.task == only]
        for s in found:
            if s.junk:
                log.warning("junk signal %s: %s", s.sid, s.junk)
                result["junk"] += 1
                if not dry:
                    _ledger_line(root, s, "junk", reason=s.junk)
        picks, rest = choose(root, [s for s in found if s.data is not None])
    if not picks and not rest:
        return {**result, "reason": "no signals"}

    todo, deferred = [], []
    for s in picks:
        if s.data["direction"] == "easier" and not SIMPLIFY_ENABLED and not signal:
            deferred.append(s)
        else:
            todo.append(s)
    if limit:
        todo = todo[:limit]
    closing = {s.sid for s in todo} | {s.sid for s in deferred}
    for s in deferred:
        log.info("%s %s deferred: the easier direction is off", s.task, s.sid)
        result["deferred"] += 1
        if not dry:
            _ledger_line(root, s, "deferred")
    for s, why, pick in rest:
        # A signal yielding to a pick this round did not reach (--limit)
        # stays pending; one about a gone revision is closed now.
        if pick is not None and pick.sid not in closing:
            continue
        log.info("%s %s superseded: %s", s.task, s.sid, why)
        result["superseded"] += 1
        if not dry:
            _ledger_line(root, s, "superseded", reason=why)
    if not todo:
        return result

    # What box training gives each task, from the mix the trainer reads, and
    # which axes the accepted rewrites already used. Read once per round: the
    # mix is 12 MB on GPFS and the rewrite records are one small file each.
    declared = declared_resources(root.mix.live)
    history = operator_history(root)
    handled: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(handle, root, s, declared=declared, history=history, dry=dry): s
                for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                h = fut.result()
            except NoSeed as e:
                log.warning("junk signal %s: %s", s.sid, e)
                result["junk"] += 1
                if not dry:
                    _ledger_line(root, s, "junk", reason=str(e)[:200])
                continue
            except Exception as e:  # noqa: BLE001 -- one signal, not the round
                log.exception("%s: handling failed before a rewrite existed: %s", s.sid, e)
                continue
            meta = h["meta"]
            log.info("%s %s r%s %s %s/%s -> %s (%s%s) %s", meta["task"], s.sid,
                     meta["input_rev"], meta["job"], s.data.get("solved"),
                     s.data.get("total"), h["status"], meta.get("stage") or "-",
                     f": {meta['reason'][:200]}" if meta.get("reason") else "",
                     _rewrite_ref(root, h["rewrite"]))
            handled.append(h)
            if h["status"] != "accepted":
                _close(root, h, dry=dry)

    if not dry:
        # fold() settles each of these one way or the other (accepted with a
        # revision, or rejected/failed at the fold); closing them afterwards
        # is what makes the ledger line the last thing written.
        to_fold = [h for h in handled if h["status"] == "accepted"]
        result["mix_version"] = fold(root, to_fold)
        for h in to_fold:
            _close(root, h, dry=dry)
    for h in handled:
        result["counts"][h["status"]] = result["counts"].get(h["status"], 0) + 1
    result["handled"] = len(handled)
    result["accepted"] = result["counts"].get("accepted", 0)
    return result


def _wait_for_signals(root: layout.Root, max_wait: int, tick: float = 5.0) -> None:
    """Sleep until a pending signal shows up, or `max_wait` seconds pass.

    Training writes signals in bursts and then goes quiet for a whole step,
    so a flat sleep made a signal written a second after a round wait the
    better part of two minutes for nothing. Watch instead and return as soon
    as something lands; the timeout is only there so a round still happens
    if a signal is ever written without us noticing.
    """
    ledger = load_ledger(root)
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if _pending(root, ledger):
            return
        time.sleep(tick)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one round then exit")
    ap.add_argument("--interval", type=int, default=120,
                    help="seconds between rounds when looping")
    ap.add_argument("--only", help="handle only this task's signals (dev)")
    ap.add_argument("--limit", type=int, help="cap tasks handled per round (dev)")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent rewrites per round")
    ap.add_argument("--dry", action="store_true",
                    help="handle, but publish no mix version and write no ledger or "
                         "lineage line; everything lands under the rewrite directory. "
                         "Implies --once")
    ap.add_argument("--signal", metavar="RUN/TASK--gN",
                    help="replay this signal whether or not the ledger closed it; "
                         "implies --dry")
    args = ap.parse_args()
    root = layout.Root.from_env()
    # Before the log file is even opened: a refused second instance must not
    # write "loop up" into the log the first one owns.
    _lock_fd = acquire_singleton(root.evolution.loop_lock)  # noqa: F841 -- held for the process lifetime

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        # FileHandler only. The unit launches the loop with `>> loop.log 2>&1`
        # for stray prints and tracebacks; a StreamHandler here would write
        # every record to the same file a second time.
        handlers=[logging.FileHandler(root.evolution.loop_log)])
    # A row declaring no daytona_* is boxed at this size, here and (if the
    # trainer's env agrees) in training. None means the harness default 2/4/6,
    # which is not what the trainer runs at unless its env says so too.
    log.info("fleet default for rows declaring no daytona_*: cpu=%s mem_gb=%s "
             "disk_gb=%s (from TT_DAYTONA_CPU/MEM_GB/DISK_GB; None = harness "
             "default 2/4/6)", FLEET["cpu"], FLEET["mem_gb"], FLEET["disk_gb"])

    dry = args.dry or bool(args.signal)
    if args.once or args.only or dry:
        r = run_round(root, only=args.only, limit=args.limit, workers=args.workers,
                      dry=dry, signal=args.signal)
        log.info("round done%s: %s", " (dry)" if dry else "", r)
        if not dry:
            rebuild_status(root)
            if r.get("handled"):
                _snapshot_lineage(root, f"once: {r}")
        return
    log.info("evolve loop up: pid=%d root=%s interval=%ds workers=%d simplify=%s",
             os.getpid(), root.path, args.interval, args.workers, SIMPLIFY_ENABLED)
    while True:
        try:
            r = run_round(root, workers=args.workers)
            if r.get("handled") or r.get("junk") or r.get("deferred") or r.get("superseded"):
                log.info("round: %s", r)
            rebuild_status(root)
            if r.get("handled"):
                _snapshot_lineage(root, f"round: {r}")
        except Exception as e:  # noqa: BLE001
            log.exception("round failed: %s", e)
        _wait_for_signals(root, args.interval)


if __name__ == "__main__":
    main()
