"""EVOLVE_PEER_CONTEXT=1 (beta, default off): show a rewrite's author where the
student's ability stands now, as a few other tasks it partially solves.

A harder or easier author otherwise sees only this task's own groups. With the
flag on, the loop stages traces/peers/ beside the attempts: up to PEER_COUNT
other tasks whose latest training group in the signal's run came out inside
the target band (EVOLVE_HARDER_TARGET, else DEFAULT_BAND), each with its
instruction and the commands of one solved and one failed attempt, outputs
left out. The author's prompt asks it to bring this task to a similar
difficulty, and gives the root's path and layout for when the staged tasks do
not answer a question. Under traces/ and in the author's prompt only, so the
blind verifier never sees it.

Selection reads the run's rollouts/ listing once per run every CACHE_SEC and
opens only the header lines of the groups it considers. Any failure here is
logged and stages nothing: the rewrite goes on without the reference.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

import simplify_operators as so

from torchtitan.experiments.rl.examples.tmax import layout, rollout_record

log = logging.getLogger(__name__)

PEER_COUNT = 4
# Solved-count band out of so.TARGET_GROUP when EVOLVE_HARDER_TARGET is unset.
DEFAULT_BAND = "3-8"
# Groups examined, newest first, before giving up on filling PEER_COUNT.
MAX_GROUPS_SCANNED = 80
CACHE_SEC = 600
INSTRUCTION_CHARS = 3000
COMMAND_CHARS = 300
COMMANDS_CHARS = 6000

# Validation groups carry negative ids, counting down (-1, -2, ...); a bench
# set frozen from an eval keeps them under rollouts/ (g-270-r0.jsonl).
_RECORD = re.compile(r"g(-?\d+)-r(\d+)\.jsonl")
_cache: dict[Path, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("EVOLVE_PEER_CONTEXT", "0") == "1"


def _band(total: int) -> tuple[int, int]:
    if os.environ.get("EVOLVE_HARDER_TARGET", "").strip():
        band = so.target_band("EVOLVE_HARDER_TARGET", total)
    else:
        lo, hi = (int(x) * total / so.TARGET_GROUP for x in DEFAULT_BAND.split("-"))
        lo = min(max(1, int(lo + 0.5)), total - 1)
        band = lo, min(max(lo, int(hi + 0.5)), total - 1)
    assert band is not None
    return band


def _recency(group: int) -> tuple[int, int]:
    """Sort key, larger is preferred: any training group (id >= 0, counting up)
    before any validation group (id < 0, counting down), each newest first. The
    two counters are separate, so a training and a validation id say nothing
    about which came later; training groups are what the student trains on."""
    return (1, group) if group >= 0 else (0, -group)


def _latest_groups(run: layout.Run) -> list[tuple[int, Path]]:
    """(group, task directory) for each task's newest group by _recency, most
    preferred first. File names only: no record is opened."""
    out = []
    if not run.rollouts.is_dir():
        return out
    for task_dir in run.rollouts.iterdir():
        try:
            names = os.listdir(task_dir)
        except OSError:
            continue
        groups = [int(m[1]) for p in names if (m := _RECORD.fullmatch(p))]
        if groups:
            out.append((max(groups, key=_recency), task_dir))
    return sorted(out, key=lambda g: _recency(g[0]), reverse=True)


def _peer(task_dir: Path, group: int) -> dict | None:
    """One group's tally and a solved and a failed record, or None when it is
    outside the band or has no usable attempt of either kind."""
    heads = []
    records = task_dir.glob(f"g{group}-r*.jsonl")
    for path in sorted(records, key=lambda p: int(_RECORD.fullmatch(p.name)[2])):
        try:
            head = rollout_record.read_header(path)
        except (OSError, ValueError):
            continue
        if head.get("infra_failed") or not isinstance(head.get("reward"), (int, float)):
            continue
        heads.append((head, path))
    if len(heads) < 2:
        return None
    solved = [p for h, p in heads if h["reward"] >= 1.0]
    failed = [p for h, p in heads if h["reward"] < 1.0]
    lo, hi = _band(len(heads))
    if not (lo <= len(solved) <= hi) or not failed:
        return None
    head = heads[0][0]
    return {
        "task": head.get("task") or task_dir.name,
        "rev": head.get("rev"),
        "run": head.get("run"),
        "group": group,
        "solved": len(solved),
        "total": len(heads),
        "solved_record": str(solved[0]),
        "failed_record": str(failed[0]),
    }


def select(run: layout.Run) -> list[dict]:
    """Up to PEER_COUNT + 1 in-band groups, newest first (one spare for when
    the task being rewritten is among them). Cached per run for CACHE_SEC."""
    with _cache_lock:
        hit = _cache.get(run.path)
        if hit and time.monotonic() - hit[0] < CACHE_SEC:
            return hit[1]
    peers: list[dict] = []
    for group, task_dir in _latest_groups(run)[:MAX_GROUPS_SCANNED]:
        if (peer := _peer(task_dir, group)) is not None:
            peers.append(peer)
            if len(peers) > PEER_COUNT:
                break
    with _cache_lock:
        _cache[run.path] = (time.monotonic(), peers)
    return peers


def commands(record: Path) -> str:
    """The commands an attempt ran, turn by turn, without their output."""
    head, turns = rollout_record.read_record(record)
    lines = []
    for turn in turns:
        if head.get("agent") == "vanillux":
            cmd = rollout_record.vanillux_command(turn.get("raw", ""))
        else:
            cmd = "".join(turn.get("keystrokes") or ())
        cmd = cmd.strip()
        if len(cmd) > COMMAND_CHARS:
            cmd = cmd[:COMMAND_CHARS] + " [cut]"
        if cmd or turn.get("task_complete"):
            lines.append(f"## turn {turn.get('turn')}\n{cmd or '(submitted)'}\n")
    text = "\n".join(lines)
    if len(text) > COMMANDS_CHARS:
        text = text[:COMMANDS_CHARS] + f"\n[cut: {len(turns)} turns in all]\n"
    return text


def _instruction(root: layout.Root, task: str, rev) -> str:
    path = root.evolution.task(task).rev(int(rev or 0)) / "instruction.md"
    if not path.is_file() and not rev:
        sources = root.data / "sources"
        found = sorted(sources.glob(f"*/tasks/{task}/instruction.md")) if sources.is_dir() else []
        path = found[0] if found else path
    text = path.read_text(errors="replace") if path.is_file() else ""
    if len(text) > INSTRUCTION_CHARS:
        text = text[:INSTRUCTION_CHARS] + "\n[cut]\n"
    return text


def stage(root: layout.Root, task_id: str, traces: Path, signal: dict) -> int:
    """traces/peers/: index.json (the root's path and the peers) and, per
    peer, instruction.md, solved.md and failed.md. Returns how many peers were
    staged. index.json is written even with none, so the author still gets the
    pointer to the rest of the root; a failure stages nothing."""
    try:
        peers = [p for p in select(root.run(str(signal["run"]))) if p["task"] != task_id]
        out = traces / "peers"
        index = []
        for i, peer in enumerate(peers[:PEER_COUNT], 1):
            d = out / f"peer-{i}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "instruction.md").write_text(_instruction(root, peer["task"], peer["rev"]))
            (d / "solved.md").write_text(commands(Path(peer["solved_record"])))
            (d / "failed.md").write_text(commands(Path(peer["failed_record"])))
            index.append({k: peer[k] for k in ("task", "rev", "run", "group", "solved", "total")}
                         | {"dir": f"traces/peers/peer-{i}"})
        out.mkdir(parents=True, exist_ok=True)
        layout.write_json_atomic(out / "index.json", {"root": str(root.path), "peers": index})
        return len(index)
    except Exception:  # noqa: BLE001 -- a beta reference must never stop a rewrite
        log.warning("%s peer context not staged", task_id, exc_info=True)
        shutil.rmtree(traces / "peers", ignore_errors=True)
        return 0


def prompt(traces: Path) -> str:
    """The author prompt's STUDENT ABILITY paragraph (when peers were staged)
    and its pointer to the rest of the root; "" without index.json. Only the
    rewrite author's prompt carries it: the blind verifier is kept apart from
    the root by never being told where it is."""
    try:
        index = json.loads((traces / "peers" / "index.json").read_text())
        root, peers = index["root"], index["peers"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    text = ""
    if peers:
        rows = "".join(
            f"- {p['dir']}/: {p['solved']} of {p['total']} solved (group {p['group']})\n"
            for p in peers
        )
        text += (
            "\n\nWHERE THE STUDENT STANDS NOW\n"
            "These are other tasks from the same training run whose latest group the "
            "student partly solved. Each directory holds the task's instruction and the "
            "commands of one solved and one failed attempt (outputs left out):\n"
            f"{rows}"
            "This is the difficulty the student can partly reach today. Read them, and "
            "bring this task to a similar difficulty: compare how long the solved "
            "attempts are, what the failed ones got stuck on, and how exact each task's "
            "requirements are. They are read-only evidence about the student; do not copy "
            "any of them into the task.\n"
        )
    text += (
        "\n\nTHE REST OF THE EXPERIMENT\n"
        f"The whole experiment root is at {root}. When what traces/ holds does not "
        "answer a question about where the student stands, you may read other "
        "tasks there:\n"
        f"- {root}/runs/<run>/rollouts/<task>/g<group>-r<idx>.jsonl: one attempt per "
        "file. Line 1 is its header (task, rev, group, reward, finish_reason, turns); "
        "each later line is one turn. `head -1` is enough to tally a group.\n"
        f"- {root}/evolution/tasks/<task>/rewrites/<stamp>--<job>/: one earlier rewrite "
        "of that task. rewrite.json holds its status and its input and result "
        "revisions; notes/hardening.md and notes/simplify.json say what its author "
        "changed, why, and what it predicted.\n"
        "The root holds thousands of tasks and gigabytes of records, and your time "
        "budget covers the whole rewrite: open a few files you picked for a reason, "
        "never scan it. It is read-only, and none of it goes into the task.\n"
    )
    return text
