#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The intervention rewriter: every version is the original plus a named set
of interventions, built from the original every time.

    intervention_rewriter.py <job> [--workers 16]

A rewriter on the scheduler socket that uses only the bench interface (events,
the which_version hook, add / reweight / retire, the files) and the evolve
loop's rewrite machinery (evolve_ondella.handle: oracle, probes, verifier,
grader replay, the simplify rules). What it decides:

- A version is a set of interventions, each a named change to the original
  with a kind (KINDS). The original is the empty set. Every build starts from
  the original package and applies the whole set (the author is told so in
  run/intervention_plan.json); no version is its parent plus a delta, so a
  version never carries help nobody chose for it.
- Easier = one intervention added to a set; harder = one withdrawn. Withdrawal
  follows WITHDRAW_RANK: setup and checking hints first, scope removals (a
  required part of the task taken out) last.
- What to add comes from a diagnosis: a codex session (diagnose_prompt) that
  reads the original package and the training attempts of every version,
  finds where the attempts on the base version first go wrong, and proposes
  up to --candidates interventions that differ in mechanism, not only in
  strength. Each proposal is built, in parallel, as base + proposal.
- Cold start: the first diagnosis runs as soon as --cold-min attempts on the
  original have been scored (rollout_done), not at the first group's end; the
  slots keep drawing the original meanwhile (the interface has no idle draw).
- A version's latest group all-failed, with no descendant of its set active
  and learnable or untested, and a group since its last diagnosis: diagnose
  again with it as base. A version's latest group at >= --mastered: build its
  set minus the next intervention in withdrawal order that has not been built.
- which_version: a version with no group drawn yet is drawn first (oldest
  first); otherwise no answer, so the weighted draw.
- Weights at every group_done: p(1-p) of the last --history groups (smoothed
  by --prior), --explore while untested, never below --original-floor for the
  original. At --max-active a new version replaces the active version with the
  longest all-failed streak (never the original, never one that solved
  anything in its last --history groups); a version with an active
  descendant and --stale-after all-failed groups in a row is retired.

State lives in evolution/interventions/ (state.json, diagnoses/, signals/);
every decision is one ``policy ...`` line in evolution/loop.log. It reads
training records only, never the readout.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import shutil
import signal as signals
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from torchtitan.experiments.rl.examples.tmax import layout  # noqa: E402
from torchtitan.experiments.rl.examples.tmax.overfit_bench.ladder_rewriter import (  # noqa: E402
    _MADE,
    _evolve,
    fail_streak,
    is_original,
    learnable,
    solve_rate,
    Sweeper,
)
from torchtitan.experiments.rl.examples.tmax.overfit_bench.rewriter_client import (  # noqa: E402
    RewriterClient,
    RewriterError,
)

log = logging.getLogger("interventions")

# Withdrawn in this order (lowest first) when a version is mastered.
WITHDRAW_RANK = {
    "setup_hint": 0,
    "check_hint": 0,
    "direction_hint": 1,
    "decomposition": 1,
    "feedback": 1,
    "smaller_instance": 1,
    "supplied_work": 2,
    "scope_removal": 3,
}
KINDS = tuple(WITHDRAW_RANK)
HINT_LEVELS = ("none", "vague", "specific")


@dataclass(frozen=True, kw_only=True)
class Policy:
    history: int = 3
    prior: float = 0.5
    explore: float = 0.25
    original_floor: float = 0.05
    mastered: float = 0.9
    max_active: int = 8
    stale_after: int = 2
    cold_min: int = 6
    candidates: int = 3
    diagnose_attempts: int = 24
    build_attempts: int = 8
    diagnose_timeout: int = 3600
    provider_retries: int = 3
    provider_retry_sec: int = 300


def key(interventions) -> str:
    """A set's identity: its sorted intervention ids, '' for the original."""
    return ",".join(sorted(interventions))


def hint_level(kinds_levels: list[str]) -> str:
    """The simplify prompt's level for a set: the largest any member asks for."""
    return max(kinds_levels, key=HINT_LEVELS.index, default="none")


def withdraw_order(members: list[dict]) -> list[dict]:
    """Setup and checking hints first, scope removals last; within a rank the
    oldest intervention first."""
    return sorted(members, key=lambda i: (WITHDRAW_RANK[i["kind"]], i["born"], i["id"]))


def weight(version: dict, groups: list[tuple], policy: Policy) -> float:
    p = solve_rate(groups, policy)
    w = policy.explore if p is None else p * (1 - p)
    if is_original(version):
        w = max(w, policy.original_floor)
    return w


