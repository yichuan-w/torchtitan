#!/usr/bin/env python3
"""Same-machine evolution round: signals -> re-tuned packages -> training mix.

The training run (della-tridao GPUs) writes one evolution signal per
zero-variance group to SIGNALS_DIR: a task the model solved 0/k (too hard, make
it easier) or k/k (too easy, make it harder). This reads those signals, re-tunes
each task the feedback way, and folds the results back into the live data_path
that TMaxDataset hot-reloads. No laptop, no flaminio, no VPN hop -- the loop runs
on the same machine as the training that feeds it.

Two things make that possible on a box with no docker (della has udocker/
apptainer only):
  * the bulk of the signal is SWE (Turing Labs) 0/k -> simplify, which only
    rewrites the instruction, so feedback_loop's instruction-only fast path
    re-validates by auditing instruction<->verifier drift instead of building;
  * each signal's source package is resolved from whichever corpus holds it
    (swe-extract or tw-extract), so both SWE and TW tasks evolve.

A round:
  1. scan SIGNALS_DIR for signals not already consumed
  2. per signal: resolve the source package, run feedback_loop.process_one
     (0/k easier, k/k harder-or-kept, in-band kept), archive the signal
  3. fold every re-tuned package into the data_path with pack_to_dataset,
     writing a new file and swapping it in atomically so a half-written mix is
     never visible to the hot reload. The folded row is provisioned at
     max(what the seed had, what the reference solution measured in the
     agent's container) -- a measurement, never the agent's estimate

Observable (per-signal log line), resumable (consumed signals are archived and
skipped; re-tuned packages are keyed by task id and overwrite cleanly),
reproducible (the signal that drove each decision is archived beside the run).

Usage:
  # one round against the live mix, atomic swap:
  evolve_ondella.py --once
  # continuous, one round every 120s:
  evolve_ondella.py --interval 120
  # dry test: one named signal, write mix to a copy, live mix untouched:
  evolve_ondella.py --once --only <task_id> --mix-out /tmp/mix_test.jsonl --keep-signal
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import shutil
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feedback_loop as fb   # noqa: E402
import pack_to_dataset as pack  # noqa: E402

BASE = Path(os.environ.get("TRL_BASE", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
SIGNALS = Path(os.environ.get("SWE_TASK_EVOLUTION_DIR", str(BASE / "evolution/signals")))
EVOLUTION_ROOT = SIGNALS.parent
CONSUMED = EVOLUTION_ROOT / "consumed"
OUT_ROOT = EVOLUTION_ROOT / "retuned"
# The package the mix currently serves for a task, kept as the next rewrite's
# starting point. See parent_src.
PARENTS = EVOLUTION_ROOT / "parents"
MIX = Path(os.environ.get("SWE_PROMPT_DATA", str(BASE / "data/mix/mix_live.jsonl")))
STATS = Path(os.environ.get(
    "SWE_EVOLUTION_STATS",
    str(EVOLUTION_ROOT / "evolution_stats.json"),
))
LINEAGE = Path(os.environ.get(
    "SWE_EVOLUTION_LINEAGE",
    str(EVOLUTION_ROOT / "evolution_lineage.jsonl"),
))
LOCK = EVOLUTION_ROOT / "evolve_ondella.lock"
# The 0/k -> easier branch is switchable, because the ratchet it drives only
# turns one way. A simplify is accepted almost every time -- the revalidation
# asks whether solve.sh still passes, and rewriting the instruction cannot break
# solve.sh -- while an evolve has to survive a rebuilt verifier. Measured on
# this corpus: 693 accepted simplifies against 335 accepted evolves in a week,
# and
# 814 against 26 in an earlier window, with the on-mix solve rate climbing while
# the fixed eval stayed flat. Off, the too-hard tail freezes instead of being
# loosened, and the only signals that move a task are the ones asking for more
# difficulty.
SIMPLIFY_ENABLED = os.environ.get("SWE_EVOLVE_SIMPLIFY", "1").lower() not in (
    "0", "false", "no")
POOL_ROOTS = [BASE / "data/swe-extract/tasks", BASE / "data/tw-extract/tasks",
              BASE / "data/tmax-extract/tasks"]

log = logging.getLogger("evolve_ondella")


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


def _read_provision(pkg: Path) -> dict:
    """The size feedback_loop left beside the package, or {} if none."""
    try:
        return json.loads((pkg / ".resources.json").read_text())
    except (OSError, ValueError):
        return {}


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


def acquire_singleton(lock_path: Path = LOCK) -> int:
    """Hold the one-loop-per-evolution-root lock for the life of this process.

    The loop is a singleton by contract -- two instances over one signals
    directory race for the same signal files, send the same k/k task to Codex
    twice, write the same retuned/<tid>/ concurrently and fold over each
    other's mix -- but nothing enforced it. There are five ways to launch it
    (restart_evolve.sh, launch_evolveloop.sh, the training launcher, a
    hand-typed command, a per-workdir systemd script); only one stops the
    previous instance first, and it stopped the first pgrep match only.
    Measured 2026-09-02: nine launches in one day, two instances alive at once
    for 27 minutes. Guarding every launcher is whack-a-mole; the process
    guards itself.

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

    Test modes (--once, --only) take it too: a hand round over a live loop's
    signals dir collides the same way. The returned fd must stay open.
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


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _content_revision(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _trace_references(paths: list[str]) -> list[str]:
    """Return trace paths relative to the evolution artifact root when possible."""
    refs = []
    for value in paths:
        path = Path(value)
        try:
            refs.append(str(path.relative_to(EVOLUTION_ROOT)))
        except ValueError:
            refs.append(str(path))
    return refs


def append_lineage_event(
    event: str,
    *,
    path: Path | None = None,
    event_time_unix_ns: int | None = None,
    **fields,
) -> None:
    """Append one durable evolution event without rewriting prior rounds."""
    path = path or LINEAGE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "record_type": "evolution_event",
        "event": event,
        "time_unix_ns": event_time_unix_ns or time.time_ns(),
        **fields,
    }
    with path.open("a") as f:
        f.write(_canonical_json(record) + "\n")
        f.flush()


