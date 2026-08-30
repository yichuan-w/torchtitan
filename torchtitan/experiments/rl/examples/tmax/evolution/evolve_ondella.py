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
     never visible to the hot reload

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
import json
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evolve as ev          # noqa: E402
import feedback_loop as fb   # noqa: E402
import pack_to_dataset as pack  # noqa: E402

BASE = Path(os.environ.get("TRL_BASE", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
SIGNALS = Path(os.environ.get("SWE_TASK_EVOLUTION_DIR", str(BASE / "evolution/signals")))
CONSUMED = BASE / "evolution/consumed"
OUT_ROOT = BASE / "evolution/retuned"
MIX = Path(os.environ.get("SWE_PROMPT_DATA", str(BASE / "data/mix/mix_live.jsonl")))
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
POOL_ROOTS = [BASE / "data/swe-extract/tasks", BASE / "data/tw-extract/tasks"]

log = logging.getLogger("evolve_ondella")


def resolve_src(tid: str) -> Path | None:
    """The source package for a task id, from whichever corpus carries it."""
    for root in POOL_ROOTS:
        d = root / tid
        if (d / "instruction.md").exists():
            return d
    return None


def signal_to_rollout(sig: dict) -> dict:
    """A signal is {task_id, solved, total, direction, attempts}; feedback_loop
    wants {task_id, solved, graded, attempts}. Every emitted attempt is graded
    (the signal is emitted from a zero-variance group), so graded == total."""
    total = sig.get("total", len(sig.get("attempts", []) or []))
    return {"task_id": sig["task_id"], "solved": sig.get("solved", 0),
            "graded": total, "attempts": sig.get("attempts", []) or [],
            "_source": "signal"}


def _handle(sp: Path):
    """Resolve one signal to (record, task_id, was_retuned) or None to skip."""
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
            junk = BASE / "evolution/junk"
            junk.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sp), str(junk / sp.name))
            log.warning("bad signal %s: %s -> quarantined", sp.name, e)
        else:
            log.warning("bad signal %s: %s (fresh, retrying)", sp.name, e)
        return None
    tid = sig.get("task_id", sp.stem)
    # Consumer-side twin of the rollouter's zero-turn guard (defense in depth;
    # signals may come from a producer predating that guard): an all-fail group
    # in which no attempt took a turn measured the infrastructure, not the
    # task. Quarantine it rather than let it drive an unearned simplify.
    att = sig.get("attempts") or []
    if sig.get("solved", 0) == 0 and att and             all(not a.get("turns") for a in att):
        junk = BASE / "evolution/junk"
        junk.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sp), str(junk / sp.name))
        log.warning("junk signal %s: all-fail with zero turns -> quarantined",
                    tid)
        return {"tid": tid, "status": "junk_infra", "retuned": False,
                "action": "-", "solved": 0, "graded": sig.get("total")}
    # Deferred, not discarded: the signal moves to its own directory rather than
    # consumed/, so turning the branch back on replays the backlog instead of
    # starting from whatever the trainer happens to emit next.
    if sig.get("solved", 0) == 0 and not SIMPLIFY_ENABLED:
        held = BASE / "evolution/deferred_easier"
        held.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sp), str(held / sp.name))
        return {"tid": tid, "status": "deferred_easier", "retuned": False,
                "action": "-", "solved": 0, "graded": sig.get("total")}
    src = resolve_src(tid)
    if src is None:
        return {"tid": tid, "status": "no_pool_dir", "retuned": False,
                "action": "-", "solved": sig.get("solved"), "graded": sig.get("total")}
    rec = fb.process_one(signal_to_rollout(sig), src, OUT_ROOT)
    st = rec.get("status", "?")
    if st not in ("ok", "kept") and rec.get("why"):
        log.warning("%s %s: %s", tid, st, str(rec["why"])[:300])
    return {"tid": tid, "status": st, "action": rec.get("action", "-"),
            "solved": rec.get("solved"), "graded": rec.get("graded"),
            "fast": rec.get("revalidate", {}).get("fast_path", ""),
            "hint": rec.get("hint"),
            "retuned": st in ("ok", "kept")}


def run_round(only: str | None = None, mix_out: Path | None = None,
              keep_signal: bool = False, limit: int | None = None,
              workers: int = 8) -> dict:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    CONSUMED.mkdir(parents=True, exist_ok=True)
    sig_files = sorted(SIGNALS.glob("*.json"))
    if only:
        sig_files = [p for p in sig_files if p.stem == only]
    if limit:
        sig_files = sig_files[:limit]
    if not sig_files:
        return {"processed": 0, "retuned": 0, "reason": "no signals"}

    # Each signal's re-tune is an independent LLM call writing its own retuned/
    # dir, so they run concurrently; the fold that follows needs them all, so it
    # waits here. Signals are archived only after processing, so a crash mid-
    # round leaves them pending and the next round picks them up (resumable).
    counts: dict[str, int] = {}
    retuned_ids: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_handle, sp): sp for sp in sig_files}
        for fut in as_completed(futs):
            sp = futs[fut]
            r = fut.result()
            if r is None:
                continue
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            log.info("%s solved=%s/%s -> %s (%s%s%s)", r["tid"], r["solved"],
                     r["graded"], r["action"], r["status"],
                     f", {r['fast']}" if r.get("fast") else "",
                     f", arm={r['hint']}" if r.get("hint") else "")
            if r["retuned"]:
                retuned_ids.append(r["tid"])
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
            rows[row["label"]] = json.dumps(row) + "\n"
            folded += 1
        tmp = target.with_suffix(target.suffix + ".incoming")
        with open(tmp, "w") as f:
            for iid in order:
                f.write(rows[iid])
        os.replace(tmp, target)          # atomic on the same filesystem
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
    BASE/evolution keeps every fold as a commit: the whole retuned/ tree plus a
    snapshot of the mix taken at the same moment. Failures never break the
    round -- versioning is an audit trail, not a dependency.
    """
    import subprocess
    root = BASE / "evolution"
    try:
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".gitignore").write_text("signals/\ndeferred_easier/\n")
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
    ap.add_argument("--log", default=str(BASE / "logs/evolve_ondella.log"))
    args = ap.parse_args()

    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        # FileHandler only. restart_evolve.sh launches the loop with
        # `>> $LOG 2>&1`, so a StreamHandler here writes every record to the
        # same file a second time -- the log doubles, and any `grep -c` over it
        # reads twice the real count. Stray library prints still reach the log
        # through that redirect; they just no longer arrive via two paths.
        handlers=[logging.FileHandler(args.log)])

    mix_out = Path(args.mix_out) if args.mix_out else None
    if args.once or args.only:
        r = run_round(only=args.only, mix_out=mix_out,
                      keep_signal=args.keep_signal, limit=args.limit,
                      workers=args.workers)
        log.info("round done: %s", r)
        if r.get("retuned") or r.get("folded"):
            _snapshot_lineage(f"once: {r}")
        return
    log.info("evolve_ondella loop up: signals=%s mix=%s interval=%ds workers=%d",
             SIGNALS, MIX, args.interval, args.workers)
    while True:
        try:
            r = run_round(workers=args.workers)
            if r["processed"]:
                log.info("round: %s", r)
            if r.get("retuned") or r.get("folded"):
                _snapshot_lineage(f"round: {r}")
        except Exception as e:  # noqa: BLE001
            log.exception("round failed: %s", e)
        _wait_for_signals(args.interval)


if __name__ == "__main__":
    main()