def check_diagnosis(d: dict, *, catalog: dict, max_candidates: int) -> list[dict]:
    """The proposals of a diagnosis.json, validated; raises on a malformed one."""
    if not isinstance(d.get("bottleneck"), dict) or not str(d["bottleneck"].get("name", "")).strip():
        raise ValueError("diagnosis needs bottleneck.name")
    out = []
    for c in d.get("candidates") or []:
        for f in ("name", "kind", "spec", "mechanism", "hint_level"):
            if not isinstance(c.get(f), str) or not c[f].strip():
                raise ValueError(f"candidate needs {f}")
        if c["kind"] not in WITHDRAW_RANK:
            raise ValueError(f"unknown kind {c['kind']!r}")
        if c["hint_level"] not in HINT_LEVELS:
            raise ValueError(f"unknown hint_level {c['hint_level']!r}")
        if c.get("reuses") and c["reuses"] not in catalog:
            raise ValueError(f"reuses unknown intervention {c['reuses']!r}")
        out.append(c)
    if not out:
        raise ValueError("diagnosis proposes no candidate")
    return out[:max_candidates]


class Coordinator:
    """``ev`` is evolution/evolve_ondella (a stand-in in tests); ``diagnose``
    runs one diagnosis session (run_diagnosis by default)."""

    def __init__(self, root: layout.Root, client: RewriterClient, ev, *, policy: Policy = Policy(),
                 workers: int = 16, sweeper: Sweeper | None = None, diagnose=None) -> None:
        self.root, self.client, self.ev, self.policy = root, client, ev, policy
        self.sweeper = sweeper or Sweeper(root, ev)
        self.diagnose = diagnose or run_diagnosis
        ev.ATTEMPT_RECORD_DIRS = ("rollouts",)
        new_dir = ev._new_rewrite_dir
        if not getattr(new_dir, "ladder_tracked", False):

            def tracked(task, job):
                rw = new_dir(task, job)
                _MADE.rewrite = rw
                return rw

            tracked.ladder_tracked = True
            ev._new_rewrite_dir = tracked
        self.dir = root.evolution.path / "interventions"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.versions: dict[str, dict] = {}
        # version -> [(passed, scored, stamp)], oldest first; version -> attempts.
        self.groups: dict[str, list[tuple]] = collections.defaultdict(list)
        self.attempts: dict[str, list[dict]] = collections.defaultdict(list)
        self._seen: set[tuple] = set()
        self.drawn: collections.Counter[str] = collections.Counter()
        self._hook_lock = threading.Lock()
        self._ex = ThreadPoolExecutor(workers, thread_name_prefix="interventions")
        self._futs: dict[Future, tuple[str, str]] = {}  # future -> (kind, set key or base key)

    # -- persistent state ---------------------------------------------------

    def _load(self) -> dict:
        path = self.dir / "state.json"
        if path.is_file():
            return json.loads(path.read_text())
        return {"interventions": {}, "sets": {}, "diagnoses": {}}

    def _save(self) -> None:
        layout.write_json_atomic(self.dir / "state.json", self.state)

    def set_of(self, vid: str) -> list[str] | None:
        """The intervention set of a version, [] for the original, None for a
        version this rewriter did not build."""
        v = self.versions.get(vid)
        if v is not None and is_original(v):
            return []
        for k, s in self.state["sets"].items():
            if s.get("version") == vid:
                return k.split(",") if k else []
        return None

    def version_of(self, k: str) -> dict | None:
        if k == "":
            return next((v for v in self.versions.values() if is_original(v)), None)
        vid = (self.state["sets"].get(k) or {}).get("version")
        return self.versions.get(vid) if vid else None

    # -- events -------------------------------------------------------------

    def start(self) -> None:
        self.sweeper.sweep(everything=True, why="left by an earlier rewriter process")
        for k, s in self.state["sets"].items():
            if s["status"] == "building":
                # The restart killed its session; the same plan is built again.
                s.update({"status": "retry", "retry_at": 0} if s.get("args") else {"status": "interrupted"})
                log.info("policy build {%s} was interrupted by a restart -> %s", k, s["status"])
        for b, d in self.state["diagnoses"].items():
            if d["status"] == "running":
                d["status"] = "interrupted"
        self._save()
        self.refresh(with_rows=True)
        for run in self.root.run_dirs():
            for event in layout.read_jsonl(run.scheduler / "events.jsonl"):
                self.on_event({**event, "run": event.get("run") or run.name})
        for vid in list(self.groups):
            self.drawn[vid] = max(self.drawn[vid], len(self.groups[vid]))
        self.rebalance()

    def refresh(self, *, with_rows: bool = False) -> None:
        state = self.client.get_state(with_rows=with_rows)
        rows = {vid: v.get("row") for vid, v in self.versions.items()}
        self.versions = {v["id"]: v for v in state["versions"]}
        if not with_rows:
            for vid, v in self.versions.items():
                if rows.get(vid) is not None:
                    v["row"] = rows[vid]

    def on_event(self, e: dict) -> bool:
        """Record one event; True for a new group_done."""
        vid = e.get("version")
        if not vid or e.get("event") not in ("rollout_done", "group_done"):
            return False
        k = (e["event"], e.get("run"), vid, e.get("group"), e.get("idx"))
        if k in self._seen:
            return False
        self._seen.add(k)
        if e["event"] == "rollout_done":
            if e.get("reward") is not None:
                self.attempts[vid].append({
                    "run": e.get("run"), "path": e["record_path"], "reward": float(e["reward"]),
                    "group": e.get("group"), "idx": e.get("idx"),
                    "finish_reason": e.get("finish_reason"), "stamp": e.get("stamp") or "",
                })
            return False
        if e.get("scored"):
            self.groups[vid].append((int(e["passed"]), int(e["scored"]), str(e.get("stamp") or ""),
                                     e.get("run"), e.get("group")))
        return True

    def which_version(self, candidates: list[dict]) -> str | None:
        """A version with no group drawn yet, oldest first; else the weighted draw."""
        with self._hook_lock:
            fresh = [c for c in candidates if not is_original(c) and self.drawn[c["id"]] == 0]
            if fresh:
                vid = min(fresh, key=lambda c: (str(c.get("added_at") or ""), c["id"]))["id"]
            else:
                vid = None
            # Every draw counts toward its version, answered here or not; the
            # answer is the one draw this hook knows the version of.
            if vid is not None:
                self.drawn[vid] += 1
            return vid

    # -- what the policy reads ---------------------------------------------------

    def active(self) -> list[dict]:
        return [v for v in self.versions.values() if v["status"] == "active"]

    def _busy(self, k: str) -> bool:
        """A diagnosis of ``k`` or a build with ``k`` as base is running."""
        if (self.state["diagnoses"].get(k) or {}).get("status") == "running":
            return True
        return any(s["status"] in ("building", "retry") and s.get("base") == k for s in self.state["sets"].values())

    def _descendants_alive(self, k: str) -> bool:
        """A version whose set strictly contains ``k`` is active and learnable,
        or untested, or still being built."""
        base = set(k.split(",")) if k else set()
        for sk, s in self.state["sets"].items():
            members = set(sk.split(",")) if sk else set()
            if not (members > base):
                continue
            if s["status"] in ("building", "retry"):
                return True
            v = self.versions.get(s.get("version") or "")
            if v is None or v["status"] != "active":
                continue
            groups = self.groups[v["id"]]
            if not groups or learnable(groups, self.policy):
                return True
        return False

    # -- the policy ------------------------------------------------------------

    def plan(self) -> None:
        pol = self.policy
        for k, s in list(self.state["sets"].items()):
            if s["status"] == "retry" and s.get("retry_at", 0) <= time.time():
                parent = self.versions.get(s["parent"])
                if parent is None:
                    s["status"] = "failed"
                    continue
                a = s["args"]
                self.build(k.split(",") if k else [], base=s["base"], parent=parent, attempts=a["attempts"],
                           solved=a["solved"], total=a["total"])
        # Most recently measured first: of several all-failed bases, the one the
        # newest group is about is diagnosed, and the rest wait for its result.
        for v in sorted(self.active(), key=lambda v: self.groups[v["id"]][-1][2] if self.groups[v["id"]] else "",
                        reverse=True):
            members = self.set_of(v["id"])
            if members is None:
                continue
            k = key(members)
            if self._busy(k):
                continue
            groups = self.groups[v["id"]]
            last = groups[-1] if groups else None
            diag = self.state["diagnoses"].get(k) or {}
            if last is not None and last[1] and last[0] / last[1] >= pol.mastered and members:
                self.withdraw(v, members)
                continue
            if self._descendants_alive(k):
                continue
            if not groups:
                if is_original(v) and not diag:
                    failed = [a for a in self.attempts[v["id"]] if a["reward"] < 1.0]
                    if len(failed) >= pol.cold_min:
                        self.start_diagnosis(v, k, why=f"cold start: {len(failed)} scored attempts failed")
                continue
            if last[0] == 0 and len(groups) > diag.get("groups", -1):
                pending = self._pending_measurement()
                if pending:
                    # Measure before expanding: the next diagnosis then reads
                    # every version's result, and one base's interventions are
                    # not copied onto every other all-failed base.
                    if diag.get("waiting") != pending:
                        self.state["diagnoses"].setdefault(k, {"status": "waiting", "groups": -1})["waiting"] = pending
                        self._save()
                        log.info("policy wait {%s} on %s: %s not measured yet", k, v["label"], pending)
                    continue
                self.start_diagnosis(v, k, why=f"latest group g{last[4]} solved 0/{last[1]}")

    def _pending_measurement(self) -> str:
        """What a new diagnosis would not see yet: versions being built and
        active versions with no group, '' when there is none."""
        diagnosing = sorted(k for k, d in self.state["diagnoses"].items() if d.get("status") == "running")
        building = sorted(k for k, s in self.state["sets"].items() if s["status"] in ("building", "retry"))
        untested = sorted(v["label"] for v in self.active() if not is_original(v) and not self.groups[v["id"]])
        return ", ".join([f"diagnosis of {{{k}}} running" for k in diagnosing]
                         + [f"{{{k}}} building" for k in building] + [f"{u} untested" for u in untested])

    def withdraw(self, v: dict, members: list[str]) -> None:
        cat = self.state["interventions"]
        for i in withdraw_order([cat[m] for m in members]):
            rest = [m for m in members if m != i["id"]]
            rk = key(rest)
            if rk == "" or not self._buildable(rk):
                continue
            last = self.groups[v["id"]][-1]
            log.info("policy withdraw %s from {%s} (%s, rank %d): %s solved %d/%d",
                     i["id"], key(members), i["kind"], WITHDRAW_RANK[i["kind"]], v["label"], last[0], last[1])
            attempts = [a for a in self.attempts[v["id"]] if a["run"] == last[3] and a["group"] == last[4]]
            self.build(rest, base=key(members), parent=v, attempts=attempts, solved=last[0], total=last[1])
            return

    def start_diagnosis(self, v: dict, k: str, *, why: str) -> None:
        d = self.state["diagnoses"].setdefault(k, {})
        d.update({"status": "running", "groups": len(self.groups[v["id"]]), "started": layout.stamp(),
                  "version": v["id"], "runs": d.get("runs", 0) + 1})
        self._save()
        log.info("policy diagnose {%s} on %s: %s", k, v["label"], why)
        fut = self._ex.submit(self._diagnose, v, k)
        self._futs[fut] = ("diagnosis", k)

    def _diagnose(self, v: dict, k: str) -> dict:
        work = self.dir / "diagnoses" / f"{layout.stamp()}--{(k or 'original').replace(',', '+')}"
        work.mkdir(parents=True)
        index = stage_diagnosis(self, work, v, k)
        out = self.diagnose(self, work)
        return {"work": work, "index": index, "diagnosis": out, "version": v}

    def on_diagnosis(self, k: str, result: dict) -> None:
        d = self.state["diagnoses"][k]
        v, work = result["version"], result["work"]
        try:
            cands = check_diagnosis(result["diagnosis"], catalog=self.state["interventions"],
                                    max_candidates=self.policy.candidates)
        except (ValueError, TypeError, AttributeError) as e:
            d.update({"status": "failed", "reason": str(e)[:300], "dir": work.name})
            self._save()
            log.warning("policy diagnosis {%s} failed: %s", k, e)
            return
        d.update({"status": "done", "dir": work.name, "bottleneck": result["diagnosis"]["bottleneck"]["name"]})
        members = k.split(",") if k else []
        cited = cited_attempts(result["diagnosis"], result["index"])
        for c in cands:
            if c.get("reuses"):
                iid = c["reuses"]
            else:
                iid = f"i{len(self.state['interventions']) + 1}"
                self.state["interventions"][iid] = {
                    "id": iid, "name": c["name"], "kind": c["kind"], "hint_level": c["hint_level"],
                    "spec": c["spec"], "mechanism": c["mechanism"],
                    "bottleneck": result["diagnosis"]["bottleneck"]["name"],
                    "born": layout.stamp(), "diagnosis": work.name,
                }
            new = members + [iid] if iid not in members else members
            nk = key(new)
            if not self._buildable(nk):
                log.info("policy skip {%s}: already built (%s)", nk, self.state["sets"][nk]["status"])
                continue
            log.info("policy propose {%s} = {%s} + %s %s (%s): %s", nk, k, iid, c["kind"], c["hint_level"], c["name"])
            last = self.groups[v["id"]][-1] if self.groups[v["id"]] else None
            self.build(new, base=k, parent=v, attempts=cited or self.attempts[v["id"]][-self.policy.build_attempts:],
                       solved=last[0] if last else 0, total=last[1] if last else len(self.attempts[v["id"]]))
        self._save()

    # -- builds ------------------------------------------------------------------

    def _buildable(self, k: str) -> bool:
        """Never built, or its build was cut short by a restart."""
        return (self.state["sets"].get(k) or {}).get("status", "interrupted") == "interrupted"

    def build(self, members: list[str], *, base: str, parent: dict, attempts: list[dict], solved: int,
              total: int) -> None:
        ev, root = self.ev, self.root
        k = key(members)
        original = self.version_of("")
        if original is None or not attempts:
            log.warning("policy cannot build {%s}: %s", k, "no original" if original is None else "no attempts")
            return
        cat = self.state["interventions"]
        run = collections.Counter(a["run"] for a in attempts).most_common(1)[0][0]
        paths = [a["path"] for a in attempts if a["run"] == run][: self.policy.build_attempts]
        name = f"{layout.stamp()}--{(k or 'original').replace(',', '+')}"
        plan = {
            "version": [{f: cat[m][f] for f in ("id", "name", "kind", "spec", "mechanism", "bottleneck")}
                        for m in members],
            "built_from": "the original task (revision 0)",
            "measured_on": {"version": parent["label"], "set": base.split(",") if base else [],
                            "solved": solved, "total": total},
        }
        data = {
            "task": original["task"], "rev": 0, "run": run, "group": attempts[-1].get("group"),
            "attempts": paths, "direction": "easier", "solved": solved, "total": len(paths),
            "created": layout.stamp(), "version": original["id"],
            "simplify_hint": hint_level([cat[m]["hint_level"] for m in members]),
            "intervention_plan": plan,
        }
        path = self.dir / "signals" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        layout.write_json_atomic(path, data)
        sig = ev.Signal(root.run(run), path, f"interventions/{name}", data, None)
        md = original["row"]["metadata"]
        tid = original["task"]
        declared = {tid: {kk: md[rk] for kk, rk in ev.ROW_KEYS.items() if rk in md}}
        hook = ev.row_pretest(md)
        lists = ev.pack.Protected.from_tmax(md.get("tmax") or {})
        prev = self.state["sets"].get(k) or {}
        self.state["sets"][k] = {"status": "building", "base": base, "parent": parent["id"],
                                 "started": layout.stamp(), "signal": sig.sid, "version": None,
                                 "retries": prev.get("retries", 0),
                                 "args": {"attempts": attempts, "solved": solved, "total": total}}
        self._save()
        if ev.rw_workspace.configured_root(root) is not None:
            ev.ec.rewrite_budget_sec(root, refresh=True)
        fut = self._ex.submit(self._rewrite, sig, declared=declared, pretests={tid: hook} if hook else {},
                              protecteds={tid: lists} if lists is not None else {}, input_row=original["row"])
        self._futs[fut] = ("build", k)
        log.info("policy build {%s} from the original at help %s (base {%s}, %d attempts of %s)",
                 k, data["simplify_hint"], base, len(paths), parent["label"])

    def _rewrite(self, sig, **kwargs) -> dict:
        _MADE.rewrite = None
        try:
            return self.ev.handle(self.root, sig, **kwargs)
        finally:
            rw = _MADE.rewrite
            try:
                self.sweeper.sweep(rewrite=(sig.task, rw.path.name) if rw is not None else None,
                                   why="its rewrite ended")
            except Exception as e:  # noqa: BLE001 -- cleanup must not turn a verdict into a crash
                log.warning("sandbox sweep after %s failed: %s: %s", sig.sid, type(e).__name__, e)

    def on_build(self, k: str, h: dict) -> None:
        ev, s = self.ev, self.state["sets"][k]
        meta = h["meta"]
        meta["interventions"] = {"set": k.split(",") if k else [], "base": s["base"], "version": None}
        if h["status"] == "accepted":
            self._fold(k, h)
        else:
            layout.write_json_atomic(h["rewrite"].meta, meta)
        s.update({"status": h["status"], "rewrite": h["rewrite"].path.name, "finished": layout.stamp(),
                  "reason": (meta.get("reason") or "")[:300]})
        if (h["status"] == "failed" and "(provider_" in s["reason"]
                and s.get("retries", 0) < self.policy.provider_retries):
            # The provider refused the session (capacity, rate limit, a dropped
            # stream): no verdict on the plan, so the same plan is built again.
            s.update({"status": "retry", "retries": s.get("retries", 0) + 1,
                      "retry_at": time.time() + self.policy.provider_retry_sec})
            log.info("policy retry {%s} in %ds (attempt %d of %d): %s", k, self.policy.provider_retry_sec,
                     s["retries"], self.policy.provider_retries, s["reason"][:120])
        self._save()
        log.info("policy built {%s} -> %s (%s%s)", k, h["status"], meta.get("stage") or "-",
                 f": {meta['reason'][:200]}" if meta.get("reason") else "")
        ev._close(self.root, h, dry=False)

    def _fold(self, k: str, h: dict) -> None:
        ev, meta, s = self.ev, h["meta"], self.state["sets"][k]
        task = self.root.evolution.task(meta["task"])
        original = self.version_of("")
        if not self._make_room():
            ev._finish(h, "failed", stage="pool", reason=f"--max-active {self.policy.max_active} reached")
            return
        try:
            row = ev._prepare_fold_row(h["rewrite"].package, json.dumps(original["row"], ensure_ascii=False),
                                       meta, {})
            to_rev = max(task.revs(), default=0) + 1
            row["metadata"]["rev"] = to_rev
            os.rename(h["rewrite"].package, task.rev(to_rev))
        except Exception as e:  # noqa: BLE001 -- one malformed package, not the loop
            ev._finish(h, "failed", stage="fold", reason=f"{type(e).__name__}: {e}")
            return
        try:
            info = self.client.add(row, weight=self.policy.explore, parent=s["parent"])
        except RewriterError as e:
            ev._finish(h, "failed", stage="pool", reason=f"r{to_rev} not added to the pool: {e}")
            return
        meta.update({"status": "accepted", "result_rev": to_rev, "finished": layout.stamp()})
        meta["interventions"]["version"] = info["id"]
        layout.write_json_atomic(h["rewrite"].meta, meta)
        h["status"] = "accepted"
        layout.append_jsonl(task.lineage, {
            "stamp": layout.stamp(), "event": "fold", "from_rev": 0, "to_rev": to_rev, "version": info["id"],
            "parent_version": s["parent"], "interventions": k.split(",") if k else [],
            "rewrite": f"rewrites/{h['rewrite'].path.name}",
        })
        s["version"] = info["id"]
        self.versions[info["id"]] = {**info, "row": row}
        log.info("fold {%s} as r%d (%s)", k, to_rev, info["id"][:12])

    def _make_room(self) -> bool:
        active = self.active()
        if len(active) < self.policy.max_active:
            return True
        worst = sorted(
            (v for v in active if not is_original(v)
             and not any(g[0] > 0 for g in self.groups[v["id"]][-self.policy.history:])
             and self.groups[v["id"]]),
            key=lambda v: -fail_streak(self.groups[v["id"]]),
        )
        if not worst:
            return False
        self._retire(worst[0], f"replaced at cap {self.policy.max_active} "
                               f"(all-failed streak {fail_streak(self.groups[worst[0]['id']])})")
        return True

    def _retire(self, v: dict, reason: str) -> None:
        self.client.retire(v["id"])
        v["status"] = "retired"
        log.info("policy retire %s {%s}: %s", v["label"], key(self.set_of(v["id"]) or []), reason)

    def rebalance(self) -> None:
        try:
            self.refresh()
            pol = self.policy
            for v in self.active():
                members = self.set_of(v["id"])
                groups = self.groups[v["id"]]
                if (members and fail_streak(groups) >= pol.stale_after
                        and self._descendants_alive(key(members))):
                    self._retire(v, f"all-failed for its last {fail_streak(groups)} groups; a descendant is alive")
            for v in self.active():
                w = weight(v, self.groups[v["id"]], pol)
                if abs(w - float(v["weight"])) > 1e-9:
                    self.client.reweight(v["id"], w)
                    log.info("policy reweight %s: %.4f -> %.4f", v["label"], float(v["weight"]), w)
                    v["weight"] = w
        except RewriterError as e:
            log.warning("rebalance not applied: %s", e)

    # -- driving -------------------------------------------------------------------

    def collect(self) -> None:
        for fut in [f for f in self._futs if f.done()]:
            kind, k = self._futs.pop(fut)
            try:
                result = fut.result()
            except Exception as e:  # noqa: BLE001 -- one session, not the loop
                log.exception("policy %s {%s} raised: %s", kind, k, e)
                if kind == "diagnosis":
                    self.state["diagnoses"][k].update({"status": "failed", "reason": str(e)[:300]})
                else:
                    self.state["sets"][k].update({"status": "failed", "reason": str(e)[:300]})
                self._save()
                continue
            if kind == "diagnosis":
                self.on_diagnosis(k, result)
            else:
                self.on_build(k, result)
                self.rebalance()

    def step(self, timeout: float) -> None:
        event = self.client.wait_for({"rollout_done", "group_done"}, timeout=timeout)
        grouped = False
        while event is not None:
            grouped |= self.on_event(event)
            event = self.client.wait_for({"rollout_done", "group_done"}, timeout=0)
        if grouped:
            self.rebalance()
        self.collect()
        self.plan()

    def run(self, tick: float = 5.0) -> None:
        while True:
            if not self.client.connected:
                try:
                    self.client.connect()
                    self.refresh(with_rows=True)
                except RewriterError as e:
                    log.warning("%s", e)
                    continue
            try:
                self.step(tick)
            except RewriterError as e:
                log.warning("scheduler call failed: %s", e)