def update_evolution_stats(
    result: dict,
    *,
    stats_path: Path = STATS,
    signals_dir: Path = SIGNALS,
    deferred_dir: Path | None = None,
) -> dict:
    """Persist cumulative loop counters for the trainer's W&B metrics.

    The trainer already reads ``evolution_stats.json`` every train step. The
    producer previously never wrote that file, so the documented evolution
    metrics were silently absent from W&B. This writer is atomic, survives loop
    restarts, and preserves a corrupt prior file before rebuilding the counters.
    """
    deferred_dir = deferred_dir or EVOLUTION_ROOT / "deferred_easier"
    stats = {
        "schema_version": 1,
        "started_at": time.time(),
        "updated_at": None,
        "rounds": 0,
        "processed": 0,
        "retuned": 0,
        "folded": 0,
        "pending": 0,
        "deferred_easier": 0,
        "counts": {},
    }
    if stats_path.exists():
        try:
            loaded = json.loads(stats_path.read_text())
            if isinstance(loaded, dict):
                stats.update(loaded)
        except (OSError, ValueError) as e:
            backup = stats_path.with_name(
                f"{stats_path.name}.corrupt-{int(time.time())}"
            )
            shutil.copy2(stats_path, backup)
            log.warning(
                "invalid evolution stats %s: %s; backed up to %s",
                stats_path, e, backup,
            )

    if result.get("processed"):
        stats["rounds"] = int(stats.get("rounds", 0)) + 1
        for key in ("processed", "retuned", "folded"):
            stats[key] = int(stats.get(key, 0)) + int(result.get(key, 0))
        counts = dict(stats.get("counts", {}) or {})
        for key, value in (result.get("counts", {}) or {}).items():
            counts[key] = int(counts.get(key, 0)) + int(value)
        stats["counts"] = counts

    stats["pending"] = len(list(signals_dir.glob("*.json")))
    stats["deferred_easier"] = (
        len(list(deferred_dir.glob("*.json"))) if deferred_dir.is_dir() else 0
    )
    stats["updated_at"] = time.time()
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    incoming = stats_path.with_suffix(stats_path.suffix + ".incoming")
    incoming.write_text(json.dumps(stats, sort_keys=True) + "\n")
    os.replace(incoming, stats_path)
    return stats


def resolve_src(tid: str) -> Path | None:
    """The seed package for a task id, from whichever corpus carries it."""
    for root in POOL_ROOTS:
        d = root / tid
        if (d / "instruction.md").exists():
            return d
    return None


