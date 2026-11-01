#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""The teacher rewriter: one codex thread runs the curriculum of a run.

    teacher_rewriter.py <job> --prepare     # before training: round 1, its versions seeded into the pool
    teacher_rewriter.py <job>               # during training

The teacher is a codex session continued round after round in one working directory (teacher_agents.md
becomes its AGENTS.md). It is called before training and whenever a training group ends close to all
failed or all passed (at most --near attempts away), the groups that teach nothing; events that arrive
while it works are kept for its next round. Each round it reads the directory this process keeps:
every version of the pool in full, every training attempt of the run, a summary and its own history.
It writes plan.json and any new versions under new/. This process checks each new version on Daytona
(its reference solution passes its tests; its untouched state fails them), adds those that pass to the
pool, retires and reweights as planned, and records in history.md what each version then scored.

What the teacher sees is the task and this run, nothing else (README.md, "Two loops"): never the
readout, never another run's records. The pool holds at most --max-active versions. It uses only the
bench interface: the socket (add, reweight, retire, and the which_version hook, which serves the
versions the teacher's last plan put in draw_first, each once, before the weighted draw) and
`bench.py seed` before training.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import re
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
from torchtitan.experiments.rl.examples.tmax.overfit_bench.rewriter_client import (  # noqa: E402
    RewriterClient,
    RewriterError,
)

log = logging.getLogger("teacher")
HERE = Path(__file__).resolve().parent
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
TEST_LINE = re.compile(r"^(PASSED|FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$", re.M)
PACKAGE = ("instruction.md", "environment", "tests", "solution")


@dataclass(frozen=True, kw_only=True)
class Policy:
    near: int = 1
    max_active: int = 8
    round_timeout: int = 2400
    check_timeout: int = 1800
    output_chars: int = 4000
    workers: int = 8


def recipe() -> dict[str, str]:
    out = {}
    for line in (HERE / "recipe.env").read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() and not k.lstrip().startswith("#"):
            out[k.strip()] = v.strip()
    return out


def agents_md(policy: Policy, sandbox: dict) -> str:
    """``sandbox``: the original row's daytona_* fields (SANDBOX), the box every attempt runs in."""
    r = recipe()
    return (HERE / "teacher_agents.md").read_text().format(
        slots=r.get("SWE_MAX_ACTIVE_GROUPS", "?"), group=r.get("SWE_GROUP_SIZE", "?"),
        steps=r.get("SWE_TRAIN_STEPS", "?"), turns=r.get("SWE_BENCH_MAX_TURNS", "?"),
        context=r.get("SWE_MAX_CONTEXT_LEN", "?"),
        hours=f"{int(r.get('SWE_BENCH_TRAIN_BUDGET_SEC', '0')) / 3600:g}",
        stale=r.get("SWE_OFFPOLICY_STEPS", "?"), val_every=r.get("SWE_VAL_INTERVAL", "?"),
        cap=policy.max_active, cpu=sandbox.get("daytona_cpu") or "?",
        mem=sandbox.get("daytona_mem_gb") or "?", disk=sandbox.get("daytona_disk_gb") or "?")


def verifier_output(head: dict) -> str:
    """The verifier's output for a training attempt: its grade, or the grade run for the record on an
    attempt that did not submit (verifier.shadow)."""
    v = head.get("verifier") or {}
    return v.get("output_tail") or (v.get("shadow") or {}).get("output_tail") or ""


def test_results(output: str) -> dict[str, str]:
    """test id -> "PASSED" or "FAILED: <error>", from the verifier's per-test summary lines."""
    out = {}
    for verdict, test, err in TEST_LINE.findall(output):
        out[test.split("::")[-1]] = "PASSED" if verdict == "PASSED" else f"{verdict}: {err}".rstrip(": ")
    return out


def compact_attempt(src: Path, label: str, chars: int) -> str:
    """A training record as the teacher reads it: a summary line, then one line per turn."""
    lines = src.read_text().splitlines()
    head = json.loads(lines[0])
    summary = {"problem": label, "group": head.get("group"), "attempt": head.get("rollout"),
               "reward": head.get("reward"), "end": head.get("finish_reason"), "submitted": head.get("submitted"),
               "turns": head.get("turns"), "tests": test_results(verifier_output(head))}
    if not summary["tests"]:
        summary["verifier_output"] = verifier_output(head)[-chars:]
    out = [json.dumps(summary, ensure_ascii=False)]
    for line in lines[1:]:
        try:
            t = json.loads(line)
        except ValueError:
            continue
        o = t.get("output") or ""
        if len(o) > chars:
            o = o[: chars // 2] + f"\n[... {len(o) - chars} characters cut ...]\n" + o[-chars // 2:]
        keys = t.get("keystrokes")
        out.append(json.dumps({"turn": t.get("turn"), "think": t.get("think"), "analysis": t.get("analysis"),
                               "plan": t.get("plan"),
                               "keystrokes": "".join(keys) if isinstance(keys, list) else keys,
                               "output": o}, ensure_ascii=False))
    return "\n".join(out) + "\n"


def check_plan(plan: dict, work: Path) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("plan.json is not an object")
    weights = plan.get("weights") or {}
    if not isinstance(weights, dict) or not all(isinstance(w, (int, float)) and w >= 0 for w in weights.values()):
        raise ValueError("weights must map labels to numbers >= 0")
    retire = plan.get("retire") or []
    if not isinstance(retire, list) or not all(isinstance(x, str) for x in retire):
        raise ValueError("retire must be a list of labels")
    new = []
    first = plan.get("draw_first") or []
    if not isinstance(first, list) or not all(isinstance(x, str) for x in first):
        raise ValueError("draw_first must be a list of labels or new version names")
    for item in plan.get("new") or []:
        d = (work / str(item.get("dir", ""))).resolve()
        if d.parent != (work / "new").resolve() or not NAME.match(d.name):
            raise ValueError(f"new version {item.get('dir')!r} must be a directory directly under new/")
        missing = [p for p in PACKAGE if not (d / p).exists()]
        if missing:
            raise ValueError(f"{item['dir']} lacks {', '.join(missing)}")
        new.append({"name": d.name, "dir": d, "weight": float(item.get("weight", 1.0)),
                    "predict": str(item.get("predict", ""))[:200], "why": str(item.get("why", ""))[:800]})
    return {"assessment": str(plan.get("assessment", ""))[:6000], "weights": weights, "retire": retire, "new": new,
            "draw_first": first}


SANDBOX = {"cpu": "daytona_cpu", "mem-gb": "daytona_mem_gb", "disk-gb": "daytona_disk_gb"}


def daytona_check(package: Path, *, shortcut: str | None, timeout: int, sandbox: dict) -> dict:
    """evolution/daytona_revalidate.py: the package's reference solution (shortcut None) or a command,
    then its verifier, on a fresh Daytona sandbox sized as training sizes it (``sandbox``: the original
    row's daytona_* fields; the harness default is too small to install the verifier's torch); the
    verdict is its last stdout line."""
    tool = HERE.parent / "evolution" / "daytona_revalidate.py"
    cmd = [sys.executable, str(tool), str(package)] + (["--shortcut", shortcut] if shortcut else [])
    for flag, key in SANDBOX.items():
        if sandbox.get(key) is not None:
            cmd += [f"--{flag}", str(sandbox[key])]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    try:
        return json.loads((p.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        return {"error": (p.stderr or p.stdout)[-400:]}


def reward_of(verdict: dict) -> float | None:
    for k in ("reward", "score"):
        if isinstance(verdict.get(k), (int, float)):
            return float(verdict[k])
    return None


class Teacher:
    """``ask(teacher, prompt)`` runs one round of the codex thread (run_codex by default);
    ``check(package, shortcut=, timeout=, sandbox=)`` one Daytona verdict."""

    def __init__(self, root: layout.Root, client: RewriterClient | None, ev, *, policy: Policy = Policy(),
                 ask=None, check=daytona_check) -> None:
        self.root, self.client, self.ev, self.policy = root, client, ev, policy
        self.ask, self.check = ask or run_codex, check
        self.dir = root.evolution.path / "teacher"
        self.work = self.dir / "work"
        for d in ("problems", "attempts", "new"):
            (self.work / d).mkdir(parents=True, exist_ok=True)
        path = self.dir / "state.json"
        self.state = json.loads(path.read_text()) if path.is_file() else {"rounds": [], "versions": {}}
        self.task_id = json.loads((root.path / "job.json").read_text())["task"]
        self.versions: dict[str, dict] = {}
        self.groups: dict[str, list[dict]] = collections.defaultdict(list)
        self.attempts: dict[str, list[dict]] = collections.defaultdict(list)
        self.steps = 0
        # A resumed run is a new run directory whose groups count from 0 again; the teacher numbers
        # groups across the whole training, each run's after the highest group of the runs before it.
        self._runs: dict[str, int] = {}  # run -> highest group seen in it
        self._seen: set[tuple] = set()
        self.pending: list[str] = []  # why the teacher should be called, oldest first
        self.news: list[str] = []
        self._lock = threading.Lock()
        self._ex = ThreadPoolExecutor(policy.workers, thread_name_prefix="teacher")
        self._futs: dict[Future, tuple[str, str]] = {}

    def _save(self) -> None:
        layout.write_json_atomic(self.dir / "state.json", self.state)

    def label(self, vid: str) -> str:
        v = self.versions.get(vid)
        return f"r{v['rev']}" if v else vid[:12]

    def by_label(self, label: str) -> dict | None:
        return next((v for v in self.versions.values() if f"r{v['rev']}" == label), None)

    # -- the pool and what training did ---------------------------------------------

    def refresh(self) -> None:
        if self.client is None:
            pool = self.root.mix.path / "pool.json"
            self.versions = {v["id"]: v for v in json.loads(pool.read_text())["versions"]} if pool.is_file() else {}
            if not self.versions:
                row = json.loads(self.root.mix.read_live().splitlines()[0])
                self.versions = {"original": {"id": "original", "rev": 0, "task": self.task_id, "status": "active",
                                              "weight": 1.0, "row": row}}
            return
        rows = {vid: v.get("row") for vid, v in self.versions.items()}
        state = self.client.get_state(with_rows=True)
        self.versions = {v["id"]: v for v in state["versions"]}
        for vid, v in self.versions.items():
            if v.get("row") is None:
                v["row"] = rows.get(vid)

    def on_event(self, e: dict) -> None:
        kind, vid = e.get("event"), e.get("version")
        if kind == "step_done":
            self.steps = max(self.steps, int(e.get("step") or 0))
            return
        if not vid or kind not in ("rollout_done", "group_done"):
            return
        key = (kind, e.get("run"), vid, e.get("group"), e.get("idx"))
        if key in self._seen:
            return
        self._seen.add(key)
        group = self._group(e.get("run"), int(e.get("group") or 0))
        if kind == "rollout_done":
            self.attempts[vid].append({"run": e.get("run"), "path": e["record_path"], "group": group,
                                       "idx": e.get("idx"), "reward": e.get("reward"), "end": e.get("finish_reason")})
            return
        if not e.get("scored"):
            return
        g = {"group": group, "passed": int(e["passed"]), "scored": int(e["scored"]), "at": e.get("stamp")}
        self.groups[vid].append(g)
        label = self.label(vid)
        self.news.append(f"{label} g{g['group']} finished: {g['passed']}/{g['scored']} passed")
        near = self.policy.near
        if g["passed"] <= near or g["passed"] >= g["scored"] - near:
            self.pending.append(f"{label}'s group g{g['group']} ended {g['passed']}/{g['scored']}")

    def _group(self, run: str | None, group: int) -> int:
        run = run or ""
        offset = 0
        for r, top in self._runs.items():
            if r == run:
                break
            offset += top + 1
        self._runs[run] = max(self._runs.get(run, -1), group)
        return offset + group

    def which_version(self, candidates: list[dict]) -> str | None:
        """The next version the teacher's draw_first names that is still active; else the weighted draw."""
        with self._lock:
            queue = self.state.setdefault("draw_first", [])
            active = {f"r{c['rev']}": c["id"] for c in candidates}
            while queue:
                label = queue.pop(0)
                if label in active:
                    layout.write_json_atomic(self.dir / "state.json", self.state)
                    return active[label]
            return None

    # -- the teacher's directory --------------------------------------------------------

    def stage(self) -> None:
        w = self.work
        (w / "AGENTS.md").write_text(agents_md(self.policy, self._sandbox()))
        task = self.root.evolution.task(self.task_id)
        r0 = task.rev(0)
        if not r0.exists():
            r0 = self.ev._ensure_r0(self.root, task, self.task_id)
        for vid, v in self.versions.items():
            label = f"r{v['rev']}"
            d = w / "problems" / label
            if not d.exists():
                src = task.rev(int(v["rev"])) if int(v["rev"]) else r0
                # The whole task package (task.toml included: the row is built from it), less the
                # rewrite pipeline's own run/ and traces/.
                shutil.copytree(src, d, ignore=shutil.ignore_patterns("run", "traces", ".gitignore"))
                if int(v["rev"]):
                    diff = subprocess.run(["diff", "-ruN", "--exclude=.*", "--exclude=status.json",
                                           "--exclude=diff.patch", "../r0", "."], cwd=d,
                                          capture_output=True, text=True, check=False).stdout
                    (d / "diff.patch").write_text(diff)
            info = self.state["versions"].get(label, {})
            layout.write_json_atomic(d / "status.json", {
                "label": label, "in_pool": v["status"] == "active", "weight": v.get("weight"),
                "from": info.get("from"), "made_in_round": info.get("round"), "your_prediction": info.get("predict"),
                "groups": [f"{g['passed']}/{g['scored']}" for g in self.groups[vid]]})
            for a in self.attempts[vid]:
                dst = w / "attempts" / label / f"g{a['group']}" / f"{int(a['idx']) + 1:02d}.jsonl"
                if dst.exists():
                    continue
                src = self.root.run(a["run"]).path / a["path"]
                try:
                    text = compact_attempt(src, label, self.policy.output_chars)
                except (OSError, ValueError, IndexError):
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(text)
        (w / "summary.md").write_text(self.summary())
        (w / "history.md").write_text(self.history())

    def summary(self) -> str:
        out = [f"# The pool ({sum(1 for v in self.versions.values() if v['status'] == 'active')} active of at most "
               f"{self.policy.max_active}); steps done: {self.steps}\n\n",
               "| version | in pool | weight | groups (passed/scored) | how attempts ended |\n|---|---|---|---|---|\n"]
        tests: dict[str, collections.Counter] = {}
        graded: collections.Counter = collections.Counter()
        for vid, v in sorted(self.versions.items(), key=lambda kv: kv[1]["rev"]):
            label = f"r{v['rev']}"
            ends = collections.Counter(a["end"] for a in self.attempts[vid])
            out.append(f"| {label} | {'yes' if v['status'] == 'active' else 'no'} | {float(v.get('weight') or 0):.3g} | "
                       + (" ".join(f"{g['passed']}/{g['scored']}" for g in self.groups[vid]) or "none yet") + " | "
                       + (", ".join(f"{k} {n}" for k, n in ends.most_common()) or "-") + " |\n")
            adir = self.work / "attempts" / label
            for f in sorted(adir.glob("g*/*.jsonl")) if adir.is_dir() else []:
                try:
                    res = json.loads(f.open().readline()).get("tests") or {}
                except (OSError, ValueError):
                    continue
                if res:
                    graded[label] += 1
                    c = tests.setdefault(label, collections.Counter())
                    c.update({t: int(r == "PASSED") for t, r in res.items()})
        if tests:
            out.append("\n## Attempts that passed each test (of the graded attempts)\n\n")
            for label, c in tests.items():
                out.append(f"- **{label}** ({graded[label]} graded): "
                           + ", ".join(f"{t} {n}" for t, n in sorted(c.items())) + "\n")
        return "".join(out)

    def history(self) -> str:
        out = ["# Your rounds, oldest first\n"]
        for r in self.state["rounds"]:
            out.append(f"\n## Round {r['n']} ({r['at']}), called because: {r['why']}\n")
            out.append(f"Your assessment: {r.get('assessment', '')}\n")
            out.append(f"What was done: {r.get('done', '') or 'nothing'}\n")
            for name, made in (r.get("made") or {}).items():
                v = self.by_label(made["label"]) if made.get("label") else None
                after = " ".join(f"{g['passed']}/{g['scored']}" for g in self.groups[v["id"]]) if v else ""
                out.append(f"- `{name}` -> {made.get('label') or made.get('outcome')}: you predicted "
                           f"{made.get('predict') or '-'}; its groups since: {after or 'none yet'}\n")
        return "".join(out)

    # -- one round ---------------------------------------------------------------------

    def start_round(self) -> None:
        n = len(self.state["rounds"]) + 1
        why = "; ".join(self.pending) if self.pending else "training has not started; the student has made no attempt yet"
        news, self.pending, self.news = self.news, [], []
        self.stage()
        # A resumed thread keeps the AGENTS.md it started with; tell it when the file has changed since.
        agents = (self.work / "AGENTS.md").read_text()
        if self.state.get("agents_md") not in (None, agents):
            news.append("AGENTS.md has changed since your last round; read it again")
        self.state["agents_md"] = agents
        shutil.rmtree(self.work / "new", ignore_errors=True)
        (self.work / "new").mkdir()
        (self.work / "plan.json").unlink(missing_ok=True)
        prompt = (f"Round {n}. You are called because {why}.\n"
                  + ("Since your last round:\n" + "".join(f"- {x}\n" for x in news[-40:]) if news else "")
                  + "The directory is up to date (summary.md, history.md, problems/, attempts/). "
                  "Read what you need, then write plan.json, and any new versions under new/.\n")
        log.info("teacher round %d: %s", n, why)
        self.state["rounds"].append({"n": n, "at": layout.stamp(), "why": why, "status": "running"})
        self._save()
        fut = self._ex.submit(self._round, prompt)
        self._futs[fut] = ("round", str(n))

    def _round(self, prompt: str) -> dict:
        self.ask(self, prompt)
        path = self.work / "plan.json"
        if not path.is_file():
            raise RuntimeError("no plan.json was written")
        return json.loads(path.read_text())

    def on_round(self, n: int, raw: dict) -> None:
        r = self.state["rounds"][n - 1]
        try:
            plan = check_plan(raw, self.work)
        except ValueError as e:
            r.update({"status": "refused", "done": f"plan not carried out: {e}"})
            self.pending.append(f"your round {n} plan was not carried out: {e}")
            self._save()
            return
        done = []
        for label in plan["retire"]:
            v = self.by_label(label)
            if v is None or v["status"] != "active":
                done.append(f"retire {label}: not an active version")
                continue
            self._call("retire", v["id"])
            v["status"] = "retired"
            done.append(f"retired {label}")
        for label, w in plan["weights"].items():
            v = self.by_label(label)
            if v is None or v["status"] != "active":
                done.append(f"weight {label}: not an active version")
                continue
            self._call("reweight", v["id"], float(w))
            v["weight"] = float(w)
            done.append(f"{label} weight {w:g}")
        # draw_first replaces the queue; a new version's name stands for its label once it joins.
        names = {m["name"] for m in plan["new"]}
        with self._lock:
            self.state["draw_first"] = [x for x in plan["draw_first"] if x not in names]
        r["draw_first_pending"] = [x for x in plan["draw_first"] if x in names]
        r["made"] = {}
        for item in plan["new"]:
            dest = self.dir / "built" / f"{n:03d}-{item['name']}"
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(item["dir"], dest, ignore=shutil.ignore_patterns(".*"))
            r["made"][item["name"]] = {"predict": item["predict"], "why": item["why"], "weight": item["weight"],
                                       "outcome": "being checked"}
            fut = self._ex.submit(self.validate, dest)
            self._futs[fut] = ("version", f"{n}/{item['name']}")
            done.append(f"checking new version {item['name']}")
        r.update({"status": "done", "assessment": plan["assessment"], "done": "; ".join(done)})
        self._save()
        log.info("teacher round %d: %s", n, r["done"] or "no changes")

    def _call(self, op: str, *args) -> None:
        if self.client is None:
            return
        try:
            getattr(self.client, op)(*args)
        except RewriterError as e:
            log.warning("%s %s not applied: %s", op, args, e)

    # -- new versions ------------------------------------------------------------------

    def _sandbox(self) -> dict:
        original = next(v for v in self.versions.values() if int(v["rev"]) == 0)
        return {k: original["row"]["metadata"].get(k) for k in SANDBOX.values()}

    def validate(self, package: Path) -> dict:
        sandbox = self._sandbox()
        solved = self.check(package, shortcut=None, timeout=self.policy.check_timeout, sandbox=sandbox)
        if reward_of(solved) != 1.0:
            return {"ok": False, "package": str(package),
                    "reason": f"its reference solution did not pass its tests: {json.dumps(solved)[:300]}"}
        bare = self.check(package, shortcut="true", timeout=self.policy.check_timeout, sandbox=sandbox)
        if reward_of(bare) != 0.0:
            return {"ok": False, "package": str(package),
                    "reason": f"its untouched starting state did not fail its tests: {json.dumps(bare)[:300]}"}
        return {"ok": True, "package": str(package)}

    def on_version(self, key: str, result: dict) -> None:
        n, name = int(key.split("/")[0]), key.split("/", 1)[1]
        made = self.state["rounds"][n - 1]["made"][name]
        if not result.get("ok"):
            made["outcome"] = f"failed its checks: {result.get('reason', '')}"
            self.pending.append(f"your new version {name} (round {n}) failed its checks")
            self.news.append(f"new version {name} failed its checks: {result.get('reason', '')[:300]}")
            self._save()
            return
        if sum(1 for v in self.versions.values() if v["status"] == "active") >= self.policy.max_active:
            made["outcome"] = f"passed its checks, but the pool was full ({self.policy.max_active} active)"
            self.pending.append(f"your new version {name} passed its checks but the pool is full")
            self._save()
            return
        task = self.root.evolution.task(self.task_id)
        original = next(v for v in self.versions.values() if int(v["rev"]) == 0)
        rev = max(task.revs(), default=0) + 1
        shutil.copytree(result["package"], task.rev(rev))
        row = self.ev.candidate_row(task.rev(rev), original["row"], {"task": self.task_id, "input_rev": rev - 1})
        row["metadata"]["rev"] = rev
        label = f"r{rev}"
        self.state["versions"][label] = {"from": name, "round": n, "predict": made["predict"]}
        if self.client is None:
            made.update({"outcome": "seeded", "label": label, "row": row})
            if name in self.state["rounds"][n - 1].get("draw_first_pending", []):
                with self._lock:
                    self.state.setdefault("draw_first", []).append(label)
        else:
            info = self.client.add(row, weight=made["weight"], parent=original["id"])
            self.versions[info["id"]] = {**info, "row": row}
            made.update({"outcome": "in the pool", "label": label})
            if name in self.state["rounds"][n - 1].get("draw_first_pending", []):
                with self._lock:
                    self.state.setdefault("draw_first", []).append(label)
            self.news.append(f"new version {name} joined the pool as {label}")
            layout.append_jsonl(task.lineage, {"stamp": layout.stamp(), "event": "fold", "from_rev": 0,
                                               "to_rev": rev, "version": info["id"], "teacher": name})
        self._save()
        log.info("new version %s (round %d) is %s", name, n, label)

    # -- driving -----------------------------------------------------------------------

    def collect(self) -> None:
        for fut in [f for f in self._futs if f.done()]:
            kind, key = self._futs.pop(fut)
            try:
                result = fut.result()
            except Exception as e:  # noqa: BLE001 -- one session or check, not the loop
                log.exception("%s %s raised: %s", kind, key, e)
                if kind == "round":
                    r = self.state["rounds"][int(key) - 1]
                    r.update({"status": "failed", "done": f"{type(e).__name__}: {e}"[:300]})
                    self.pending.append(f"your round {key} ended without a plan ({type(e).__name__})")
                    self._save()
                    continue
                result = {"ok": False, "reason": f"{type(e).__name__}: {e}"[:300]}
            if kind == "round":
                self.on_round(int(key), result)
            else:
                self.on_version(key, result)

    def round_running(self) -> bool:
        return any(kind == "round" for kind, _ in self._futs.values())

    def start(self) -> None:
        self.refresh()
        for run in self.root.run_dirs():
            for e in layout.read_jsonl(run.scheduler / "events.jsonl"):
                self.on_event({**e, "run": e.get("run") or run.name})
        self.news = []
        self.pending = [] if self.state["rounds"] else ["training has started and no round has run yet"]

    def step(self, timeout: float) -> None:
        e = self.client.wait_for({"rollout_done", "group_done", "step_done"}, timeout=timeout)
        while e is not None:
            self.on_event(e)
            e = self.client.wait_for({"rollout_done", "group_done", "step_done"}, timeout=0)
        self.collect()
        if self.pending and not self.round_running():
            self.refresh()
            self.start_round()

    def run(self, tick: float = 5.0) -> None:
        while True:
            if not self.client.connected:
                try:
                    self.client.connect()
                except RewriterError as e:
                    log.warning("%s", e)
                    continue
            try:
                self.step(tick)
            except RewriterError as e:
                log.warning("scheduler call failed: %s", e)

    def prepare(self) -> list[dict]:
        """Round 1 before training; returns the rows to seed: the original and every new version that
        passed its checks."""
        self.refresh()
        self.start_round()
        while self._futs:
            time.sleep(2)
            self.collect()
        original = next(v for v in self.versions.values() if int(v["rev"]) == 0)
        rows = [original["row"]]
        for made in self.state["rounds"][0].get("made", {}).values():
            if made.get("row"):
                rows.append(made.pop("row"))
        self._save()
        return rows


def run_codex(teacher: Teacher, prompt: str) -> None:
    """One round of the teacher's codex thread: the first round starts it, later rounds resume it. Its
    CODEX_HOME is the teacher's own, kept across rounds so the thread can be resumed; each round's
    prompt and output are kept under rounds/."""
    ec = teacher.ev.ec
    home = layout.SessionDir(teacher.dir / "thread")
    home.codex_home.mkdir(parents=True, exist_ok=True)
    if ec.ACCOUNT_HOME:
        (home.codex_home / "auth.json").unlink(missing_ok=True)
    env = ec._codex_env(home)
    sid = teacher.state.get("thread")
    cmd = ec._codex_cmd(teacher.work, ec.CODEX_MODEL, resume=sid)
    rd = teacher.dir / "rounds" / f"{len(teacher.state['rounds']):03d}"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "prompt.md").write_text(prompt)
    for attempt in range(6):
        with (rd / f"stdout-{attempt}.txt").open("w") as out, (rd / f"stderr-{attempt}.txt").open("w") as err:
            p = subprocess.run(cmd, input=prompt, stdout=out, stderr=err, text=True, cwd=str(teacher.work), env=env,
                               timeout=teacher.policy.round_timeout, check=False)
        if p.returncode == 0:
            break
        text = (rd / f"stdout-{attempt}.txt").read_text(errors="replace") + (rd / f"stderr-{attempt}.txt").read_text(errors="replace")
        if ec._agent_failure_type(text, "").startswith("provider_") or "at capacity" in text:
            # The provider cut the thread off; continue it rather than lose the round.
            time.sleep(min(120 * 2 ** attempt, 900))
            sid = sid or _thread_id(ec, home)
            cmd = ec._codex_cmd(teacher.work, ec.CODEX_MODEL, resume=sid)
            prompt = "The model provider cut your session off; nothing you did caused it. Continue where you stopped."
            continue
        raise RuntimeError(f"codex exited {p.returncode}; see {rd}")
    if ec.ACCOUNT_HOME:
        (home.codex_home / "auth.json").unlink(missing_ok=True)
    if not teacher.state.get("thread"):
        teacher.state["thread"] = _thread_id(ec, home)
        teacher._save()


def _thread_id(ec, home: layout.SessionDir) -> str | None:
    try:
        return ec._session_id(home)
    except RuntimeError:
        return None


def _evolve():
    evolution = HERE.parent / "evolution"
    if str(evolution) not in sys.path:
        sys.path.insert(0, str(evolution))
    import evolve_ondella as ev  # noqa: PLC0415

    return ev


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", type=Path)
    ap.add_argument("--prepare", action="store_true", help="before training: round 1, seeded into the pool")
    for f, default in Policy.__dataclass_fields__.items():
        ap.add_argument(f"--{f.replace('_', '-')}", type=type(default.default), default=default.default)
    ap.add_argument("--tick", type=float, default=5.0)
    args = ap.parse_args()
    policy = Policy(**{f: getattr(args, f) for f in Policy.__dataclass_fields__})
    job = args.job.resolve()
    os.environ.setdefault("TRL_BASE", str(job))
    root = layout.Root(job)
    ev = _evolve()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(root.evolution.loop_log)])
    _lock = ev.acquire_singleton(root.evolution.loop_lock)  # noqa: F841 -- held for life
    if args.prepare:
        teacher = Teacher(root, None, ev, policy=policy)
        rows = teacher.prepare()
        rows_file = teacher.dir / "seed-rows.jsonl"
        rows_file.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        from torchtitan.experiments.rl.examples.tmax.overfit_bench import bench  # noqa: PLC0415
        result = bench.seed(job, rows_file)
        log.info("prepared: %d new version(s) seeded: %s", len(rows) - 1, result)
        print(json.dumps({"seeded_new_versions": len(rows) - 1}, default=str))
        return
    log.info("teacher up: pid=%d root=%s policy=%s", os.getpid(), root.path, policy)
    holder: dict = {}
    client = RewriterClient(job, which_version=lambda c: holder["t"].which_version(c) if "t" in holder else None)
    client.connect()
    teacher = Teacher(root, client, ev, policy=policy)
    holder["t"] = teacher

    def stop(signum, _frame):
        log.info("teacher stop: signal %d", signum)
        logging.shutdown()
        os._exit(128 + signum)

    for s in (signals.SIGTERM, signals.SIGINT, signals.SIGHUP):
        signals.signal(s, stop)
    teacher.start()
    teacher.run(args.tick)


if __name__ == "__main__":
    main()