# -- diagnosis ---------------------------------------------------------------------


def _head(path: Path) -> dict:
    try:
        with path.open() as f:
            return json.loads(f.readline())
    except (OSError, ValueError):
        return {}


def stage_diagnosis(co: Coordinator, work: Path, base: dict, k: str) -> dict:
    """The session's inputs under ``work``: original/ (the task package),
    attempts/ (training records, the base version's newest first, then the
    other versions'), versions.md and catalog.json. Returns the attempts index:
    local name -> run, record path, version."""
    pol, root = co.policy, co.root
    task = co.root.evolution.task(base["task"])
    src = task.rev(0)
    if not src.exists():
        src = co.ev._ensure_r0(root, task, base["task"])
    shutil.copytree(src, work / "original", ignore=shutil.ignore_patterns("run", "traces", ".*"))
    (work / "attempts").mkdir()
    index, lines = {}, ["# Versions of this task and what training measured\n"]
    order = [base] + [v for v in co.versions.values() if v["id"] != base["id"]]
    for v in order:
        members = co.set_of(v["id"])
        label = "original" if members == [] else "{" + key(members or []) + "}" if members is not None else v["label"]
        groups = co.groups[v["id"]]
        lines.append(f"\n## {v['label']} = {label} ({v['status']}){'  <- base of this diagnosis' if v is base else ''}\n")
        lines.append("groups (solved/scored): " + (", ".join(f"{g[0]}/{g[1]}" for g in groups) or "none yet") + "\n")
        n = pol.diagnose_attempts if v is base else pol.diagnose_attempts // 4
        for a in list(reversed(co.attempts[v["id"]]))[:n]:
            rec = root.run(a["run"]).path / a["path"]
            name = f"{v['label'].split('@')[-1]}-{a['run'][-8:]}-g{a['group']}-r{a['idx']}.jsonl"
            try:
                os.symlink(rec, work / "attempts" / name)
            except OSError:
                continue
            h = _head(rec)
            index[name] = {"run": a["run"], "path": a["path"], "version": v["id"], "group": a["group"]}
            lines.append(f"- attempts/{name}: reward {a['reward']:g}, {a['finish_reason']}, "
                         f"{h.get('turns', '?')} turns, submitted {h.get('submitted')}\n")
    (work / "versions.md").write_text("".join(lines))
    layout.write_json_atomic(work / "catalog.json", {
        "interventions": co.state["interventions"],
        "sets": {kk: {f: s.get(f) for f in ("status", "base", "reason")} for kk, s in co.state["sets"].items()},
        "base_set": k.split(",") if k else [],
    })
    layout.write_json_atomic(work / "index.json", index)
    return index