def mix_revisions(mix: Path) -> dict[str, str]:
    """The revision of every row as the mix now stands, keyed by instance id."""
    out: dict[str, str] = {}
    if not mix.exists():
        return out
    for ln in open(mix):
        if not ln.strip():
            continue
        row = json.loads(ln)
        iid = (row.get("metadata") or {}).get("instance_id")
        if iid:
            out[iid] = _content_revision(row)
    return out


def parent_src(tid: str, revisions: dict[str, str] | None) -> Path | None:
    """The version the mix is serving right now, when this loop wrote it.

    A task is rewritten every time it comes back with no spread, and each
    rewrite is one rung above what it started from. Starting from the seed
    every time rebuilds rung one forever: the task that needs three rungs
    never gets past the first, and training keeps paying 16 rollouts to be
    told again that it is too easy. So the parent is the last accepted child,
    and only the seed when there is none -- the same rule the BenchEvolver
    paper states as `parent = Last(lineage) if lineage else seed`.

    The recorded revision is what makes this safe. It is the row this package
    produced when it was folded; if the live mix no longer carries that row --
    the mix was rebuilt, the family was dropped, a later fold of ours was
    rejected -- the parent is stale and the seed is the honest starting point.
    """
    pkg = PARENTS / tid
    try:
        meta = json.loads((PARENTS / f"{tid}.json").read_text())
    except (OSError, ValueError):
        return None
    if not (pkg / "instruction.md").exists():
        return None
    if meta.get("sample_revision") != (revisions or {}).get(tid):
        return None
    return pkg


def record_parent(tid: str, revision: str) -> int:
    """Keep the just-folded package as the next rewrite's starting point, and
    return the rung it now sits at. Written whole then swapped in, so a crash
    mid-copy cannot leave a half package that a later round would evolve."""
    meta_path = PARENTS / f"{tid}.json"
    rung = 0
    try:
        rung = int(json.loads(meta_path.read_text()).get("rung", 0))
    except (OSError, ValueError):
        pass
    PARENTS.mkdir(parents=True, exist_ok=True)
    dest, incoming = PARENTS / tid, PARENTS / f".{tid}.incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.copytree(OUT_ROOT / tid, incoming)
    if dest.exists():
        stale = PARENTS / f".{tid}.stale"
        shutil.rmtree(stale, ignore_errors=True)
        os.rename(dest, stale)
        os.rename(incoming, dest)
        shutil.rmtree(stale, ignore_errors=True)
    else:
        os.rename(incoming, dest)
    meta_path.write_text(json.dumps(
        {"sample_revision": revision, "rung": rung + 1,
         "folded_time_unix_ns": time.time_ns()}, sort_keys=True) + "\n")
    return rung + 1


def signal_to_rollout(sig: dict) -> dict:
    """A signal is {task_id, solved, total, direction, attempts}; feedback_loop
    wants {task_id, solved, graded, attempts}. Every emitted attempt is graded
    (the signal is emitted from a zero-variance group), so graded == total."""
    total = sig.get("total", len(sig.get("attempts", []) or []))
    return {"task_id": sig["task_id"], "solved": sig.get("solved", 0),
            "graded": total, "attempts": sig.get("attempts", []) or [],
            "_source": "signal"}


