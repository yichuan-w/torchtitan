#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""EvolveBench chain mode: rewrite, measure, rewrite again, for up to 3 hops (#319).

A chain is a list of tasks, one student and one recipe. Each arm walks every
task of the chain through hops. A hop is one accepted rewrite of the task's
current revision and one k=12 measurement of the result:

    12/12 -> the next hop rewrites harder
    0/12  -> the next hop rewrites easier
    mixed -> the chain stops for that task at this hop

The next hop's traces are this hop's measurement attempts
(eval_signal_run.py turns them into a signal). Before entering, each task's
original is measured once at k=12 with the chain's student; that entry
measurement is not a hop.

Layout, under <bench>/chains/<chain>/:

    chain.json            student, recipe, strata (task -> allowed entry directions),
                          fill seed, package sources, arms
    mix.jsonl, sources/   the candidates' seed rows and a copy of each package
    evalsets/entry.jsonl  the originals still needing an entry measurement
    evals/                entry evals (and derived views of combined ones)
    entry.json            each candidate's entry measurement and admission
    hops/hop<h>/          the combined evalset of every arm's hop-h revisions
                          (rows prefixed <ARM>__) and its evals

The admitted tasks are frozen as an ordinary set (<bench>/sets/<chain>), so hop 1
is `evolve_bench.py run` of each arm on it. Every later state is read off disk:
the signal a hop answered (its rev), the rewrite that consumed it
(rewrite.json input_rev/result_rev), and the hop eval records at the result rev.

    evolve_chain.py build      candidates -> chain dir, seed mix, package copies, entry evalset
    evolve_chain.py split-eval a combined eval -> one prefix's derived eval with plain task ids
    evolve_chain.py admit      entry evals -> entry.json and the frozen set
    evolve_chain.py launch     start hop h's drive in every arm root
    evolve_chain.py hop-evalset  every arm's accepted hop-h revisions -> one combined evalset
    evolve_chain.py hop-collect  the combined hop-h eval -> per-arm evals and hop-(h+1) signals
    evolve_chain.py audit-bundle blinded bundle of the hop-h rewrites (evolve_bench.py audit-run scores it)
    evolve_chain.py report     per-arm hop counts, stubborn and oscillation rates, per-case table
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_EVO = Path(__file__).resolve().parent
sys.path.insert(0, str(_EVO))
import evolve_bench as eb  # noqa: E402
import layout  # noqa: E402

HOPS = 3
K = 12
# A measurement with fewer graded attempts decides nothing; the task is measured again.
MIN_GRADED = 10
DIRECTIONS = ("easier", "harder")
# The chain's student and the recipe its entry and hop evals run under
# (bench_cluster_eval.py stage --recipe/--checkpoint), at k=12.
STUDENTS = {
    "122b": {"checkpoint": "base-Qwen3.5-122B-A10B", "recipe": "train-111c-122b"},
    "9b": {"checkpoint": "base-Qwen3.5-9B", "recipe": "train-2026-10-25"},
}
# Per-hop outcome classes (EVOLVE_BENCH.md, Standard 2026-11-06): a mixed result
# (1-11 of 12) is clean_mixed when the hop's blind audit, after its verify step,
# confirms no defect. 3-8 is not reported.
HOP_CLASSES = ("clean_mixed", "mixed_defect", "mixed_unaudited", "12/12", "0/12", "not_accepted", "unmeasured")
TIME_LIMIT_REASONS = {"hit_time_budget"}


def band(passed: int, graded: int, k: int = K) -> str:
    """12/12, 0/12 or mixed over the graded attempts; too few graded attempts is ungraded."""
    if graded < MIN_GRADED:
        return "ungraded"
    if passed == graded:
        return "12/12"
    return "0/12" if passed == 0 else "mixed"


def next_direction(b: str) -> str | None:
    return {"12/12": "harder", "0/12": "easier"}.get(b)


def _chain_dir(bench: Path, chain: str) -> Path:
    return bench / "chains" / chain


def _load_chain(bench: Path, chain: str) -> tuple[Path, dict]:
    d = _chain_dir(bench, chain)
    return d, json.load(open(d / "chain.json"))