def cited_attempts(diagnosis: dict, index: dict) -> list[dict]:
    """The training attempts the diagnosis cites, as build inputs."""
    out, seen = [], set()
    for ev in (diagnosis.get("bottleneck") or {}).get("evidence") or []:
        name = Path(str(ev.get("attempt", ""))).name
        if name in index and name not in seen:
            seen.add(name)
            i = index[name]
            out.append({"run": i["run"], "path": i["path"], "group": i["group"]})
    return out


def diagnose_prompt(extra: str, candidates: int) -> str:
    return f"""You are diagnosing a student model's training attempts on a task, to
choose what help to build into the next versions of it.

The student is scored only on the ORIGINAL task (original/: instruction.md,
environment/, solution/, tests/), unchanged. Versions exist so that the student
learns to solve the original. A version is the original plus a set of named
interventions, always built fresh from the original; catalog.json lists every
intervention so far and every set built (base_set is the set of the version you
diagnose; [] is the original). versions.md lists every version with its
training groups' solve counts and its attempts in attempts/ (one JSON line of
summary, then one line per turn).

Do this:
1. Read every attempt of the base version (listed first in versions.md) and
   find where each first goes wrong: the first decision or action after which
   it could no longer pass. Count how many attempts stop at each point. Compare
   with the original's reference solution and tests to name what was wrong.
   Read other versions' attempts where they show whether a candidate help
   works (a version that already has it).
2. Name the dominant bottleneck: the earliest point most attempts fail at.
3. Propose up to {candidates} interventions on that bottleneck that differ in
   mechanism -- what in the task changes, not only how much is said: for
   example making the original's own error or output reach the student sooner,
   a direction in words, decomposing the goal into checked subgoals, a smaller
   instance of the same decision, supplying work outside the retained skill.
   Each is specified against the ORIGINAL so it can be built together with
   base_set's interventions, in a few sentences an author can apply exactly.
   Each intervention is one kind of help, withdrawn as a whole: how to install
   or set up the runtime is its own setup_hint, an instruction to run and check
   the implementation its own check_hint, and neither rides along inside
   another intervention, because a later version withdraws setup and checking
   help first and must be able to do so without losing the rest.
   An intervention already in catalog.json that fits is proposed by its id
   (reuses) rather than written again; one that was built into a version that
   then failed on this same bottleneck is not proposed again unless you say
   what differs.
4. kind is one of {", ".join(KINDS)}, by what changes in the package:
   setup_hint, check_hint, direction_hint, decomposition and feedback add
   words to the instruction only (an optional exercise, a worked plan, advice
   on what to check); smaller_instance makes the graded task itself smaller
   (fewer cases, smaller inputs the verifier uses); supplied_work puts finished
   work into the environment. The first five keep the original verifier
   byte for byte, so a version of them is graded exactly as the original is.
   scope_removal takes out a part the
   original requires (a forward pass, a gradient, a class): propose it only
   when catalog.json shows interventions of other kinds failed on this
   bottleneck, and say so. hint_level is none (structural change only), vague
   (a direction, no concrete steps) or specific (one trace-supported step).

Rules every intervention is held to:
- It never states the final answer, an exact patch, or the output the student
  should compute, and never reveals the verifier.
- Wording help is written as separate sentences that a later version can drop.
- Help about how to work (a table to write, a check to run, an order of steps)
  is advice the student may take, worded as advice ("It helps to ..."), never
  a requirement: the verifier grades only the result, so a required process it
  cannot check makes the version invalid. (Measured: v11's {{i3}}, written as
  "First write a shape table ...", was given up by its author for that reason.)
{extra}
Write diagnosis.json in the current directory and read it back:
{{"bottleneck": {{"name": "...", "description": "...", "attempts_hit": N,
  "attempts_read": M, "evidence": [{{"attempt": "<file name in attempts/>",
  "turn": T, "observation": "..."}}]}},
 "other_bottlenecks": [{{"name": "...", "attempts_hit": N}}],
 "candidates": [{{"name": "...", "kind": "...", "hint_level": "...",
  "spec": "the change to the original, exactly", "mechanism": "why the
  failing decision changes", "prediction": "k of 12", "reuses": "<id or omit>"}}]}}
Do not change any other file.
"""