def _handle(sp: Path, declared: dict[str, dict] | None = None,
            revisions: dict[str, str] | None = None):
    """Resolve one signal to (record, task_id, was_retuned) or None to skip.

    `declared` is the mix's per-row daytona_* (declared_resources), read once
    per round; it says what box training gives each task."""
    try:
        sig = json.loads(sp.read_text())
    except Exception as e:  # noqa: BLE001
        # A signal that never parses must not stay in signals/: any *.json
        # there makes _wait_for_signals return immediately, so one corrupt
        # file (e.g. a 0-byte leftover from a killed producer) turns the
        # loop into a busy-spin that floods the log. Give a fresh file one
        # grace window in case the producer is still mid-write, then
        # quarantine it like any other junk signal.
        try:
            stale = time.time() - sp.stat().st_mtime > 60
        except OSError:
            return None  # consumed by someone else meanwhile
        if stale:
            junk = EVOLUTION_ROOT / "junk"
            junk.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sp), str(junk / sp.name))
            log.warning("bad signal %s: %s -> quarantined", sp.name, e)
        else:
            log.warning("bad signal %s: %s (fresh, retrying)", sp.name, e)
        return None
    tid = sig.get("task_id", sp.stem)
    source = {
        "signal_file": sp.name,
        "signal_created_time_unix_ns": sig.get("created_time_unix_ns"),
        "source_group_id": sig.get("source_group_id"),
        "source_lineage": sig.get("source_lineage"),
    }
    # Consumer-side twin of the rollouter's zero-turn guard (defense in depth;
    # signals may come from a producer predating that guard): an all-fail group
    # in which no attempt took a turn measured the infrastructure, not the
    # task. Quarantine it rather than let it drive an unearned simplify.
    att = sig.get("attempts") or []
    if sig.get("solved", 0) == 0 and att and             all(not a.get("turns") for a in att):
        junk = EVOLUTION_ROOT / "junk"
        junk.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sp), str(junk / sp.name))
        log.warning("junk signal %s: all-fail with zero turns -> quarantined",
                    tid)
        return {"tid": tid, "status": "junk_infra", "retuned": False,
                "action": "-", "solved": 0, "graded": sig.get("total"),
                **source}
    # Deferred, not discarded: the signal moves to its own directory rather than
    # consumed/, so turning the branch back on replays the backlog instead of
    # starting from whatever the trainer happens to emit next.
    if sig.get("solved", 0) == 0 and not SIMPLIFY_ENABLED:
        held = EVOLUTION_ROOT / "deferred_easier"
        held.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sp), str(held / sp.name))
        return {"tid": tid, "status": "deferred_easier", "retuned": False,
                "action": "-", "solved": 0, "graded": sig.get("total"),
                **source}
    # One rung above what training is running right now, not above the seed.
    src = parent_src(tid, revisions) or resolve_src(tid)
    if src is None:
        return {"tid": tid, "status": "no_pool_dir", "retuned": False,
                "action": "-", "solved": sig.get("solved"), "graded": sig.get("total"),
                **source}
    from_parent = src.parent == PARENTS
    rec = fb.process_one(signal_to_rollout(sig), src, OUT_ROOT,
                         resources=training_box(tid, declared))
    st = rec.get("status", "?")
    if st not in ("ok", "kept") and rec.get("why"):
        log.warning("%s %s: %s", tid, st, str(rec["why"])[:300])
    elif st == "kept" and rec.get("why"):
        # A codex `kept` is the agent reading the package and declining the axis
        # it was given -- a real answer, not an error. But it left no trace at
        # all, so a run where every k/k signal came back kept read exactly like
        # one where the agent never ran (measured 2026-09-02: 5 of 5 kept, and
        # the only way to tell them apart was to catch a /tmp workdir before it
        # was cleaned up). The verdict text says which axis did not fit.
        log.info("%s kept: %s", tid, str(rec["why"])[:300])
    return {"tid": tid, "status": st, "action": rec.get("action", "-"),
            "solved": rec.get("solved"), "graded": rec.get("graded"),
            "fast": rec.get("revalidate", {}).get("fast_path", ""),
            "hint": rec.get("hint"),
            "codex_trace_dirs": _trace_references(rec.get("codex_trace_dirs", [])),
            "resources": rec.get("resources"),
            "from_parent": from_parent,
            "retuned": st in ("ok", "kept"), **source}


def _signal_task_key(path: Path) -> str:
    """Group valid signals by task; leave malformed files independently handled."""
    try:
        return str(json.loads(path.read_text()).get("task_id") or path.name)
    except Exception:  # noqa: BLE001 - _handle performs quarantine and logging
        return path.name


def _signal_time_key(path: Path) -> tuple[int, str]:
    """Order repeated task signals by creation time, then filename."""
    try:
        value = json.loads(path.read_text()).get("created_time_unix_ns", 0)
        return int(value or 0), path.name
    except Exception:  # noqa: BLE001 - malformed files retain deterministic order
        return 0, path.name


def _handle_task_signals(paths: list[Path], declared: dict[str, dict] | None = None,
                         revisions: dict[str, str] | None = None,
                         ) -> list[tuple[Path, dict | None]]:
    """Process repeated occurrences of one task serially to avoid output races."""
    results = []
    for path in sorted(paths, key=_signal_time_key):
        received_time = time.time_ns()
        result = _handle(path, declared, revisions)
        if result is not None:
            result["signal_received_time_unix_ns"] = received_time
            result["retune_finished_time_unix_ns"] = time.time_ns()
        results.append((path, result))
    return results