def _read_ids(spec: str) -> list[str]:
    """Ids from a file (one per line, or a JSON list / object) or a comma list."""
    p = Path(spec)
    if p.is_file():
        text = p.read_text()
        try:
            obj = json.loads(text)
            return list(obj) if isinstance(obj, (list, dict)) else [str(obj)]
        except ValueError:
            return [ln.split()[0] for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return [s for s in spec.split(",") if s]


# --------------------------------------------------------------------------- build


def build(a: argparse.Namespace) -> None:
    """Candidates -> chain dir. Strata are given as NAME:DIRS:IDS (DIRS easier,
    harder or both). The fill stratum is --fill-pool minus the named ids in a
    random.Random(--fill-seed) order (its first --fill-n when given); admit
    --cap takes the admitted fill tasks in that order."""
    out = _chain_dir(a.bench, a.chain)
    if out.exists():
        raise SystemExit(f"{out} exists; a chain is built once")
    strata: dict[str, dict] = {}
    order: list[str] = []
    for spec in a.stratum:
        name, dirs, rest = spec.split(":", 2)
        ids, n, seed = (rest.rsplit(":", 2) + [None, None])[:3] if rest.count(":") >= 2 else (rest, None, None)
        allowed = list(DIRECTIONS) if dirs == "both" else [dirs]
        if any(d not in DIRECTIONS for d in allowed):
            raise SystemExit(f"--stratum {spec}: directions are easier, harder or both")
        tids = [t for t in _read_ids(ids) if t not in order]
        strata[name] = {"directions": allowed, "tasks": tids, "from": ids}
        if n is not None:
            # A seeded sample of the listed ids (sorted first, so the draw does not depend on file order).
            pool = sorted(tids)
            tids = sorted(random.Random(int(seed)).sample(pool, min(int(n), len(pool))))
            strata[name].update(tasks=tids, sample={"n": int(n), "seed": int(seed), "pool": pool})
        order += tids
    fill = None
    if a.fill_pool:
        pool = sorted(set(_read_ids(a.fill_pool)) - set(order))
        rng = random.Random(a.fill_seed)
        drawn = pool[:]
        rng.shuffle(drawn)
        take = drawn[:a.fill_n] if a.fill_n else drawn
        name, dirs = a.fill_stratum.split(":", 1)
        allowed = list(DIRECTIONS) if dirs == "both" else [dirs]
        strata[name] = {"directions": allowed, "tasks": take}
        order += take
        fill = {"stratum": name, "pool": a.fill_pool, "pool_size": len(pool), "seed": a.fill_seed,
                "fill_n": a.fill_n, "shuffled_pool": drawn, "taken": take}
    rows_by_id: dict[str, tuple[dict, str]] = {}
    for mix in a.rows:
        for r in eb._rows(mix):
            rows_by_id.setdefault(eb._iid(r), (r, str(Path(mix).resolve())))
    rows, packages, missing = [], {}, []
    (out / "sources").mkdir(parents=True)
    for tid in order:
        if tid not in rows_by_id:
            missing.append(f"{tid}: no row")
            continue
        row, row_from = rows_by_id[tid]
        pkg = next((Path(s) / "tasks" / tid for s in a.sources if (Path(s) / "tasks" / tid).is_dir()), None)
        if pkg is None:
            missing.append(f"{tid}: no package")
            continue
        row_grader = (pkg / "tests" / "test.sh").read_text() != row["metadata"]["tmax"]["test_sh"]
        if row_grader and not a.row_grader:
            missing.append(f"{tid}: package grader is not the row's test_sh ({pkg})")
            continue
        corpus = pkg.parent.parent
        # Corpora of different releases share names; the release tag keeps them apart.
        tag = corpus.name if not a.tag_sources else f"{corpus.name}@{corpus.parent.parent.name[:8]}"
        dst = out / "sources" / tag / "tasks" / tid
        shutil.copytree(pkg, dst, symlinks=True)
        if row_grader:
            # The release builder rewrote this row's test_sh (the SWE-Rebench
            # find_repo patch); the row is what the student is graded by, so r0
            # carries it too, and freeze's package-is-the-row check holds.
            (dst / "tests" / "test.sh").write_text(row["metadata"]["tmax"]["test_sh"])
        packages[tid] = {"source": tag, "from": str(pkg.resolve()), "tree_sha256": eb.tree_sha256(dst),
                         "row_from": row_from, "test_sh_from_row": row_grader}
        rows.append(row)
    if missing and not a.allow_missing:
        shutil.rmtree(out)
        raise SystemExit("unresolved candidates:\n  " + "\n  ".join(missing))
    with open(out / "mix.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    have = {eb._iid(r) for r in rows}
    for s in strata.values():
        s["tasks"] = [t for t in s["tasks"] if t in have]
    st = STUDENTS[a.student]
    chain = {
        "name": a.chain, "created": layout.stamp(), "bench_commit": eb._bench_commit(),
        "student": a.student, **st, "k": K, "hops": HOPS, "min_graded": MIN_GRADED,
        "harness": a.harness, "strata": strata, "fill": fill, "unresolved": missing,
        "packages": packages, "mix_sha256": layout.sha256_file(out / "mix.jsonl"),
        "arms": {}, "note": a.note,
    }
    layout.write_json_atomic(out / "chain.json", chain)
    measured = set()
    for ev in a.measured or []:
        measured |= set(_eval_tasks(ev))
    entry_rows = [r for r in rows if eb._iid(r) not in measured]
    eb._write_evalset(out / "evalsets" / "entry.jsonl", entry_rows, student_harness=a.harness,
                      note=f"chain {a.chain}: originals needing an entry measurement")
    print(f"{out}: {len(rows)} candidates in {len(strata)} strata "
          f"({', '.join(f'{k} {len(v['tasks'])}' for k, v in strata.items())}); "
          f"{len(entry_rows)} need an entry measurement; unresolved {len(missing)}")


def _eval_tasks(ev: Path) -> dict[str, list[dict]]:
    """Each task's record headers in an eval directory (and its --attempt-* siblings)."""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for d in [ev, *sorted(ev.parent.glob(f"{ev.name}--attempt-*"))]:
        for p in sorted((d / "validation_rollouts").glob("*/*.jsonl")):
            if not eb.RECORD_NAME.fullmatch(p.name):
                continue
            with open(p) as fh:
                h = json.loads(fh.readline())
            h["_path"] = str(p)
            out[h["task"]].append(h)
    return out


def measure(headers: list[dict], rev: int | None = None) -> dict:
    """Pass and graded counts of one task's attempts (at ``rev`` when given):
    infra losses excluded, as eval_signal_run.py does."""
    hs = [h for h in headers if rev is None or int(h.get("rev") or 0) == rev]
    graded = [h for h in hs if not h.get("infra_failed")]
    passed = sum(1 for h in graded if (h.get("reward") or 0) >= 1)
    waits = []
    for h in graded:
        t = h.get("timing") or {}
        if isinstance(t, dict) and h.get("turns") and t.get("agent_secs") is not None:
            waits.append((t["agent_secs"] - (t.get("agent_exec_secs") or 0)) / h["turns"])
    return {"passed": passed, "graded": len(graded), "infra": len(hs) - len(graded), "band": band(passed, len(graded)),
            # an infrastructure finding, never difficulty: attempts stopped by the
            # wall clock, beside the per-turn generation wait
            "time_limit": sum(1 for h in graded if h.get("finish_reason") in TIME_LIMIT_REASONS),
            "gen_wait_per_turn": round(sum(waits) / len(waits), 1) if waits else None}


# ---------------------------------------------------------------------- split-eval


def split_eval(combined: Path, prefix: str, out: Path) -> Path:
    """One prefix's tasks of a combined eval, as an eval directory with the
    prefix taken off every task id: records (first line rewritten), index and
    an evalset of the plain rows, so freeze, eval_signal_run.py and the loop
    read it as an eval of these rows. The records the loop later reads as
    traces then name the task, not the arm that produced it."""
    if out.exists():
        raise SystemExit(f"{out} exists")
    launch = json.load(open(combined / "launch.json"))
    tag = f"{prefix}__"
    rows = [r for r in eb._rows(Path(launch["env"]["SWE_TB2_DATA"])) if eb._iid(r).startswith(tag)]
    plain = []
    for r in rows:
        r = json.loads(json.dumps(r))
        md = r["metadata"]
        md["instance_id"] = md.get("bench_orig_instance_id") or md["instance_id"][len(tag):]
        r["label"] = md["instance_id"]
        for k in ("bench_orig_instance_id", "bench_group"):
            md.pop(k, None)
        plain.append(r)
    out.mkdir(parents=True)
    m = eb._write_evalset(out / "evalset.jsonl", plain, student_harness=_harness_of(launch),
                          note=f"derived: prefix {prefix} of {combined}")
    n = 0
    dirs = [combined, *sorted(combined.parent.glob(f"{combined.name}--attempt-*"))]
    if launch.get("cluster", {}).get("local_output"):
        lo = Path(launch["cluster"]["local_output"])
        dirs += [lo, *sorted(lo.parent.glob(f"{lo.name}--attempt-*"))]
    seen: dict[str, Path] = {}
    for d in dirs:
        for p in sorted((d / "validation_rollouts").glob(f"{tag}*/*.jsonl")):
            if not eb.RECORD_NAME.fullmatch(p.name):
                continue
            tid = p.parent.name[len(tag):]
            rel = f"validation_rollouts/{tid}/{p.name}"
            if rel in seen:
                continue  # the collected copy and the job's own; the first listed wins
            seen[rel] = p
            lines = p.read_text().splitlines(keepends=True)
            head = json.loads(lines[0])
            head["task"] = tid
            lines[0] = json.dumps(head) + "\n"
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text("".join(lines))
            n += 1
    if (combined / eb.INDEX).exists() or (combined / eb.SALVAGED).exists():
        index = []
        for e in eb.eval_index(combined):
            if not str(e.get("task", "")).startswith(tag):
                continue
            e = dict(e)
            e["task"] = e["task"][len(tag):]
            for key in ("record", "path", "trial"):
                if isinstance(e.get(key), str):
                    e[key] = e[key].replace(tag, "")
            index.append(e)
        dst = out / eb.INDEX if (combined / eb.INDEX).exists() else out / eb.SALVAGED
        dst.parent.mkdir(parents=True, exist_ok=True)
        layout.write_json_atomic(dst, index)
    derived = dict(launch)
    derived["eval"] = out.name
    derived["env"] = {**launch["env"], "SWE_TB2_DATA": str((out / "evalset.jsonl").resolve())}
    derived["evalset_sha256"] = m["sha256"]
    derived["derived_from"] = {"eval": str(combined.resolve()), "prefix": prefix}
    layout.write_json_atomic(out / "launch.json", derived)
    print(f"{out}: {len(plain)} rows, {n} records from prefix {prefix} of {combined.name}")
    return out


def _harness_of(launch: dict) -> str:
    return eb._harness(launch) or "terminus"


# --------------------------------------------------------------------------- admit


def admit(a: argparse.Namespace) -> None:
    """Entry evals -> each candidate's entry band and admission, then the
    admitted tasks frozen as <bench>/sets/<chain> (signals = the entry attempts).
    The first eval listed that measured a task is its entry measurement."""
    d, chain = _load_chain(a.bench, a.chain)
    found: dict[str, tuple[Path, list[dict]]] = {}
    for ev in a.eval:
        for tid, hs in _eval_tasks(ev).items():
            found.setdefault(tid, (ev, hs))
    entry: dict[str, dict] = {}
    admitted_ids = []
    for sname, s in chain["strata"].items():
        for tid in s["tasks"]:
            if tid not in found:
                entry[tid] = {"stratum": sname, "admitted": False, "why": "not_measured"}
                continue
            ev, hs = found[tid]
            m = measure(hs, rev=0)
            direction = next_direction(m["band"])
            ok = direction in s["directions"]
            why = None if ok else (m["band"] if m["band"] in ("mixed", "ungraded")
                                   else f"{m['band']} (stratum takes {s['directions']})")
            entry[tid] = {"stratum": sname, **m, "eval": str(ev.resolve()), "admitted": ok,
                          "direction": direction if ok else None, "why": why}
            if ok:
                admitted_ids.append(tid)
    if a.cap and chain.get("fill"):
        fname = chain["fill"]["stratum"]
        room = a.cap - sum(1 for t in admitted_ids if entry[t]["stratum"] != fname)
        for t in chain["fill"]["taken"]:
            if entry.get(t, {}).get("admitted"):
                if room > 0:
                    room -= 1
                else:
                    entry[t].update(admitted=False, direction=None, why=f"over --cap {a.cap} (fill order)")
        admitted_ids = [t for t in admitted_ids if entry[t]["admitted"]]
    rows = [r for r in eb._rows(d / "mix.jsonl") if eb._iid(r) in set(admitted_ids)]
    adm_mix = d / "admitted.jsonl"
    with open(adm_mix, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    layout.write_json_atomic(d / "entry.json", {"created": layout.stamp(), "evals": [str(e.resolve()) for e in a.eval],
                                                "cap": a.cap, "tasks": entry})
    summary = collections.Counter((v["stratum"], v.get("band", "-"), v["admitted"]) for v in entry.values())
    for k_, n in sorted(summary.items()):
        print(f"  {k_[0]:12s} {k_[1]:12s} admitted={k_[2]} {n}")
    if a.dry_run:
        print(f"dry run: {len(admitted_ids)} would be admitted; nothing frozen")
        return
    refs = [r.resolve() for r in a.reference_first_group or []]
    if a.reference_eval:
        refs.append(reference_run(d, a.reference_eval, a.reference_band, set(admitted_ids)))
    eb.freeze(bench=a.bench, name=a.chain, run=None, evals=[e.resolve() for e in a.eval], first_group=[],
              selected=None, release=None, mix=adm_mix, sources=sorted((d / "sources").iterdir()),
              log=eb._log_for(a.bench, "chain-freeze"), recipe=chain["recipe"],
              reference_first_group=refs, reference_band=a.reference_band, reference_k=a.reference_k or None)


def reference_run(d: Path, evals: list[Path], band_: str, exclude: set[str]) -> Path:
    """The chain student's in-band eval groups as a training-run-shaped
    directory freeze --reference-first-group reads: one group per task that
    came out inside ``band_`` with every attempt graded, renumbered to a
    positive group id because EVOLVE_PEER_CONTEXT's record pattern (g<N>-r<i>)
    skips the negative ids evals use."""
    out = d / "reference-run"
    if out.exists():
        shutil.rmtree(out)
    lo, hi = (int(x) for x in band_.split("-"))
    (out / "inputs").mkdir(parents=True)
    (out / "rollouts").mkdir()
    shutil.copy2(d / "mix.jsonl", out / "inputs" / "mix.jsonl")
    n = 0
    for ev in evals:
        if not (out / "launch.json").exists():
            shutil.copy2(ev / "launch.json", out / "launch.json")
        for tid, hs in sorted(_eval_tasks(ev).items()):
            m = measure(hs, rev=0)
            if tid in exclude or m["infra"] or not lo <= m["passed"] <= hi or (out / "rollouts" / tid).exists():
                continue
            n += 1
            group = 900000 + n
            for i, h in enumerate(sorted(hs, key=lambda h: int(h.get("rollout") or 0))):
                lines = Path(h["_path"]).read_text().splitlines(keepends=True)
                head = json.loads(lines[0])
                head.update(group=group, rollout=i)
                lines[0] = json.dumps(head) + "\n"
                dst = out / "rollouts" / tid / f"g{group}-r{i}.jsonl"
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("".join(lines))
    print(f"{out}: {n} in-band ({band_}) reference groups from {[e.name for e in evals]}")
    return out


# ------------------------------------------------------------------- the hop state


def _rewrites(root: Path, tid: str) -> list[dict]:
    out = []
    for p in sorted((root / "evolution" / "tasks" / tid / "rewrites").glob("*/rewrite.json")):
        rw = json.load(open(p))
        rw["_dir"] = str(p.parent)
        out.append(rw)
    return out


def _hop_evals(root: Path) -> dict[int, list[Path]]:
    """The arm's derived hop evals, evals/chain-hop<h>--*, oldest first."""
    out: dict[int, list[Path]] = collections.defaultdict(list)
    for p in sorted((root / "evals").glob("chain-hop*--*")):
        m = re.match(r"chain-hop(\d+)--", p.name)
        if m and p.is_dir() and not re.search(r"--attempt-", p.name):
            out[int(m.group(1))].append(p)
    return out


def task_hops(root: Path, tid: str, entry_band: str, evals: dict[int, list[Path]] | None = None) -> list[dict]:
    """One task's hops in one arm root, read off disk. Hop h answers the
    signal at rev h-1 (the set's entry signal for h=1); its rewrite is the
    accepted one with input_rev h-1, and its measurement the hop-h eval
    records at rev h. A newer eval of the same hop (a re-measure) replaces
    an older one task by task."""
    evals = _hop_evals(root) if evals is None else evals
    rws = _rewrites(root, tid)
    hops = []
    direction = next_direction(entry_band)
    for h in range(1, HOPS + 1):
        if direction is None:
            break
        tried = [rw for rw in rws if int(rw.get("input_rev") or 0) == h - 1]
        acc = next((rw for rw in tried if rw["status"] == "accepted"), None)
        hop = {"hop": h, "direction": direction, "attempts": len(tried)}
        if acc is None:
            if not tried:
                hop["state"] = "not_started"
            elif any(rw["status"] == "running" for rw in tried):
                hop["state"] = "in_flight"
            else:
                hop["state"] = "not_accepted"
                hop["class"] = eb.classify(tried[-1])
            hops.append(hop)
            break
        hop["rewrite"] = Path(acc["_dir"]).name
        hop["job"] = acc.get("job")
        hop["rev"] = int(acc["result_rev"])
        m = None
        for ev in evals.get(h, []):
            hs = _eval_tasks(ev).get(tid)
            if hs:
                got = measure(hs, rev=hop["rev"])
                if got["graded"] or got["infra"]:
                    m = {**got, "eval": ev.name}
        if m is None:
            hop["state"] = "unmeasured"
            hops.append(hop)
            break
        hop.update(m)
        hop["state"] = "measured"
        hops.append(hop)
        direction = next_direction(m["band"])
    return hops


def _arm_roots(chain: dict) -> dict[str, Path]:
    return {label: Path(v["root"]) for label, v in chain["arms"].items()}


def _set_tasks(bench: Path, chain_name: str) -> dict[str, dict]:
    """The frozen set's tasks with their entry measurement (manifest.measured)."""
    m = json.load(open(bench / "sets" / chain_name / "manifest.json"))
    return {t: {"passed": v["solved"], "graded": v["graded"], "band": band(v["solved"], v["graded"])}
            for t, v in m["measured"].items()}


# -------------------------------------------------------------------------- launch


def launch(a: argparse.Namespace) -> None:
    """Hop 1: `evolve_bench.py run --no-auto-eval` of each arm on the frozen
    set (the root is created and recorded in chain.json). Hop h>1: a drive in
    the same root over <root>/chain/hop<h>-signals, which hop-collect wrote."""
    d, chain = _load_chain(a.bench, a.chain)
    log = eb._log_for(a.bench, f"chain-launch-hop{a.hop}")
    for spec in a.arm or []:
        label, path = spec.split("=", 1)
        arm = eb.load_arm(Path(path))
        root = a.bench / "arms" / f"{a.chain}--{arm['name']}"
        prev = chain["arms"].get(label)
        if prev and prev["root"] != str(root):
            raise SystemExit(f"arm label {label} already names {prev['root']}")
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", label):
            raise SystemExit(f"arm label {label!r}: upper-case letters and digits (it prefixes eval task ids)")
        chain["arms"][label] = {"root": str(root), "arm_file": str(Path(path).resolve()), "name": arm["name"]}
    layout.write_json_atomic(d / "chain.json", chain)
    labels = a.only or sorted(chain["arms"])
    for label in labels:
        info = chain["arms"][label]
        root = Path(info["root"])
        if a.hop == 1:
            eb.run_arm(bench=a.bench, set_name=a.chain, arm_path=Path(info["arm_file"]), log=log,
                       workers=a.workers, no_auto_eval=True)
            continue
        run = root / "chain" / f"hop{a.hop}-signals"
        if not (run / "selected.json").exists():
            print(f"{label}: no {run}/selected.json (hop-collect --hop {a.hop - 1} writes it); skipped")
            continue
        unit = f"bench-{root.name}-hop{a.hop}"
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0:
            print(f"{label}: {unit} is running; skipped")
            continue
        arm = eb.load_arm(Path(info["arm_file"]))
        if a.workers:
            arm = {**arm, "workers": a.workers}
        tt, head, _ = eb._arm_checkout(arm)
        rec = json.load(open(root / "bench_arm.json"))
        if rec["arm_commit"] != head:
            raise SystemExit(f"{root} ran {rec['arm_commit'][:12]}, {tt} is now at {head[:12]}")
        eb._start_unit(unit, root, eb._drive_env(root, arm, eb.DRIVE_PINS, run, run / "selected.json"),
                       ["bash", str(eb.DRIVE)], log)
        print(f"{label}: {unit} started over {run}")


# --------------------------------------------------------------------- hop-evalset


def hop_evalset(a: argparse.Namespace) -> Path:
    """Every arm's accepted hop-h revisions not yet measured, as one evalset
    whose task ids carry the arm label (<ARM>__<task>), for one cluster job."""
    d, chain = _load_chain(a.bench, a.chain)
    entry = _set_tasks(a.bench, a.chain)
    rows, waiting = [], collections.Counter()
    for label, root in sorted(_arm_roots(chain).items()):
        if not eb._drive_finished(root) and not a.allow_running:
            raise SystemExit(f"{label}: the newest offline_drive log of {root} does not end in 'finished'")
        live = {eb._iid(r): r for r in eb._rows(layout.Root(root).mix.live_version()[1])}
        evals = _hop_evals(root)
        for tid, e in sorted(entry.items()):
            hops = task_hops(root, tid, e["band"], evals)
            last = hops[-1] if hops else None
            if not last or last["hop"] != a.hop:
                continue
            if last["state"] in ("not_started", "in_flight"):
                waiting[(label, last["state"])] += 1
                continue
            if last["state"] != "unmeasured" and not (a.remeasure and last.get("band") == "ungraded"):
                continue
            r = live.get(tid)
            if r is None or int(r["metadata"].get("rev") or 0) != last["rev"]:
                raise SystemExit(f"{label} {tid}: live mix is not at hop {a.hop}'s rev {last['rev']}")
            r = json.loads(json.dumps(r))
            md = r["metadata"]
            md["bench_orig_instance_id"] = md["instance_id"]
            md["bench_group"] = label
            md["instance_id"] = f"{label}__{md['instance_id']}"
            r["label"] = md["instance_id"]
            rows.append(r)
    if waiting:
        print(f"waiting: {dict(waiting)}")
    hop_dir = d / "hops" / f"hop{a.hop}"
    n = len(list((hop_dir / "evalsets").glob("hop*.jsonl"))) if (hop_dir / "evalsets").exists() else 0
    path = hop_dir / "evalsets" / f"hop{a.hop}-{chr(ord('a') + n)}.jsonl"
    m = eb._write_evalset(path, rows, student_harness=chain["harness"],
                          note=f"chain {a.chain} hop {a.hop}: every arm's accepted revision (prefix = arm)")
    print(f"{path}: {len(rows)} rows {dict(collections.Counter(r['metadata']['bench_group'] for r in rows))} "
          f"sha256 {m['sha256'][:12]}")
    return path


# --------------------------------------------------------------------- hop-collect


def hop_collect(a: argparse.Namespace) -> None:
    """The combined hop-h eval -> <root>/evals/chain-hop<h>--<eval> per arm,
    then for every task that came out 0/12 or 12/12 (h < 3) the next hop's
    signal run, <root>/chain/hop<h+1>-signals, via eval_signal_run.py."""
    d, chain = _load_chain(a.bench, a.chain)
    entry = _set_tasks(a.bench, a.chain)
    for label, root in sorted(_arm_roots(chain).items()):
        for ev in a.eval:
            out = root / "evals" / f"chain-hop{a.hop}--{ev.name}"
            if not out.exists():
                split_eval(ev, label, out)
        if a.hop >= HOPS:
            continue
        cont = []
        tally = collections.Counter()
        for tid, e in sorted(entry.items()):
            this = next((h for h in task_hops(root, tid, e["band"]) if h["hop"] == a.hop), None)
            if this is None:
                continue
            tally[this.get("band", this["state"])] += 1
            if this["state"] == "measured" and next_direction(this["band"]):
                cont.append(tid)
        print(f"{label}: hop {a.hop} {dict(tally)}; {len(cont)} continue")
        run = root / "chain" / f"hop{a.hop + 1}-signals"
        if run.exists():
            print(f"{label}: {run} exists; not rewritten")
            continue
        if not cont:
            continue
        run.parent.mkdir(exist_ok=True)
        subprocess.run([sys.executable, str(_EVO / "eval_signal_run.py"), "--arm", str(root), "--out", str(run),
                        "--tasks", *cont], check=True)
        # The set's reference-only groups go into every hop's signal run, which
        # offline_stage.py stages beside the signals (EVOLVE_PEER_CONTEXT reads the signal's run).
        set_run = a.bench / "sets" / a.chain / "runs" / a.chain
        if (set_run / "reference.json").exists():
            refs = json.load(open(set_run / "reference.json"))
            for rel in refs:
                (run / rel).parent.mkdir(parents=True, exist_ok=True)
                os.link(set_run / rel, run / rel)
            shutil.copy2(set_run / "reference.json", run / "reference.json")
            print(f"{label}: {len(refs)} reference records into {run}")


# -------------------------------------------------------------------- audit-bundle


def audit_bundle(a: argparse.Namespace) -> None:
    """A blinded bundle of every arm's accepted hop-h rewrites (input rev h-1 ->
    result rev h), in evolve_bench.audit_bundle's layout so `evolve_bench.py
    audit-run` scores it unchanged. The original is the revision the hop
    rewrote, so the auditor judges this one step."""
    d, chain = _load_chain(a.bench, a.chain)
    if a.out.exists():
        raise SystemExit(f"{a.out} exists")
    if a.key.resolve() == a.out.resolve() or a.out.resolve() in a.key.resolve().parents:
        raise SystemExit("the key has to stay outside the audit directory")
    roots = _arm_roots(chain)
    accepted: dict[str, dict[str, dict]] = {}
    for label, root in roots.items():
        acc = {}
        for p in (root / "evolution" / "tasks").glob("*/rewrites/*/rewrite.json"):
            rw = json.load(open(p))
            if rw["status"] == "accepted" and int(rw.get("result_rev") or 0) == a.hop:
                rw["_dir"] = p.parent
                acc[rw["task"]] = rw
        accepted[label] = acc
    tasks = sorted(set().union(*[set(v) for v in accepted.values()]))
    a.out.mkdir(parents=True)
    shutil.copy2(_EVO / "agents" / "bench_audit.md", a.out / "INSTRUCTIONS.md")
    shutil.copy2(_EVO / "agents" / "bench_audit_verify.md", a.out / "VERIFY.md")
    keymap, task_of = {}, {}
    for t in tasks:
        order = [label for label in sorted(roots) if t in accepted[label]]
        random.Random(f"{a.seed}:{a.hop}:{t}").shuffle(order)
        # Hop 1: every variant rewrote the seed, so they share one original/ as in
        # evolve_bench.audit_bundle. Later hops: each arm rewrote its own previous
        # revision, so each variant gets its own task directory whose original/ is
        # that revision; the rubric and the audit prompt stay unchanged.
        groups = [(t, order)] if a.hop == 1 else [(f"{t}~{i + 1}", [lab]) for i, lab in enumerate(order)]
        for dname, labels in groups:
            td = a.out / dname
            td.mkdir()
            keymap[dname] = {f"V{i + 1}": lab for i, lab in enumerate(labels)}
            task_of[dname] = t
            first = accepted[labels[0]][t]
            shutil.copytree(roots[labels[0]] / "evolution" / "tasks" / t / f"r{first['input_rev']}", td / "original",
                            symlinks=True)
            for v, label in keymap[dname].items():
                rw = accepted[label][t]
                tdir = roots[label] / "evolution" / "tasks" / t
                r_in, r_out = f"r{rw['input_rev']}", f"r{rw['result_rev']}"
                vd = td / v
                vd.mkdir()
                shutil.copytree(tdir / r_out, vd / "package", symlinks=True)
                for part in ("instruction.md", "tests", "solution", "environment"):
                    (vd / f"diff_{part.replace('.md', '')}.txt").write_text(
                        eb._diff(tdir, f"{r_in}/{part}", f"{r_out}/{part}"))
                (vd / "agent_summary.md").write_text(
                    eb._scrub(eb._last_agent_message(rw["_dir"]) or "(no agent summary found)", list(roots.values())))
                (vd / "direction.txt").write_text(f"{rw['job']}\n")
    a.key.parent.mkdir(parents=True, exist_ok=True)
    layout.write_json_atomic(a.key, {"arms": {k: str(v) for k, v in roots.items()}, "seed": a.seed, "hop": a.hop,
                                     "chain": a.chain, "tasks": keymap, "task_of": task_of})
    print(f"hop {a.hop}: {len(tasks)} tasks -> {a.out}; key -> {a.key}")


def _audits(paths: list[tuple[Path, Path]]) -> dict[tuple[str, str, int], dict]:
    """(arm, task, hop) -> the blind auditor's label and its confirmed or partly confirmed defect classes."""
    out = {}
    for audit, key in paths:
        k = json.load(open(key))
        hop = int(k.get("hop", 1))
        for dname, variants in k["tasks"].items():
            t = k.get("task_of", {}).get(dname, dname)
            for v, label in variants.items():
                vp = audit / dname / v / "verdict.json"
                if not vp.exists():
                    continue
                verdict = json.load(open(vp))
                checks = json.load(open(audit / dname / v / "verify.json")) if (audit / dname / v / "verify.json").exists() else {}
                confirmed = sorted({f["class"] for f in verdict.get("findings", [])
                                    if checks.get(f["id"]) in ("confirmed", "partly")})
                pending = [f["id"] for f in verdict.get("findings", [])
                           if f["class"] in eb.DEFECT_LABELS and f["id"] not in checks]
                defects = sorted(set(confirmed) & eb.DEFECT_LABELS)
                # Settled once every defect finding went through the verify step.
                out[(label, t, hop)] = {"label": verdict.get("label"), "confirmed": confirmed,
                                        "defects": defects, "settled": not pending}
    return out


def hop_class(hop: dict, audit: dict | None) -> str:
    """One hop's outcome class (HOP_CLASSES)."""
    if hop["state"] != "measured":
        return hop["state"]
    if hop["band"] != "mixed":
        return hop["band"]
    if audit is None or not audit["settled"]:
        return "mixed_unaudited"
    return "mixed_defect" if audit["defects"] else "clean_mixed"


# -------------------------------------------------------------------------- report

# How a doc case's known failure class is checked on one hop: the blind audit
# labels and confirmed finding classes that mean the hop repeated it, and/or a
# rule on the hop's outcome or diff. Classes that describe the data rather than
# a rewrite get no check ("data").
AUDIT_RULES = {
    "starter-does-the-work": {"LEAKY"},
    "leak-in-image": {"LEAKY"},
    "leak-key-trick": {"LEAKY"},
    "hint-left": {"LEAKY"},
    "answer-in-env": {"LEAKY"},
    "hidden-requirement": {"UNFAIR", "UNSTATED-ENVIRONMENT-TRAP"},
    "identity-uid": {"UNFAIR", "UNSTATED-ENVIRONMENT-TRAP"},
    "silent-trap": {"UNFAIR", "UNSTATED-ENVIRONMENT-TRAP"},
    "rewrite-broke-grader": {"BROKEN"},
    "no-difficulty-added": {"WEAK", "GRADER-TIGHTENING-ONLY"},
    "rephrase-only": {"WEAK"},
    "version-pin-no-difficulty": {"WEAK"},
    "method-policing": {"GRADER-TIGHTENING-ONLY"},
}
DATA_CLASSES = {
    "reward-hack", "resource-undersized", "external-repo-grader", "harness-incompatible", "verifier-env-broken",
    "hf-moved-to-repair", "verifier-always-passes", "verifier-timeout", "wrong-reference-answer",
    "impossible-task", "instruction-grader-contradiction", "near-always-solved", "good-contrast",
}


def _diff_facts(root: Path, tid: str, hop: dict) -> dict:
    tdir = root / "evolution" / "tasks" / tid
    old, new = tdir / f"r{hop['rev'] - 1}", tdir / f"r{hop['rev']}"
    if not (old.is_dir() and new.is_dir()):
        return {}
    ds = eb.diff_stats(old, new, "")
    r0 = eb._words(tdir / "r0" / "instruction.md")
    return {"assertions": ds["assertions"], "tests_size_ratio": ds["tests_size_ratio"],
            "instruction_words": ds["instruction_words"], "instruction_words_r0": r0,
            "solution_changed": ds["solution_changed"], "dockerfile_changed": ds["dockerfile_changed"]}


def repeat_check(cls: str, hop: dict, audit: dict | None, facts: dict, oscillated: bool) -> tuple[str, str]:
    """Whether one hop repeated a known failure class: (verdict, basis).
    verdict is yes / no / n/a / unaudited."""
    if cls in DATA_CLASSES:
        return "n/a", "data class"
    if cls == "refuses-harder":
        if hop["direction"] != "harder":
            return "n/a", "easier hop"
        return ("yes", f"not accepted ({hop.get('class')})") if hop["state"] == "not_accepted" else ("no", "accepted")
    if hop["state"] != "measured" and cls not in ("chain-accumulation",):
        return "n/a", hop["state"]
    b = hop.get("band")
    if cls == "overshoot-budget":
        return ("yes", "harder -> 0/12") if hop["direction"] == "harder" and b == "0/12" else ("no", f"{hop['direction']} -> {b}")
    if cls == "oscillation":
        return ("yes", "chain oscillated") if oscillated else ("no", "no oscillation")
    if cls in ("easier-chain-dead", "never-solved"):
        if hop["direction"] != "easier":
            return "n/a", "harder hop"
        return ("yes", "easier -> still 0/12") if b == "0/12" else ("no", f"easier -> {b}")
    if cls == "verifier-weakened":
        a_ = facts.get("assertions")
        ratio = facts.get("tests_size_ratio")
        if hop["direction"] == "easier" and a_ and (a_[1] < a_[0] or (ratio is not None and ratio < 0.8)):
            return "yes", f"diff: assertions {a_[0]}->{a_[1]}, tests size x{ratio}"
        return "no", f"diff: assertions {a_}, tests size x{ratio}" if a_ else "no diff facts"
    if cls == "chain-accumulation":
        w = facts.get("instruction_words")
        w0 = facts.get("instruction_words_r0")
        if hop["hop"] >= 2 and w and w0 and w[1] > w[0] and w[1] >= 1.5 * w0:
            return "yes", f"diff: instruction words r0 {w0} -> r{hop['rev']} {w[1]}"
        return "no", f"diff: instruction words r0 {w0} -> {w[1] if w else '?'}"
    labels = AUDIT_RULES.get(cls)
    if labels is None:
        return "n/a", "no rule"
    if audit is None:
        out_rule = cls in ("no-difficulty-added", "rephrase-only", "version-pin-no-difficulty") and \
            hop["direction"] == "harder" and b == "12/12"
        return ("yes", "harder -> still 12/12 (outcome; unaudited)") if out_rule else ("unaudited", "no blind audit yet")
    hit = sorted(labels & ({audit["label"]} | set(audit["confirmed"])))
    if hit:
        return "yes", f"audit {audit['label']}; confirmed {audit['confirmed']}"
    return "no", f"audit {audit['label']}; confirmed {audit['confirmed']}"


def chain_metrics(per_task: dict[str, list[dict]], n_chain: int) -> dict:
    """Per hop, the outcome classes of the tasks that reached it, overall and
    by the hop's direction (each hop dict carries ``cls``, hop_class's
    answer); the stubborn rate (>= 2 consecutive same-direction hops that
    left the result where it was: easier still 0/12, harder still 12/12) and
    the oscillation rate (harder -> 0/12 then easier -> 12/12, or the
    reverse), both over the chain's size. A hop whose rewrite was not
    accepted is not clean_mixed; the stubborn rate counting such hops as
    unchanged is reported beside."""
    per_hop = {h: collections.Counter() for h in range(1, HOPS + 1)}
    per_dir = {h: {d: collections.Counter() for d in DIRECTIONS} for h in range(1, HOPS + 1)}
    time_limit = {h: collections.Counter() for h in range(1, HOPS + 1)}
    defects = {h: collections.Counter() for h in range(1, HOPS + 1)}
    waits: dict[int, list[float]] = {h: [] for h in range(1, HOPS + 1)}
    stubborn, stubborn_na, osc = [], [], []
    for tid, hops in per_task.items():
        for hop in hops:
            per_hop[hop["hop"]][hop["cls"]] += 1
            per_dir[hop["hop"]][hop["direction"]][hop["cls"]] += 1
            if hop["state"] == "measured":
                time_limit[hop["hop"]]["attempts"] += hop.get("graded", 0)
                time_limit[hop["hop"]]["time_limit"] += hop.get("time_limit", 0)
                if hop.get("gen_wait_per_turn") is not None:
                    waits[hop["hop"]].append(hop["gen_wait_per_turn"])
            for c in (hop.get("audit") or {}).get("defects", []):
                defects[hop["hop"]][c] += 1
        run = run_na = best = best_na = 0
        for hop in hops:
            same = hop["state"] == "measured" and (
                (hop["direction"] == "easier" and hop["band"] == "0/12")
                or (hop["direction"] == "harder" and hop["band"] == "12/12"))
            run = run + 1 if same else 0
            run_na = run_na + 1 if (same or hop["state"] == "not_accepted") else 0
            best, best_na = max(best, run), max(best_na, run_na)
        if best >= 2:
            stubborn.append(tid)
        if best_na >= 2:
            stubborn_na.append(tid)
        for x, y in zip(hops, hops[1:]):
            if x["state"] == y["state"] == "measured" and (
                (x["direction"] == "harder" and x["band"] == "0/12" and y["direction"] == "easier" and y["band"] == "12/12")
                or (x["direction"] == "easier" and x["band"] == "12/12" and y["direction"] == "harder" and y["band"] == "0/12")):
                osc.append(tid)
                break

    def rate(xs: list) -> dict:
        return {"tasks": len(xs), "rate": round(len(xs) / n_chain, 3) if n_chain else None, "ids": sorted(xs)}

    def row(c: collections.Counter) -> dict:
        return {**{k: c[k] for k in HOP_CLASSES}, **{k: v for k, v in c.items() if k not in HOP_CLASSES},
                "reached": sum(c.values())}

    return {"chain_tasks": n_chain,
            "per_hop": {h: {**row(per_hop[h]), "by_direction": {d: row(per_dir[h][d]) for d in DIRECTIONS},
                            "time_limit_attempts": dict(time_limit[h]),
                            "gen_wait_per_turn_sec": round(sum(waits[h]) / len(waits[h]), 1) if waits[h] else None,
                            "confirmed_defects": dict(defects[h])} for h in per_hop},
            "stubborn": rate(stubborn), "stubborn_counting_not_accepted": rate(stubborn_na),
            "oscillation": rate(osc)}


def _failure_classes(cases: Path) -> dict[str, dict]:
    """task -> its failure classes and the direction of the cited case (doc-cases cases.jsonl)."""
    out: dict[str, dict] = {}
    for ln in open(cases):
        r = json.loads(ln)
        cm = r.get("chain_mode") or {}
        ids = [r["task_id"]] if r.get("task_id") else cm.get("via_candidates", [])
        for t in ids:
            e = out.setdefault(t, {"classes": [], "directions": [], "proposed": False})
            for c in r.get("failure_class") or []:
                if c not in e["classes"]:
                    e["classes"].append(c)
            if r.get("direction") and r["direction"] not in e["directions"]:
                e["directions"].append(r["direction"])
            e["proposed"] |= bool(cm.get("proposed"))
    return out


def report(a: argparse.Namespace) -> None:
    d, chain = _load_chain(a.bench, a.chain)
    entry = _set_tasks(a.bench, a.chain)
    strata = {t: s for s, v in chain["strata"].items() for t in v["tasks"]}
    audits = _audits([(Path(x), Path(y)) for x, y in (s.split("=", 1) for s in (a.audit or []))])
    classes = _failure_classes(a.cases) if a.cases else {}
    out: dict[str, Any] = {"chain": a.chain, "student": chain["student"], "recipe": chain["recipe"],
                           "created": layout.stamp(), "entry": json.load(open(d / "entry.json"))
                           if (d / "entry.json").exists() else None, "arms": {}}
    lines = [f"# chain {a.chain} ({chain['student']}, {chain['recipe']}, k={K})", ""]
    for label, root in sorted(_arm_roots(chain).items()):
        evals = _hop_evals(root)
        per_task = {t: task_hops(root, t, e["band"], evals) for t, e in sorted(entry.items())}
        for t, hops in per_task.items():
            for hop in hops:
                hop["audit"] = audits.get((label, t, hop["hop"]))
                hop["cls"] = hop_class(hop, hop["audit"])
        groups = {"all": list(per_task)}
        for t in per_task:
            groups.setdefault(f"stratum:{strata.get(t, '?')}", []).append(t)
        mets = {g: chain_metrics({t: per_task[t] for t in ts}, len(ts)) for g, ts in groups.items()}
        cases = {}
        for t, hops in per_task.items():
            if t not in classes:
                continue
            osc = t in mets["all"]["oscillation"]["ids"]
            rows = []
            for hop in hops:
                facts = _diff_facts(root, t, hop) if hop.get("rev") else {}
                checks = {c: repeat_check(c, hop, audits.get((label, t, hop["hop"])), facts, osc)
                          for c in classes[t]["classes"]}
                rows.append({**{k: hop.get(k) for k in ("hop", "direction", "state", "cls", "passed", "graded", "class")},
                             "audit": hop["audit"], "repeat": checks})
            cases[t] = {"classes": classes[t]["classes"], "entry": entry[t], "hops": rows}
        out["arms"][label] = {"root": str(root), "metrics": mets, "tasks": per_task, "cases": cases}
        m = mets["all"]
        lines += [f"## {label} ({root.name})", "",
                  "| hop | reached | clean_mixed | mixed, defect | mixed, unaudited | 12/12 | 0/12 | not accepted "
                  "| unmeasured | time-limit attempts | confirmed defects |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for h, c in m["per_hop"].items():
            tl = c["time_limit_attempts"]
            lines.append(f"| {h} | {c['reached']} | {c['clean_mixed']} | {c['mixed_defect']} | {c['mixed_unaudited']} | "
                         f"{c['12/12']} | {c['0/12']} | {c['not_accepted']} | "
                         f"{c['unmeasured'] + c.get('not_started', 0) + c.get('in_flight', 0)} | "
                         f"{tl.get('time_limit', 0)}/{tl.get('attempts', 0)} | {c['confirmed_defects'] or '-'} |")
        lines += ["", f"stubborn {m['stubborn']['tasks']}/{m['chain_tasks']} ({m['stubborn']['rate']}); counting "
                  f"not-accepted hops as unchanged {m['stubborn_counting_not_accepted']['tasks']}; oscillation "
                  f"{m['oscillation']['tasks']}/{m['chain_tasks']} ({m['oscillation']['rate']})", ""]
        for g, mm in mets.items():
            if g != "all":
                lines.append(f"- {g}: {mm['chain_tasks']} tasks, stubborn {mm['stubborn']['tasks']}, "
                             f"oscillation {mm['oscillation']['tasks']}")
        if cases:
            lines += ["", "| task | classes | entry | hop 1 | hop 2 | hop 3 |", "|---|---|---|---|---|---|"]
            for t, c in sorted(cases.items()):
                cells = []
                for r in c["hops"]:
                    rep = ", ".join(f"{k}={v[0]}" for k, v in r["repeat"].items())
                    res = f"{r['passed']}/{r['graded']}" if r["state"] == "measured" else r["state"]
                    cells.append(f"{r['direction'][0].upper()} {res} [{rep}]")
                cells += [""] * (HOPS - len(cells))
                lines.append(f"| {t} | {', '.join(c['classes'])} | {c['entry']['passed']}/{c['entry']['graded']} | "
                             + " | ".join(cells) + " |")
        lines.append("")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    layout.write_json_atomic(a.out, out)
    a.out.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


# ---------------------------------------------------------------------------- main


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bench", type=Path, required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="candidates -> chain dir, seed mix, package copies, entry evalset")
    b.add_argument("--chain", required=True)
    b.add_argument("--student", choices=sorted(STUDENTS), required=True)
    b.add_argument("--harness", default="terminus", choices=eb.HARNESSES)
    b.add_argument("--stratum", action="append", default=[], help="NAME:easier|harder|both:IDS[:N:SEED] (file or comma list; N:SEED draws a seeded sample of N)")
    b.add_argument("--fill-pool", help="ids to draw the fill from")
    b.add_argument("--fill-seed", type=int, default=0)
    b.add_argument("--fill-n", type=int, default=0, help="draw this many fill candidates (0: the whole pool)")
    b.add_argument("--fill-stratum", default="fill:easier", help="NAME:DIRS of the fill stratum")
    b.add_argument("--rows", type=Path, nargs="+", required=True, help="mix files holding the seed rows (first wins)")
    b.add_argument("--sources", type=Path, nargs="+", required=True, help="corpus dirs holding tasks/<id> (first wins)")
    b.add_argument("--tag-sources", action="store_true", help="name copied corpora <corpus>@<release8>")
    b.add_argument("--measured", type=Path, nargs="*", help="evals already measuring some originals (left out of entry)")
    b.add_argument("--row-grader", action="store_true",
                   help="where the package's tests/test.sh is not the row's test_sh, copy the row's into the package")
    b.add_argument("--allow-missing", action="store_true")
    b.add_argument("--note", default="")
    s = sub.add_parser("split-eval", help="one prefix of a combined eval -> a derived eval with plain task ids")
    s.add_argument("eval_dir", type=Path)
    s.add_argument("--prefix", required=True)
    s.add_argument("--out", type=Path, required=True)
    ad = sub.add_parser("admit", help="entry evals -> entry.json and the frozen set")
    ad.add_argument("--chain", required=True)
    ad.add_argument("--eval", type=Path, nargs="+", required=True)
    ad.add_argument("--cap", type=int, default=0, help="admit fill tasks (in fill order) only up to this chain size")
    ad.add_argument("--reference-first-group", type=Path, nargs="*",
                    help="training runs whose in-band rev-0 first groups become EVOLVE_PEER_CONTEXT references")
    ad.add_argument("--reference-eval", type=Path, nargs="*",
                    help="evals of the chain's student whose in-band groups become references")
    ad.add_argument("--reference-band", default="3-8")
    ad.add_argument("--reference-k", type=int, default=0, help="graded attempts a reference group must have")
    ad.add_argument("--dry-run", action="store_true")
    la = sub.add_parser("launch", help="start hop h's drive in every arm root")
    la.add_argument("--chain", required=True)
    la.add_argument("--hop", type=int, required=True, choices=range(1, HOPS + 1))
    la.add_argument("--arm", action="append", help="LABEL=<arm json> (hop 1: registers the arm)")
    la.add_argument("--only", nargs="*")
    la.add_argument("--workers", type=int)
    he = sub.add_parser("hop-evalset", help="every arm's accepted hop-h revisions -> one combined evalset")
    he.add_argument("--chain", required=True)
    he.add_argument("--hop", type=int, required=True, choices=range(1, HOPS + 1))
    he.add_argument("--remeasure", action="store_true", help="also tasks whose hop eval came out ungraded")
    he.add_argument("--allow-running", action="store_true")
    hc = sub.add_parser("hop-collect", help="combined hop-h eval -> per-arm evals and hop-(h+1) signals")
    hc.add_argument("--chain", required=True)
    hc.add_argument("--hop", type=int, required=True, choices=range(1, HOPS + 1))
    hc.add_argument("--eval", type=Path, nargs="+", required=True)
    au = sub.add_parser("audit-bundle", help="blinded bundle of the hop-h rewrites")
    au.add_argument("--chain", required=True)
    au.add_argument("--hop", type=int, required=True, choices=range(1, HOPS + 1))
    au.add_argument("--out", type=Path, required=True)
    au.add_argument("--key", type=Path, required=True)
    au.add_argument("--seed", type=int, default=319)
    r = sub.add_parser("report", help="per-arm hop counts, stubborn and oscillation rates, per-case table")
    r.add_argument("--chain", required=True)
    r.add_argument("--audit", nargs="*", help="AUDIT_DIR=KEY_JSON of scored audit bundles")
    r.add_argument("--cases", type=Path, help="doc-cases cases.jsonl (failure classes)")
    r.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.cmd == "split-eval":
        split_eval(a.eval_dir.resolve(), a.prefix, a.out)
        return
    {"build": build, "admit": admit, "launch": launch, "hop-evalset": hop_evalset, "hop-collect": hop_collect,
     "audit-bundle": audit_bundle, "report": report}[a.cmd](a)


if __name__ == "__main__":
    main()