def run_diagnosis(co: Coordinator, work: Path) -> dict:
    """One codex session over ``work``; returns diagnosis.json."""
    ec, so = co.ev.ec, co.ev.ec.so
    sd = layout.SessionDir(work / "session")
    sd.path.mkdir(mode=0o700)
    sd.codex_home.mkdir(mode=0o700)
    prompt = diagnose_prompt(so.extra_guidance(), co.policy.candidates)
    sd.prompt.write_text(prompt)
    cmd = ec._codex_cmd(work, ec.CODEX_MODEL)
    env = ec._codex_env(sd)
    with sd.stdout.open("w") as out, sd.stderr.open("w") as err:
        proc = subprocess.run(cmd, input=prompt, stdout=out, stderr=err, text=True, cwd=str(work), env=env,
                              timeout=co.policy.diagnose_timeout, check=False)
    if ec.ACCOUNT_HOME:
        (sd.codex_home / "auth.json").unlink(missing_ok=True)
    path = work / "diagnosis.json"
    if not path.is_file():
        raise RuntimeError(f"diagnosis wrote no diagnosis.json (exit {proc.returncode})")
    return json.loads(path.read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", type=Path)
    ap.add_argument("--workers", type=int, default=16)
    for f, default in Policy.__dataclass_fields__.items():
        ap.add_argument(f"--{f.replace('_', '-')}", type=type(default.default), default=default.default)
    ap.add_argument("--tick", type=float, default=5.0)
    args = ap.parse_args()
    policy = Policy(**{f: getattr(args, f) for f in Policy.__dataclass_fields__})
    job = args.job.resolve()
    os.environ.setdefault("TRL_BASE", str(job))
    root = layout.Root(job)
    ev = _evolve()
    import grader_replay  # noqa: PLC0415 -- on the path _evolve() set

    ev.EASIER_GRADER_REPLAY = grader_replay.enabled(default="1")
    budget = ev.ec.rewrite_budget_sec(root, refresh=True)
    if budget <= ev.ec.FINAL_VALIDATION_RESERVE_SEC:
        raise SystemExit(f"rewrite budget {budget:.0f}s does not exceed the final-validation reserve")
    tool_bin = ev.rw_workspace.prepare_tool_bin(root)
    if tool_bin != root.bin:
        os.environ[ev.rw_workspace.TOOL_BIN_ENV] = str(tool_bin)
    _lock_fd = ev.acquire_singleton(root.evolution.loop_lock)  # noqa: F841 -- held for life
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(root.evolution.loop_log)])
    published = ev.rw_workspace.recover(root)
    recovered = ev.finalize_interrupted(root, include=published)
    if recovered["failed"]:
        raise RuntimeError(f"could not finalize orphaned rewrites: {recovered}")
    log.info("interventions up: pid=%d root=%s workers=%d policy=%s", os.getpid(), root.path, args.workers, policy)
    holder: dict = {}
    client = RewriterClient(job, which_version=lambda c: holder["co"].which_version(c) if "co" in holder else None)
    client.connect()
    co = Coordinator(root, client, ev, policy=policy, workers=args.workers)
    holder["co"] = co

    def stop(signum, _frame):
        log.info("policy stop: signal %d; tearing down the job's sandbox servers", signum)
        try:
            co.sweeper.sweep(everything=True, why="rewriter stopping")
        finally:
            logging.shutdown()
            os._exit(128 + signum)

    for s in (signals.SIGTERM, signals.SIGINT, signals.SIGHUP):
        signals.signal(s, stop)
    co.start()
    co.run(args.tick)


if __name__ == "__main__":
    main()