def run_round(only: str | None = None, mix_out: Path | None = None,
              keep_signal: bool = False, limit: int | None = None,
              workers: int = 8) -> dict:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    CONSUMED.mkdir(parents=True, exist_ok=True)
    sig_files = sorted(SIGNALS.glob("*.json"))
    if only:
        safe_only = only.replace("/", "_")
        sig_files = [
            p
            for p in sig_files
            if p.stem == safe_only or p.stem.startswith(f"{safe_only}--")
        ]
    if limit:
        sig_files = sig_files[:limit]
    if not sig_files:
        return {"processed": 0, "retuned": 0, "reason": "no signals"}

    # Different tasks are independent and run concurrently. Repeated occurrences
    # of one task run serially because they share one retuned/<task_id> output
    # directory; concurrent writes there corrupt the package. Signals are archived
    # only after processing, so a crash mid-round leaves them resumable.
    counts: dict[str, int] = {}
    retuned_ids: list[str] = []
    retuned_sources: dict[str, dict] = {}
    signals_by_task: dict[str, list[Path]] = {}
    for signal_path in sig_files:
        signals_by_task.setdefault(_signal_task_key(signal_path), []).append(signal_path)
    # What box training gives each task, from the mix the trainer reads. Read
    # once here rather than per signal: the mix is 12 MB on GPFS.
    declared = declared_resources(MIX)
    # Which row the mix carries for each task, so a rewrite starts from the
    # version training is running rather than from the seed.
    revisions = mix_revisions(MIX)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(_handle_task_signals, paths, declared, revisions): task_id
            for task_id, paths in signals_by_task.items()
        }
        for fut in as_completed(futs):
            for sp, r in fut.result():
                if r is None:
                    continue
                counts[r["status"]] = counts.get(r["status"], 0) + 1
                source_lineage = r.get("source_lineage") or {}
                lineage_fields = {**source_lineage, "task_id": r["tid"]}
                append_lineage_event(
                    "signal_received",
                    event_time_unix_ns=r.get("signal_received_time_unix_ns"),
                    signal_file=r.get("signal_file"),
                    signal_created_time_unix_ns=r.get("signal_created_time_unix_ns"),
                    source_group_id=r.get("source_group_id"),
                    **lineage_fields,
                )
                append_lineage_event(
                    "retune_finished",
                    event_time_unix_ns=r.get("retune_finished_time_unix_ns"),
                    status=r["status"],
                    action=r.get("action"),
                    solved=r.get("solved"),
                    total=r.get("graded"),
                    signal_file=r.get("signal_file"),
                    codex_trace_dirs=r.get("codex_trace_dirs", []),
                    resources=r.get("resources"),
                    from_parent=r.get("from_parent"),
                    **lineage_fields,
                )
                # `from` is the ladder: `parent` means this rewrite built on the
                # version training is running, `seed` that it started over.
                log.info("%s solved=%s/%s -> %s (%s%s%s, from=%s)", r["tid"], r["solved"],
                         r["graded"], r["action"], r["status"],
                         f", {r['fast']}" if r.get("fast") else "",
                         f", arm={r['hint']}" if r.get("hint") else "",
                         "parent" if r.get("from_parent") else "seed")
                if r["retuned"]:
                    if r["tid"] not in retuned_ids:
                        retuned_ids.append(r["tid"])
                    retuned_sources[r["tid"]] = {
                        "source_occurrence_id": source_lineage.get("occurrence_id"),
                        "source_sample_revision": source_lineage.get("sample_revision"),
                        "source_mix_revision": source_lineage.get("mix_revision"),
                        "source_group_id": r.get("source_group_id"),
                    }
                if not keep_signal and sp.exists():
                    # _handle may have already routed the file (junk quarantine)
                    shutil.move(str(sp), str(CONSUMED / sp.name))

    # Fold this round's re-tuned packages into the data_path: read the base,
    # replace the row for each re-tuned id (pack.to_row rebuilds it from the
    # package), keep every other row and the order, then swap the file in
    # atomically so a half-written mix is never visible to the hot reload. Only
    # the ids re-tuned this round move -- a package that failed revalidation was
    # never added to retuned_ids, so its unbuilt rewrite cannot leak in. Each
    # row is rebuilt under its own guard so one malformed package is skipped, not
    # fatal to the whole fold (the silent-crash mode of the argv/main() path).
    folded = 0
    folded_records: list[dict] = []
    if retuned_ids:
        target = mix_out or MIX
        rows: dict[str, str] = {}
        order: list[str] = []
        for ln in open(MIX):
            if ln.strip():
                iid = json.loads(ln)["metadata"]["instance_id"]
                rows[iid] = ln if ln.endswith("\n") else ln + "\n"
                order.append(iid)
        for tid in retuned_ids:
            try:
                row = pack.to_row(str(OUT_ROOT / tid))
            except Exception as e:  # noqa: BLE001
                log.warning("fold skip %s: %s", tid, e)
                continue
            if row["label"] not in rows:
                # The task is no longer in the mix, so it was taken out
                # deliberately (a whole family dropped, a broken package purged).
                # Re-adding it would undo that, and because a new label lands at
                # the END of the file it would also shift the last holdout_n rows
                # -- rotating a task out of the held-out eval slice and another
                # in, which silently invalidates every before/after comparison
                # anchored on it. Nine SWE rows re-entered this way within an hour
                # of the family being dropped. Replace only.
                log.warning("fold skip %s: no longer in the mix", tid)
                continue
            # Per-sandbox resources do not live in the package. prepare_rts_data
            # takes them through _to_row(resources=...), which the mix build
            # filled from measured_resources.csv; pack.to_row has no source for
            # them, so a folded row arrives with the three keys absent and the
            # sandbox falls back to the TT_DAYTONA_* fleet defaults. On this
            # corpus that is 1 CPU against a measured 2, so a retuned task runs
            # on half the cores it was provisioned for, hits
            # SWE_TIME_BUDGET_SEC, and the timeout reads back to this loop as
            # "too hard" -- a task made easier because it was starved.
            #
            # feedback_loop leaves .resources.json beside the package: max(what
            # the seed had, what the reference solution measured in the
            # agent's container), so a harder version that outgrew the seed's
            # box is provisioned for what it needs and one that did not keeps
            # the seed's size. Without that file (no measurement, a package
            # from before there was one) the replaced row's values carry
            # across: a retune edits the instruction, tests, solution and
            # Dockerfile, never the provisioning the task was measured at.
            old_md = json.loads(rows[row["label"]])["metadata"]
            sized = _read_provision(OUT_ROOT / tid)
            for key, rk in ROW_KEYS.items():
                if sized.get(key) is not None:
                    row["metadata"][rk] = sized[key]
                elif rk not in row["metadata"] and rk in old_md:
                    row["metadata"][rk] = old_md[rk]
            res = {rk: row["metadata"].get(rk) for rk in ROW_KEYS.values()}
            res_source = sized.get("source") or "inherited"
            rows[row["label"]] = json.dumps(row) + "\n"
            revision = _content_revision(row)
            # Keep it as the next rewrite's starting point, so the task climbs
            # instead of being rebuilt from the seed on its next signal.
            try:
                rung = record_parent(tid, revision)
            except Exception as e:  # noqa: BLE001 -- a fold must not fail on this
                log.warning("fold %s: could not record the parent: %s", tid, e)
                rung = None
            log.info("fold %s: rung %s, %s (%s)", tid, rung,
                     " ".join(f"{k}={v}" for k, v in res.items()), res_source)
            folded += 1
            folded_records.append(
                {
                    "task_id": tid,
                    "sample_revision": revision,
                    "rung": rung,
                    "resources": {**res, "source": res_source},
                    **retuned_sources.get(tid, {}),
                }
            )
        tmp = target.with_suffix(target.suffix + ".incoming")
        with open(tmp, "w") as f:
            for iid in order:
                f.write(rows[iid])
        os.replace(tmp, target)          # atomic on the same filesystem
        mix_revision = _content_revision(
            [_canonical_json(json.loads(rows[iid])) for iid in order]
        )
        for record in folded_records:
            append_lineage_event(
                "folded",
                **record,
                mix_revision=mix_revision,
                mix_file=target.name,
            )
        log.info("folded %d re-tuned tasks -> %s (%d rows)",
                 folded, target, len(order))

    return {"processed": len(sig_files), "retuned": len(retuned_ids),
            "folded": folded, "counts": counts}



def _wait_for_signals(max_wait: int, tick: float = 2.0) -> None:
    """Sleep until a signal shows up, or `max_wait` seconds pass.

    The loop used to sleep a flat 120 s between rounds, so a signal written a
    second after a round ended waited the better part of two minutes for no
    reason. There is nothing to poll for most of that time -- training writes
    signals in bursts and then goes quiet for a whole step. Watch the directory
    instead and return as soon as something lands; the timeout is only there so
    a round still happens if a signal is ever written without us noticing.
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if any(SIGNALS.glob("*.json")):
            return
        time.sleep(tick)



def _snapshot_lineage(note: str) -> None:
    """Commit the evolution state so every task version survives.

    The retuned pool is rewritten in place on re-evolve, which destroys the
    previous version of a task -- but the loop is the research object, and
    analysing it needs the full lineage (seed -> v1 -> v2 ...). A git repo at
    EVOLUTION_ROOT keeps every fold as a commit: the whole retuned/ tree plus a
    snapshot of the mix taken at the same moment. Failures never break the
    round -- versioning is an audit trail, not a dependency.
    """
    import subprocess
    root = EVOLUTION_ROOT
    try:
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        ignore = root / ".gitignore"
        entries = set(ignore.read_text().splitlines()) if ignore.exists() else set()
        entries.update({"signals/", "deferred_easier/", "codex_traces/"})
        ignore.write_text("\n".join(sorted(entries)) + "\n")
        shutil.copy2(MIX, root / "mix_snapshot.jsonl")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        r = subprocess.run(
            ["git", "-c", "user.email=evolve@terminal-rl", "-c", "user.name=evolve-loop",
             "commit", "-q", "-m", note],
            cwd=root, capture_output=True, text=True)
        if r.returncode == 0:
            log.info("lineage snapshot committed: %s", note)
    except Exception as e:  # noqa: BLE001 -- never fail a round on archiving
        log.warning("lineage snapshot failed: %s", e)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one round then exit")
    ap.add_argument("--interval", type=int, default=120,
                    help="seconds between rounds when looping")
    ap.add_argument("--only", help="process just this task id (test)")
    ap.add_argument("--mix-out", help="write the folded mix here instead of the "
                                       "live data_path (test; live mix untouched)")
    ap.add_argument("--keep-signal", action="store_true",
                    help="do not archive consumed signals (test)")
    ap.add_argument("--limit", type=int, help="cap signals per round (test)")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent re-tunes per round")
    ap.add_argument("--log", default=str(EVOLUTION_ROOT / "evolve_ondella.log"))
    args = ap.parse_args()
    # Before the log file is even opened: a refused second instance must not
    # write "loop up" into the log the first one owns.
    _lock_fd = acquire_singleton()  # noqa: F841 -- held for the process lifetime

    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        # FileHandler only. restart_evolve.sh launches the loop with
        # `>> $LOG 2>&1`, so a StreamHandler here writes every record to the
        # same file a second time -- the log doubles, and any `grep -c` over it
        # reads twice the real count. Stray library prints still reach the log
        # through that redirect; they just no longer arrive via two paths.
        handlers=[logging.FileHandler(args.log)])
    # A row declaring no daytona_* is boxed at this size, here and (if the
    # trainer's env agrees) in training. None means the harness default 2/4/6,
    # which is not what the trainer runs at unless its env says so too.
    log.info("fleet default for rows declaring no daytona_*: cpu=%s mem_gb=%s "
             "disk_gb=%s (from TT_DAYTONA_CPU/MEM_GB/DISK_GB; None = harness "
             "default 2/4/6)", FLEET["cpu"], FLEET["mem_gb"], FLEET["disk_gb"])

    mix_out = Path(args.mix_out) if args.mix_out else None
    if args.once or args.only:
        r = run_round(only=args.only, mix_out=mix_out,
                      keep_signal=args.keep_signal, limit=args.limit,
                      workers=args.workers)
        if mix_out is None:
            update_evolution_stats(r)
        log.info("round done: %s", r)
        if r.get("retuned") or r.get("folded"):
            _snapshot_lineage(f"once: {r}")
        return
    log.info("evolve_ondella loop up: pid=%d signals=%s mix=%s interval=%ds "
             "workers=%d", os.getpid(), SIGNALS, MIX, args.interval, args.workers)
    while True:
        try:
            r = run_round(workers=args.workers)
            update_evolution_stats(r)
            if r["processed"]:
                log.info("round: %s", r)
            if r.get("retuned") or r.get("folded"):
                _snapshot_lineage(f"round: {r}")
        except Exception as e:  # noqa: BLE001
            log.exception("round failed: %s", e)
        _wait_for_signals(args.interval)


if __name__ == "__main__":
    main()
