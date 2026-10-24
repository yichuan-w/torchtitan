#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""EvolveBench: one offline round of the rewriter over a frozen signal set, measured.

EVOLVE_BENCH.md says what it measures and why each pin exists. Subcommands, in
the order an arm goes through them:

    freeze        signals (a run dir, or a base-student eval) -> <bench>/sets/<name>/, pinned by sha256
    run           an arm config + a set -> <bench>/arms/<set>--<arm>/, offline_drive.sh as a user unit
    evalset       an arm root -> its accepted revisions as data/evalsets/bench-accepted.jsonl
    eval          an evalset -> della/tb2_eval_local.sh under the pinned student recipe
    tally         evals and/or training runs [+ root] -> one JSON: bands, funnel, rewrite stats, cost, repair
    compare       tally JSONs -> paired transitions, exact McNemar, a markdown table
    audit-bundle  arm roots -> blinded per-task bundles for the LLM audit, key kept outside
    audit-tally   audit verdicts + key + tallies -> per-arm audit rates, $ per clean mixed task

Every subcommand logs one timestamped line per item to a file and prints where.
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import glob
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_EVO = Path(__file__).resolve().parent
_TMAX = _EVO.parent
# By file, not through the package: rl/__init__.py imports the training stack.
sys.path.insert(0, str(_TMAX))
sys.path.insert(0, str(_EVO))
import layout  # noqa: E402
import offline_cost  # noqa: E402

# The student recipe every bench eval runs under. tb2_eval_local.sh exports
# these into the trainer and records them in launch.json's env; tally reads
# them back from there, and two evals that disagree on any are not compared.
STUDENT_RECIPE = {
    "SWE_TB2_VAL_K": "5",
    "SWE_TB2_VAL_TEMPERATURE": "1.0",
    "SWE_TB2_VAL_TOP_P": "1.0",
    # A row declaring no agent timeout gets this; one declaring its own gets
    # max(declared, floor). Both are the eval recipe's own knobs: the eval
    # config does not read SWE_TIME_BUDGET_SEC / SWE_AGENT_TIMEOUT_FLOOR_SEC.
    "SWE_EVAL_TIME_BUDGET_SEC": "2400",
    "SWE_EVAL_BUDGET_FLOOR_SEC": "7200",
    "SWE_TIME_BUDGET_SEC": "2400",
    "SWE_MAX_NUM_SEQS": "256",
    "TMAX_TERMINUS_MAX_TURNS": "120",
    "TMAX_TURN_MAX_TOKENS": "32768",
    "TMAX_EXEC_TIMEOUT_SEC": "120",
    "SWE_MAX_CONTEXT_LEN": "63488",
    "TT_DAYTONA_CPU": "1",
    "TT_DAYTONA_MEM_GB": "2",
    "TT_DAYTONA_DISK_GB": "2",
    # The eval writes no evolution signals: what it measures never reaches a loop.
    "SWE_EVOLUTION_SIGNALS": "0",
}
# The student harness is part of the recipe but chosen per eval (`eval --harness`).
HARNESSES = ("terminus", "miniswe")
# tb2_eval_local.sh runs one generator.
EVAL_GENERATORS = 1
# Attempts queue for a decode slot while their wall clock runs: past
# max_num_seqs x generators they read as time-budget failures (one eval at 445
# on one 256-seq generator: 356 of its last 400 attempts at the 2400 s budget).
ROLLOUT_CONCURRENCY = str(int(STUDENT_RECIPE["SWE_MAX_NUM_SEQS"]) * EVAL_GENERATORS)
# Launch-level fields that have to agree too.
RECIPE_FIELDS = ("checkpoint", "tt_commit", "gpu_names", "num_generators")

# What `run` sets for the drive, whatever the arm says. TT_DAYTONA_* are the
# trainer's fleet defaults (a row declaring none is verified at that size);
# MAX_* are offline_drive.sh's own defaults; SWE_POLICY_CANARY_K=0 because the
# loop must spend no student rollouts on a rewrite.
DRIVE_PINS = {
    "TT_DAYTONA_CPU": "1",
    "TT_DAYTONA_MEM_GB": "2",
    "TT_DAYTONA_DISK_GB": "2",
    "MAX_ROUNDS": "6",
    "MAX_ATTEMPTS": "2",
    "SWE_POLICY_CANARY_K": "0",
}
# Set by `run` from the set and the root; an arm naming one is refused.
DRIVE_OWNED = (
    "TRL_PROFILE",
    "TRL_BASE",
    "SOURCE_RUN",
    "SELECTED",
    "WORKERS",
    "ATTEMPTS_SINCE",
    "EVOLVE_SOCK_DIR",
    "TMPDIR",
)
ARM_KEYS = {"name", "profile", "commit", "bin", "workers", "env"}

BUCKETS = ("mixed", "all_solved", "none_solved", "ungraded", "no_attempts")
GRADED_BUCKETS = BUCKETS[:3]
INDEX = Path("trainer/validation_traces/step-0/index.json")

# Hosts a grader may fetch from without depending on someone's live repository:
# package indexes, distro mirrors and toolchain installers.
PACKAGE_HOSTS = {
    "pypi.org", "files.pythonhosted.org", "pypi.python.org", "astral.sh",
    "deb.debian.org", "security.debian.org", "archive.ubuntu.com",
    "security.ubuntu.com", "ports.ubuntu.com", "dl-cdn.alpinelinux.org",
    "registry.npmjs.org", "crates.io", "static.crates.io", "index.crates.io",
    "static.rust-lang.org", "sh.rustup.rs", "proxy.golang.org",
    "download.pytorch.org", "conda.anaconda.org", "repo.anaconda.com",
    "repo.maven.apache.org", "rubygems.org", "cloud.r-project.org", "nodejs.org",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "example.com", "example.org"}
FETCH_RE = re.compile(
    r"\bgit['\"]?[\s,]+['\"]?(clone|fetch|pull|ls-remote)\b|clone_from\(|\bcurl\b|\bwget\b"
    r"|requests\.(get|post|head)\(|urlopen\(|urlretrieve\(|httpx\.|aiohttp|pip\s+install\s+git\+"
)
URL_RE = re.compile(r"\b(?:https?|git|ssh)://([A-Za-z0-9.-]+)|\bgit@([A-Za-z0-9.-]+):")
# A new check that runs the submitted program on inputs the grader writes.
EXEC_RE = re.compile(r"subprocess\.(run|call|check_call|check_output|Popen)|os\.system\(|\bbash\s+-c\b|\bpython3?\s+/(app|home|tmp|workspace)")
WRITE_RE = re.compile(r"tempfile\.|mkdtemp|NamedTemporaryFile|write_text\(|write_bytes\(|open\([^)]*['\"][wa]b?['\"]|cat\s*>|>\s*/tmp/|shutil\.copy")
ASSERT_RE = re.compile(r"\bassert\w*\b")
CLAIM_UNCHANGED_RE = re.compile(
    r"\b(verifier|tests?|checker|grader|test\.sh|test_\w+\.py)\b[^.\n]{0,60}"
    r"\b(unchanged|untouched|not (?:been )?(?:changed|modified|edited|touched)|left as[- ]is|kept as[- ]is)"
    r"|\b(no changes? to|without (?:changing|modifying|touching)) (?:the )?(verifier|tests?|checker|grader)\b",
    re.I,
)
TEXT_LIMIT = 2 << 20


# --------------------------------------------------------------------------- util


class Log:
    """One timestamped line per item, to a file named before anything runs."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._f = open(path, "a")

    def __call__(self, msg: str) -> None:
        self._f.write(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}\n")
        self._f.flush()


def _log_for(directory: Path, cmd: str) -> Log:
    log = Log(directory / "logs" / f"evolve_bench-{cmd}--{layout.stamp()}.log")
    print(f"log: {log.path}")
    return log


def _rows(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _iid(row: dict) -> str:
    return row["metadata"]["instance_id"]


def tree_sha256(d: Path) -> str:
    """One digest over a directory: every path, symlink target and file's bytes."""
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d).as_posix()
        if p.is_symlink():
            h.update(f"L {rel} {os.readlink(p)}\n".encode())
        elif p.is_file():
            h.update(f"F {rel} {layout.sha256_file(p)}\n".encode())
    return h.hexdigest()


def _git(checkout: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _bench_commit() -> str:
    """This checkout's commit, -dirty when tracked files differ from it."""
    sha = _git(_EVO, "rev-parse", "HEAD")
    dirty = _git(_EVO, "status", "--porcelain", "--untracked-files=no")
    return sha + ("-dirty" if dirty else "")


def _profile(name: str) -> dict[str, str]:
    path = _TMAX / "runbook" / "profiles" / f"{name}.env"
    if not path.is_file():
        raise SystemExit(f"no profile {name} at {path}")
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_evalset(path: Path, rows: list[dict], **meta) -> dict:
    """An evalset and its manifest, in offline_evalsets.py's shape plus ``meta``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "name": path.stem,
        "rows": len(rows),
        "sha256": layout.sha256_file(path),
        "created": layout.stamp(),
        **meta,
        "labels": [_iid(r) for r in rows],
    }
    layout.write_json_atomic(path.with_name(path.stem + ".manifest.json"), manifest)
    return manifest


def _record_path(ev: Path, rel: str) -> Path:
    """Where an eval index entry's record is: the controller writes each
    lifetime's records into a sibling ``<eval>--attempt-<id>/`` directory
    while index.json names them relative to the eval."""
    for d in [ev, *sorted(Path(p) for p in glob.glob(glob.escape(str(ev)) + "--attempt-*"))]:
        if (d / rel).exists():
            return d / rel
    raise FileNotFoundError(f"{rel}: not under {ev} or any of its attempt directories")


def bucket(passed: int, graded: int) -> str:
    """A task's band over its graded attempts (repair_eval_summary.py's rule):
    fewer than two graded attempts is no spread at all."""
    if graded < 2:
        return "ungraded"
    return "all_solved" if passed == graded else "none_solved" if passed == 0 else "mixed"


def _harness(launch: dict | None) -> str | None:
    """The student harness a launch ran: TMAX_AGENT, whose default is terminus."""
    if launch is None:
        return None
    return launch.get("env", {}).get("TMAX_AGENT") or "terminus"


# ------------------------------------------------------------------------- freeze


def _signals_from_eval(ev: Path, name: str, log: Log) -> tuple[dict, dict]:
    """First-round signals from a base-student eval of the whole set: none
    solved -> easier, all solved -> harder, each at rev 0, in
    offline_select_eval.py's signal shape, with the source path of every
    record its attempts name. Mixed tasks need no rewrite and are returned
    as usable as-is, with the ungraded."""
    per: dict[str, dict[str, Any]] = collections.defaultdict(lambda: {"pass": 0, "graded": 0, "records": []})
    for e in json.load(open(ev / INDEX)):
        t = per[e["task"]]
        t["records"].append(e["record"])
        if not e.get("infra_failed"):
            t["graded"] += 1
            t["pass"] += e["state"] == "PASS"
    signals, rest = {}, {}
    stamp = layout.stamp()
    for tid, v in sorted(per.items()):
        b = bucket(v["pass"], v["graded"])
        if b not in ("all_solved", "none_solved"):
            rest[tid] = b
            log(f"task={tid} {v['pass']}/{v['graded']} {b}: no signal")
            continue
        groups = {int(re.findall(r"g(-?\d+)-r\d+\.jsonl$", r)[0]) for r in v["records"]}
        if len(groups) != 1:
            raise SystemExit(f"{tid}: the eval's records span groups {sorted(groups)}")
        records = {f"rollouts/{tid}/{Path(rel).name}": _record_path(ev, rel) for rel in sorted(v["records"])}
        group = groups.pop()
        sig = {
            "attempts": list(records), "created": stamp,
            "direction": "harder" if b == "all_solved" else "easier",
            "group": group, "rev": 0, "run": name,
            "solved": v["pass"], "task": tid, "total": v["graded"],
        }
        signals[tid] = (sig, records)
    return signals, rest


def _signals_from_run(run: Path, selected: Path | None) -> dict:
    """The signals of a run directory (``selected`` filters them, task ->
    {"group": N}), with the source path of every record they name."""
    if selected is not None:
        picks = {t: int(v["group"]) for t, v in json.load(open(selected)).items()}
    else:
        picks = {}
        for p in sorted((run / "signals").glob("*.json")):
            s = json.load(open(p))
            if s["task"] in picks:
                raise SystemExit(f"{run} holds two signals for {s['task']}; give --selected")
            picks[s["task"]] = int(s["group"])
    out = {}
    for tid, group in sorted(picks.items()):
        sig = json.load(open(run / "signals" / f"{tid}--g{group}.json"))
        out[tid] = (sig, {rel: run / rel for rel in sig["attempts"]})
    return out


def freeze(
    *,
    bench: Path,
    name: str,
    run: Path | None,
    eval_dir: Path | None,
    selected: Path | None,
    release: Path | None,
    mix: Path | None,
    sources: list[Path],
    log: Log,
) -> Path:
    """Snapshot a signal selection as ``<bench>/sets/<name>/``.

    Signals come from ``run`` (a directory in the run shape: a training run,
    or one a staging tool wrote; ``selected`` filters it) or are made from
    ``eval_dir``, a base-student eval of the whole set. The seed rows and
    packages come from a data release (``release``: its config, excludes
    included, goes into the manifest) or from ``mix`` + ``sources``. The set
    holds the signals and a hardlink of every rollout record they name, a
    hardlink of the seed mix, the selected tasks' original rows, and a
    manifest pinning each of those by sha256 and each task's seed package by
    a tree digest. Everything is checked before anything is written.
    """
    out = bench / "sets" / name
    if out.exists():
        raise SystemExit(f"{out} exists; a set is frozen once")
    release_info = None
    if release is not None:
        rm = json.load(open(release / "manifest.json"))
        release_info = {
            "path": str(release.resolve()),
            "release_sha256": rm["release_sha256"],
            "config": json.load(open(release / "config.json")),
        }
        mix = release / "mix.jsonl"
        sources = sorted((release / "sources").iterdir())
    assert mix is not None
    rows = _rows(mix)
    by_id = {_iid(r): r for r in rows}
    rest: dict[str, str] = {}
    if eval_dir is not None:
        src_launch = eval_dir / "launch.json"
        signals, rest = _signals_from_eval(eval_dir, name, log)
    else:
        assert run is not None
        src_launch = run / "launch.json"
        signals = _signals_from_run(run, selected)
    outside = sorted((set(signals) | set(rest)) - set(by_id))
    if outside:
        raise SystemExit(f"{len(outside)} tasks are not rows of {mix}: {outside[:5]}")

    packages: dict[str, dict] = {}
    # r0 is copied from the package and the signal measured the row: a task
    # whose package grader is not the row's would be rewritten from a grader
    # the student never met, so it is left out and listed.
    package_not_row: list[str] = []
    for tid, (sig, _) in sorted(signals.items()):
        if sig["task"] != tid or sig["direction"] not in ("harder", "easier"):
            raise SystemExit(f"{tid}: not a harder/easier signal: {sig}")
        # An arm root starts at the seed, and the loop takes only a signal at a
        # task's current revision.
        if sig["rev"] != 0:
            raise SystemExit(f"{tid}: signal at rev {sig['rev']}, a fresh root is at 0")
        found = [s for s in sources if (s / "tasks" / tid).is_dir()]
        if len(found) != 1:
            raise SystemExit(f"{tid}: package under {len(found)} of the sources, need exactly 1")
        pkg = found[0] / "tasks" / tid
        if (pkg / "tests" / "test.sh").read_text() != by_id[tid]["metadata"]["tmax"]["test_sh"]:
            package_not_row.append(tid)
            log(f"task={tid} left out: {pkg}/tests/test.sh is not the mix row's test_sh")
            continue
        packages[tid] = {"source": found[0].name, "tree_sha256": tree_sha256(pkg)}
    for tid in package_not_row:
        del signals[tid]

    dst = out / "runs" / name
    (dst / "signals").mkdir(parents=True)
    (dst / "rollouts").mkdir()
    files: dict[str, str] = {}
    directions: collections.Counter = collections.Counter()
    for tid, (sig, records) in sorted(signals.items()):
        for rel, src in records.items():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            os.link(src, dst / rel)
            files[f"runs/{name}/{rel}"] = layout.sha256_file(dst / rel)
        rel_sig = f"signals/{tid}--g{sig['group']}.json"
        layout.write_json_atomic(dst / rel_sig, sig)
        files[f"runs/{name}/{rel_sig}"] = layout.sha256_file(dst / rel_sig)
        directions[sig["direction"]] += 1
        log(
            f"task={tid} group={sig['group']} direction={sig['direction']} solved={sig['solved']}/{sig['total']} "
            f"records={len(records)} package={packages[tid]['source']}:{packages[tid]['tree_sha256'][:12]}"
        )
    shutil.copy2(src_launch, dst / "launch.json")
    files[f"runs/{name}/launch.json"] = layout.sha256_file(dst / "launch.json")
    os.link(mix, out / "mix.jsonl")
    files["mix.jsonl"] = layout.sha256_file(out / "mix.jsonl")
    layout.write_json_atomic(out / "selected.json", {t: {"group": s["group"]} for t, (s, _) in sorted(signals.items())})
    files["selected.json"] = layout.sha256_file(out / "selected.json")
    original = [r for r in rows if _iid(r) in signals]
    m = _write_evalset(
        out / "original.jsonl", original,
        source=str(Path(mix).resolve()),
        source_rows=f"the {len(original)} selected tasks' rows of the seed mix, in mix order",
        set=name, set_dir=str(out.resolve()),
    )
    files["original.jsonl"] = m["sha256"]
    # Which corpus each mix row came from, so a report can say how many repair rows the set holds.
    row_source = {}
    for s in sources:
        for t in (s / "tasks").iterdir():
            if t.name in by_id:
                row_source[t.name] = s.name
    manifest = {
        "name": name,
        "created": layout.stamp(),
        "bench_commit": _bench_commit(),
        "signals_from": {"run": str(run.resolve())} if run else {"eval": str(eval_dir.resolve())},
        "selected_from": str(selected.resolve()) if selected else None,
        "student_harness": _harness(json.load(open(src_launch))),
        "release": release_info,
        "seed_mix": {"path": str(Path(mix).resolve()), "sha256": files["mix.jsonl"], "rows": len(rows)},
        "sources": [str(s.resolve()) for s in sources],
        "rows_by_source": dict(sorted(collections.Counter(row_source.values()).items())),
        "row_source": row_source,
        "run": f"runs/{name}",
        "tasks": len(signals),
        "directions": dict(directions),
        # Rows the signal source scored that need no rewrite (mixed: usable as
        # they are) or carry no verdict (fewer than two graded attempts).
        "no_signal": dict(collections.Counter(rest.values())),
        "no_signal_tasks": rest,
        "package_not_row": package_not_row,
        "packages": packages,
        "files": files,
    }
    layout.write_json_atomic(out / "manifest.json", manifest)
    log(f"done set={out} tasks={len(signals)} directions={dict(directions)} no_signal={manifest['no_signal']} "
        f"package_not_row={package_not_row} files={len(files)}")
    print(f"{out}: {len(signals)} signals {dict(directions)}, no signal {manifest['no_signal']}, "
          f"left out (package grader is not the row's) {package_not_row}, {len(files)} files pinned")
    return out


def load_set(set_dir: Path, *, verify: bool) -> tuple[dict, str]:
    """A set's manifest and the manifest's own sha256; with ``verify``, every
    pinned file and package is checked against it."""
    mpath = set_dir / "manifest.json"
    manifest = json.load(open(mpath))
    if verify:
        bad = [r for r, sha in manifest["files"].items() if layout.sha256_file(set_dir / r) != sha]
        for tid, p in manifest["packages"].items():
            src = next(s for s in manifest["sources"] if Path(s).name == p["source"])
            if tree_sha256(Path(src) / "tasks" / tid) != p["tree_sha256"]:
                bad.append(f"package {tid}")
        if bad:
            raise SystemExit(f"{set_dir}: {len(bad)} pinned items changed: {bad[:5]}")
    return manifest, layout.sha256_file(mpath)


# ---------------------------------------------------------------------------- run


def load_arm(path: Path) -> dict:
    arm = json.load(open(path))
    if set(arm) != ARM_KEYS:
        raise SystemExit(f"{path}: an arm has exactly the keys {sorted(ARM_KEYS)}, got {sorted(arm)}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", arm["name"]):
        raise SystemExit(f"{path}: arm name {arm['name']!r} must be a plain file name")
    clash = sorted(set(arm["env"]) & (set(DRIVE_PINS) | set(DRIVE_OWNED)))
    if clash:
        raise SystemExit(f"{path}: env sets {clash}, which the bench pins or derives")
    if not all(isinstance(v, str) for v in arm["env"].values()):
        raise SystemExit(f"{path}: env values are strings, as the environment holds them")
    return arm


def run_arm(*, bench: Path, set_name: str, arm_path: Path, log: Log) -> Path:
    """Create the arm's root from the set and start offline_drive.sh for it as a
    systemd user unit (RUNBOOK, "Offline evolution of a signal subset"). A root
    that exists with the same arm and set is resumed: the drive restages only
    the tasks an infrastructure loss took, under a fresh ATTEMPTS_SINCE."""
    set_dir = bench / "sets" / set_name
    manifest, set_sha = load_set(set_dir, verify=True)
    arm = load_arm(arm_path)
    tt = Path(_profile(arm["profile"])["TRL_TT"])
    head = _git(tt, "rev-parse", "HEAD")
    if not head.startswith(arm["commit"]):
        raise SystemExit(f"profile {arm['profile']} checks out {tt} at {head[:12]}, the arm names {arm['commit']}")
    if _git(tt, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"{tt} has uncommitted changes; an arm runs a commit")
    codex_version = subprocess.run(
        [str(Path(arm["bin"]) / "codex"), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    root = bench / "arms" / f"{set_name}--{arm['name']}"
    record = {
        "arm": arm,
        "arm_file": str(arm_path.resolve()),
        "arm_commit": head,
        "codex_version": codex_version,
        "set": set_name,
        "set_dir": str(set_dir.resolve()),
        "set_manifest_sha256": set_sha,
        "drive_pins": DRIVE_PINS,
        "bench_commit": _bench_commit(),
    }
    rec_path = root / "bench_arm.json"
    if root.exists():
        prev = json.load(open(rec_path))
        same = {k: prev.get(k) for k in ("arm", "arm_commit", "set_manifest_sha256", "codex_version")}
        if same != {k: record[k] for k in same}:
            raise SystemExit(f"{root} holds another arm or set ({rec_path}); a new config is a new arm name")
        log(f"resume root={root}")
    else:
        py = Path(os.environ.get("TRL_VENV", "/scratch/gpfs/TRIDAO/al9080/titan-rl")) / "bin" / "python"
        subprocess.run(
            [
                str(py), str(tt / "torchtitan/experiments/rl/examples/tmax/new_root.py"),
                "--base", str(root), "--mix", str(set_dir / "mix.jsonl"),
                "--sources", *manifest["sources"], "--bin", arm["bin"], "--profile", arm["profile"],
                "--purpose", f"EvolveBench arm {arm['name']} on set {set_name} (manifest {set_sha[:12]}), {arm['commit']}",
            ],
            check=True,
        )
        record["created"] = layout.stamp()
        layout.write_json_atomic(rec_path, record)
        log(f"created root={root} arm={arm['name']} commit={head[:12]} codex={codex_version!r}")
    (root / "tmp").mkdir(exist_ok=True)
    env = {
        **arm["env"],
        **DRIVE_PINS,
        "TRL_PROFILE": arm["profile"],
        "TRL_BASE": str(root),
        "SOURCE_RUN": str(set_dir / manifest["run"]),
        "SELECTED": str(set_dir / "selected.json"),
        "WORKERS": str(arm["workers"]),
        "ATTEMPTS_SINCE": time.strftime("%Y%m%d-%H%M", time.gmtime()),
        "EVOLVE_SOCK_DIR": f"/dev/shm/{os.environ['USER']}-evolve-{root.name}",
        "TMPDIR": str(root / "tmp"),
    }
    unit = f"bench-{root.name}"
    cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect", f"--working-directory={root}"]
    cmd += [f"--setenv={k}={v}" for k, v in sorted(env.items())]
    cmd += ["bash", str(tt / "torchtitan/experiments/rl/examples/tmax/evolution/offline_drive.sh")]
    log(f"launch unit={unit} env={json.dumps(env, sort_keys=True)}")
    subprocess.run(cmd, check=True)
    print(f"{unit} started -> {root}; drive log {root}/logs/offline_drive--*.log")
    return root


# ------------------------------------------------------------------------ evalset


def _drive_finished(root: Path) -> bool:
    logs = sorted((root / "logs").glob("offline_drive--*.log"))
    return bool(logs) and logs[-1].read_text().rstrip().endswith("finished")


def build_evalset(root: Path, log: Log) -> Path:
    """The arm's accepted revisions: rows of live.jsonl for set tasks with
    metadata.rev > 0, in mix order (build_r100_evalset.py's rule)."""
    rec = json.load(open(root / "bench_arm.json"))
    if not _drive_finished(root):
        raise SystemExit(f"{root}: the newest offline_drive log does not end in 'finished'")
    _, set_sha = load_set(Path(rec["set_dir"]), verify=False)
    if set_sha != rec["set_manifest_sha256"]:
        raise SystemExit(f"{rec['set_dir']} changed since the arm ran")
    want = set(json.load(open(Path(rec["set_dir"]) / "selected.json")))
    live = layout.Root(root).mix.live_version()
    rows = [r for r in _rows(live[1]) if _iid(r) in want and int(r["metadata"].get("rev") or 0) > 0]
    for r in rows:
        log(f"task={_iid(r)} rev={r['metadata']['rev']}")
    path = root / "data" / "evalsets" / "bench-accepted.jsonl"
    m = _write_evalset(
        path, rows,
        source=str(live[1]),
        source_rows=f"live mix v{live[0]}: the set's tasks at rev > 0",
        set=rec["set"], set_dir=rec["set_dir"], set_manifest_sha256=set_sha, arm=rec["arm"]["name"],
    )
    log(f"done evalset={path} rows={len(rows)} sha256={m['sha256']}")
    print(f"{path}: {len(rows)} accepted revisions of {len(want)} set tasks")
    return path


# --------------------------------------------------------------------------- eval


def eval_env(evalset: Path, harness: str, environ: dict[str, str]) -> dict[str, str]:
    """The pinned student recipe over ``environ``; a caller value that
    disagrees with a pin is refused rather than silently replaced."""
    pins = {**STUDENT_RECIPE, "SWE_ROLLOUT_CONCURRENCY": ROLLOUT_CONCURRENCY, "TMAX_AGENT": harness}
    clash = {k: environ[k] for k in pins if k in environ and environ[k] != pins[k]}
    if clash:
        raise SystemExit(f"the environment sets {clash}, which the bench recipe pins to {[pins[k] for k in clash]}")
    manifest = json.load(open(evalset.with_name(evalset.stem + ".manifest.json")))
    if layout.sha256_file(evalset) != manifest["sha256"]:
        raise SystemExit(f"{evalset} no longer matches its manifest")
    set_harness = json.load(open(Path(manifest["set_dir"]) / "manifest.json"))["student_harness"]
    if harness != set_harness:
        raise SystemExit(f"the set's signals were measured under {set_harness}, not {harness}")
    return {**environ, **pins, "SWE_TB2_DATA": str(evalset)}


def run_eval(evalset: Path, ckpt: str, gpus: str, harness: str) -> None:
    """exec della/tb2_eval_local.sh under the recipe. The eval lands in
    <TRL_BASE>/evals/, TRL_BASE being the root or set the evalset belongs to.
    Launch it through gpu-pool on della-tridao; TRL_PROFILE names the checkout."""
    evalset = evalset.resolve()
    base = evalset.parent.parent.parent if evalset.parent.name == "evalsets" else evalset.parent
    env = eval_env(evalset, harness, dict(os.environ))
    env.update(TRL_BASE=str(base), RL_GPUS=gpus)
    script = _EVO / "della" / "tb2_eval_local.sh"
    os.execve("/bin/bash", ["bash", str(script), ckpt, gpus.split(",")[0]], env)


# ----------------------------------------------------------------- bands: evals


def read_eval(eval_dir: Path, evalset: Path | None = None) -> dict:
    """Per task of the evalset: graded attempts, passes, infra failures, the
    finish reasons and the row's rev, from the eval's validation index. An
    infra-failed attempt says nothing about the task and is counted apart.
    ``evalset`` is only for an eval launched before launch.json named it."""
    launch_path = eval_dir / "launch.json"
    launch = json.load(open(launch_path)) if launch_path.exists() else None
    if evalset is None:
        if launch is None:
            raise SystemExit(f"{eval_dir} has no launch.json; name its evalset")
        evalset = Path(launch["env"]["SWE_TB2_DATA"])
    sha = layout.sha256_file(evalset)
    if launch and launch.get("evalset_sha256") not in (None, sha):
        raise SystemExit(f"{evalset} is not the file {eval_dir} scored (sha256 differs)")
    rows = _rows(evalset)
    per: dict[str, dict[str, Any]] = {
        _iid(r): {"pass": 0, "graded": 0, "infra": 0, "finish": collections.Counter(),
                  "finish_failed": collections.Counter(), "rev": int(r["metadata"].get("rev") or 0)}
        for r in rows
    }
    for e in json.load(open(eval_dir / INDEX)):
        if e["task"] not in per:
            raise SystemExit(f"{eval_dir}: index scores {e['task']}, not in {evalset}")
        t = per[e["task"]]
        if e.get("infra_failed"):
            t["infra"] += 1
            continue
        t["graded"] += 1
        t["pass"] += e["state"] == "PASS"
        t["finish"][e.get("finish_reason") or "?"] += 1
        if e["state"] != "PASS":
            t["finish_failed"][e.get("finish_reason") or "?"] += 1
    tasks = {}
    for tid, v in per.items():
        b = "no_attempts" if v["graded"] + v["infra"] == 0 else bucket(v["pass"], v["graded"])
        tasks[tid] = {**v, "finish": dict(v["finish"]), "finish_failed": dict(v["finish_failed"]), "bucket": b}
    recipe = None
    if launch is not None:
        recipe = {"source": "eval", "harness": _harness(launch)}
        recipe.update({k: launch.get(k) for k in RECIPE_FIELDS})
        recipe.update({k: launch["env"].get(k) for k in STUDENT_RECIPE})
        recipe["SWE_ROLLOUT_CONCURRENCY"] = launch["env"].get("SWE_ROLLOUT_CONCURRENCY")
    return {
        "eval": str(eval_dir.resolve()),
        "launch": launch,
        "recipe": recipe,
        "evalset": {"path": str(evalset.resolve()), "sha256": sha, "rows": len(rows)},
        "tasks": tasks,
    }


def recipe_mismatch(a: dict | None, b: dict | None) -> dict:
    """The recipe fields two measurements disagree on; a missing recipe
    disagrees on all. Concurrency is pinned for new evals and differs only
    between old ones, where it changes timing, not the task: it is reported,
    not refused."""
    if a is None or b is None:
        return {"recipe": "unrecorded (made before launch.json recorded it)"}
    keys = [k for k in set(a) | set(b) if k != "SWE_ROLLOUT_CONCURRENCY"]
    out = {k: [a.get(k), b.get(k)] for k in sorted(keys) if a.get(k) != b.get(k)}
    # checkpoint None is the base model; the others are None only on an eval
    # launched before tb2_eval_local.sh recorded them.
    unrecorded = [k for k in RECIPE_FIELDS[1:] if k in a and a.get(k) is None]
    if unrecorded:
        out["unrecorded"] = unrecorded
    return out


def counts(tasks: dict) -> dict:
    """Three counts plus the two ways a task carries no verdict, and the
    finish-reason split over graded attempts (all, and failed only)."""
    c = collections.Counter(v["bucket"] for v in tasks.values())
    finish: collections.Counter = collections.Counter()
    failed: collections.Counter = collections.Counter()
    for v in tasks.values():
        finish.update(v["finish"])
        failed.update(v["finish_failed"])
    return {
        **{b: c[b] for b in BUCKETS},
        "tasks": len(tasks),
        "attempts_graded": sum(v["graded"] for v in tasks.values()),
        "attempts_passed": sum(v["pass"] for v in tasks.values()),
        "attempts_infra": sum(v["infra"] for v in tasks.values()),
        "finish": dict(finish.most_common()),
        "finish_failed": dict(failed.most_common()),
    }


def merge_evals(evals: list[dict]) -> dict:
    """Increments evaluated one eval each: disjoint task sets, one recipe."""
    tasks: dict = {}
    for e in evals:
        both = set(tasks) & set(e["tasks"])
        if both:
            raise SystemExit(f"{e['eval']} scores tasks another eval already did: {sorted(both)[:3]}")
        tasks.update(e["tasks"])
    for e in evals[1:]:
        # Increments made before launch.json recorded a field agree on not having it.
        mm = {k: v for k, v in recipe_mismatch(evals[0]["recipe"], e["recipe"]).items() if k != "unrecorded"}
        if mm:
            raise SystemExit(f"{e['eval']} ran another recipe than {evals[0]['eval']}: {mm}")
    return tasks


# ------------------------------------------------------------ bands: training runs


def first_samplings(runs: list[Path]) -> tuple[dict, dict]:
    """The first training group of every (task, rev) the runs sampled, as the
    eval's per-task shape, so the same band tables read off a live run: a new
    revision's first group is the policy's first contact with it. Training
    scores a solve at reward >= 1; an infra-failed or unscored attempt is
    counted apart, as in the eval."""
    groups: dict[tuple, dict[str, Any]] = {}
    launches = []
    for run in runs:
        launches.append(json.load(open(run / "launch.json")))
        for f in (run / "rollouts").glob("*/*.jsonl"):
            with open(f) as fh:
                h = json.loads(fh.readline())
            key = (h["task"], int(h.get("rev") or 0), run.name, h["group"])
            g = groups.setdefault(key, {"pass": 0, "graded": 0, "infra": 0, "finish": collections.Counter(),
                                        "finish_failed": collections.Counter(), "started": h.get("started") or ""})
            g["started"] = min(g["started"], h.get("started") or g["started"])
            r = h.get("reward")
            if h.get("infra_failed") or r is None or (isinstance(r, float) and math.isnan(r)):
                g["infra"] += 1
                continue
            g["graded"] += 1
            g["pass"] += r >= 1.0
            g["finish"][h.get("finish_reason") or "?"] += 1
            if r < 1.0:
                g["finish_failed"][h.get("finish_reason") or "?"] += 1
    first: dict[tuple, dict[str, Any]] = {}
    for (task, rev, run, group), g in sorted(groups.items(), key=lambda kv: kv[1]["started"]):
        if (task, rev) in first:
            continue
        first[(task, rev)] = {**g, "finish": dict(g["finish"]), "finish_failed": dict(g["finish_failed"]), "run": run, "group": group, "rev": rev,
                              "bucket": bucket(g["pass"], g["graded"])}
    harnesses = sorted({_harness(lc) for lc in launches})
    recipe = {"source": "run", "harness": harnesses[0] if len(harnesses) == 1 else harnesses,
              "SWE_GROUP_SIZE": sorted({lc.get("env", {}).get("SWE_GROUP_SIZE") for lc in launches}, key=str)}
    return first, recipe


# --------------------------------------------------------- rewrites: mechanical


def classify(rw: dict) -> str:
    """Why a rewrite ended where it did, from its status, stage and reason.
    ``infra:*`` classes are losses to infrastructure (offline_drive.sh
    restages them) and count in neither acceptance nor per-accepted cost."""
    st, stage, reason = rw["status"], rw.get("stage"), rw.get("reason") or ""
    if st == "accepted":
        return "accepted"
    if st == "running":
        return "in_flight"
    if st == "interrupted":
        return "infra:interrupted"
    if re.search(r"assertion", reason, re.I) and re.search(r"\b(limit|cap)\b", reason, re.I):
        return "gate:assertion_cap"
    if st == "kept":
        if reason.startswith("GIVE UP"):
            return "give_up"
        return "blocked" if reason.startswith("BLOCKED") else "kept:other"
    if st == "rejected":
        return f"gate:{stage}"
    if reason.startswith("Filtered"):
        return "provider_filter"
    if "TimeoutExpired" in reason:
        return "infra:session_timeout"
    m = re.search(r"codex session failed \((\w+)", reason)
    if m:
        return f"infra:{m.group(1)}"
    if re.search(r"semantic ?probe", reason, re.I):
        return "gate:semantic_probe"
    if re.search(r"blind verifier|verifier author|probe author", reason, re.I):
        return "gate:verifier_author"
    return "failed:other"


def _texts(d: Path) -> dict[str, str]:
    """Every text file under ``d`` (binary and very large files left out)."""
    out = {}
    if not d.is_dir():
        return out
    for p in sorted(d.rglob("*")):
        if p.is_file() and not p.is_symlink() and p.stat().st_size <= TEXT_LIMIT:
            b = p.read_bytes()
            if b"\0" not in b[:8192]:
                out[p.relative_to(d).as_posix()] = b.decode("utf-8", "replace")
    return out


def grader_hosts(pkg: Path) -> list[str]:
    """Non-package hosts a grader fetches from: URLs in any tests/ file that
    also runs a fetch (git clone, curl, wget, requests, urlopen). A grader
    that needs a live third-party host can fail on its own, whatever the
    student did."""
    hosts = set()
    for text in _texts(pkg / "tests").values():
        if not FETCH_RE.search(text):
            continue
        for m in URL_RE.finditer(text):
            h = (m.group(1) or m.group(2)).lower().rstrip(".")
            if h not in PACKAGE_HOSTS and h not in LOCAL_HOSTS and not h.endswith(".local"):
                hosts.add(h)
    return sorted(hosts)


def _words(path: Path) -> int:
    return len(path.read_text(errors="replace").split()) if path.is_file() else 0


def _last_agent_message(rw_dir: Path) -> str:
    """The author session's closing message (blind_bundle.py's reading)."""
    for j in sorted(rw_dir.glob("sessions/*--agent/codex/sessions/*/*/*/*.jsonl")):
        last = ""
        for line in open(j, errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            p = d.get("payload") or {}
            if d.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "assistant":
                last = " ".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
        if last:
            return last
    return ""


def diff_stats(old: Path, new: Path, claim_text: str) -> dict:
    """What an accepted rewrite changed, mechanically, r<input> -> r<result>."""
    t_old, t_new = _texts(old / "tests"), _texts(new / "tests")
    b_old = sum(len(s.encode()) for s in t_old.values())
    b_new = sum(len(s.encode()) for s in t_new.values())
    tests_changed = t_old != t_new
    rerun = False
    for name, text in t_new.items():
        added = [
            ln[1:] for ln in difflib.unified_diff(t_old.get(name, "").splitlines(), text.splitlines(), lineterm="", n=0)
            if ln.startswith("+") and not ln.startswith("+++")
        ]
        blob = "\n".join(added)
        if EXEC_RE.search(blob) and WRITE_RE.search(blob):
            rerun = True
    dock_old, dock_new = old / "environment" / "Dockerfile", new / "environment" / "Dockerfile"
    claims_unchanged = bool(CLAIM_UNCHANGED_RE.search(claim_text))
    return {
        "tests_bytes": [b_old, b_new],
        "tests_size_ratio": round(b_new / b_old, 3) if b_old else None,
        "assertions": [sum(len(ASSERT_RE.findall(s)) for s in t_old.values()),
                       sum(len(ASSERT_RE.findall(s)) for s in t_new.values())],
        "tests_changed": tests_changed,
        "adds_rerun_check": rerun,
        "instruction_words": [_words(old / "instruction.md"), _words(new / "instruction.md")],
        "dockerfile_changed": (dock_old.read_bytes() if dock_old.is_file() else None)
        != (dock_new.read_bytes() if dock_new.is_file() else None),
        "solution_changed": _texts(old / "solution") != _texts(new / "solution"),
        "claims_verifier_unchanged": claims_unchanged,
        "claim_mismatch": claims_unchanged and tests_changed,
    }


def _signal_band(root: Path, rw: dict) -> str | None:
    """The band of the group that asked for this rewrite, from its signal."""
    run, rest = rw["signal"].split("/", 1)
    p = root / "runs" / run / "signals" / f"{rest}.json"
    if not p.exists():
        return None
    s = json.load(open(p))
    return bucket(s["solved"], s["total"])


def rewrite_facts(root: Path, tid: str, rw_path: Path) -> dict:
    """One rewrite, mechanically: class, direction, cost, and for an accepted
    one what it changed and whether its grader needs a live third-party host."""
    rw = json.load(open(rw_path))
    rw_dir = rw_path.parent
    usd, _ = offline_cost.rewrite_usd(str(rw_dir))
    f = {
        "task": tid,
        "rewrite": rw_dir.name,
        "job": rw.get("job"),
        "status": rw["status"],
        "stage": rw.get("stage"),
        "class": classify(rw),
        "reason": (rw.get("reason") or "")[:300],
        "input_rev": rw.get("input_rev"),
        "result_rev": rw.get("result_rev"),
        "signal_band": _signal_band(root, rw) if rw.get("signal") else None,
        "usd": round(usd, 4),
        # Codex sessions record token counts (one that died before its first
        # turn records none and cost nothing); a Claude Code arm's do not.
        "cost_known": (rw.get("arm") or "codex") == "codex",
        "declared_repairs": len(rw.get("repair") or []),
    }
    task_dir = root / "evolution" / "tasks" / tid
    old = task_dir / f"r{rw.get('input_rev')}"
    f["hosts_before"] = grader_hosts(old) if old.is_dir() else None
    if rw["status"] == "accepted" and rw.get("result_rev") is not None:
        new = task_dir / f"r{rw['result_rev']}"
        notes = "\n".join(_texts(rw_dir / "notes").values())
        claim = "\n".join([notes, json.dumps(rw.get("simplify") or {}), json.dumps(rw.get("repair") or []),
                           _last_agent_message(rw_dir)])
        f["diff"] = diff_stats(old, new, claim)
        f["hosts_after"] = grader_hosts(new)
        f["has_solution"] = any((new / "solution" / s).is_file() for s in ("solve.sh", "gpt6_actions.json"))
    return f


def direction_table(rewrites: list[dict]) -> dict:
    """Accepted rewrites' after band by direction: harder -> mixed is the aim,
    -> none solved an overshoot, -> all solved a miss; easier mirrors it."""
    out = {}
    for job, over, stay in (("harder", "none_solved", "all_solved"), ("easier", "all_solved", "none_solved")):
        rs = [r for r in rewrites if r["job"] == job and r["class"] == "accepted" and r.get("after_band") in GRADED_BUCKETS]
        n = len(rs)
        c = collections.Counter(r["after_band"] for r in rs)
        out[job] = {"graded": n, "mixed": c["mixed"], "overshoot": c[over], "stays": c[stay],
                    "mixed_share": round(c["mixed"] / n, 3) if n else None,
                    "overshoot_share": round(c[over] / n, 3) if n else None,
                    "stays_share": round(c[stay] / n, 3) if n else None,
                    "accepted_unmeasured": sum(1 for r in rewrites if r["job"] == job and r["class"] == "accepted"
                                               and r.get("after_band") not in GRADED_BUCKETS)}
    return out


def rewrite_stats(rewrites: list[dict]) -> dict:
    """Mechanical diff statistics over accepted rewrites, by direction."""
    out = {}
    for job in ("harder", "easier"):
        rs = [r for r in rewrites if r["job"] == job and "diff" in r]
        if not rs:
            continue
        d = [r["diff"] for r in rs]
        ratios = sorted(x["tests_size_ratio"] for x in d if x["tests_size_ratio"] is not None)
        deltas = sorted(x["assertions"][1] - x["assertions"][0] for x in d)
        words = sorted(x["instruction_words"][1] - x["instruction_words"][0] for x in d)
        med = lambda xs: xs[len(xs) // 2] if xs else None  # noqa: E731
        out[job] = {
            "accepted": len(rs),
            "tests_size_ratio_median": med(ratios),
            "assertion_delta_median": med(deltas),
            "instruction_word_delta_median": med(words),
            "tests_changed": sum(x["tests_changed"] for x in d),
            "adds_rerun_check": sum(x["adds_rerun_check"] for x in d),
            "dockerfile_changed": sum(x["dockerfile_changed"] for x in d),
            "solution_changed": sum(x["solution_changed"] for x in d),
            "claim_mismatch": sum(x["claim_mismatch"] for x in d),
            "grader_network": {
                "introduced": sum(bool(r["hosts_after"]) and not r["hosts_before"] for r in rs),
                "kept": sum(bool(r["hosts_after"]) and bool(r["hosts_before"]) for r in rs),
                "removed": sum(not r["hosts_after"] and bool(r["hosts_before"]) for r in rs),
            },
        }
    return out


def task_final(rewrites: list[dict]) -> str:
    """A task's outcome: its newest rewrite's class (accepted is final, since
    the drive never restages an accepted task)."""
    if not rewrites:
        return "not_handled"
    return rewrites[-1]["class"]


def funnel(per_task: dict[str, list[dict]]) -> dict:
    final = {t: task_final(rs) for t, rs in per_task.items()}
    c = collections.Counter(final.values())
    verdicts = sum(n for k, n in c.items() if not k.startswith("infra:") and k not in ("in_flight", "not_handled"))
    all_rw = [r for rs in per_task.values() for r in rs]
    usd_counted = sum(r["usd"] for r in all_rw if not r["class"].startswith("infra:"))
    usd_infra = sum(r["usd"] for r in all_rw if r["class"].startswith("infra:"))
    return {
        "tasks": len(final),
        "final": dict(sorted(c.items())),
        "accepted": c["accepted"],
        "acceptance_rate": round(c["accepted"] / verdicts, 3) if verdicts else None,
        "give_ups": c["give_up"] + c["blocked"],
        "rewrites_by_class": dict(sorted(collections.Counter(r["class"] for r in all_rw).items())),
        "usd_counted": round(usd_counted, 2),
        "usd_infra": round(usd_infra, 2),
        "usd_per_accepted": round(usd_counted / c["accepted"], 2) if c["accepted"] else None,
        # A Claude Code arm records no Codex token counts: its cost is unknown, not 0.
        "rewrites_cost_unknown": sum(not r["cost_known"] for r in all_rw),
        "task_final": final,
    }


# ------------------------------------------------------------------------ repair


def defect_labels(root: Path, labels: Path | None) -> dict[str, dict]:
    """Known defects of repair rows: the HF judge's verdict and the missing
    reference solution from each repair source's metadata.parquet, plus an
    optional TSV (task_id, defect_class[, fixed][, note]) of labels checked by
    hand, whose ``fixed`` column is ground truth."""
    out: dict[str, dict] = {}
    sources = root / "data" / "sources"
    for src in sorted(sources.iterdir()) if sources.is_dir() else []:
        meta = src / "metadata.parquet"
        if "repair" not in src.name or not meta.exists():
            continue
        import pyarrow.parquet as pq

        for r in pq.read_table(meta).to_pylist():
            tid = r.get("task_id") or r.get("instance_id")
            classes = []
            v = (r.get("verdict") or "").upper()
            if v and v != "CLEAN":
                classes.append(f"hf:{v}")
            if str(r.get("has_reference_solution")) == "False":
                classes.append("no_reference_solution")
            out[tid] = {"source": src.name, "classes": classes or ["hf:none"], "ground_truth": {}}
    if labels is not None:
        for r in csv.DictReader(open(labels), delimiter="\t"):
            e = out.setdefault(r["task_id"], {"source": None, "classes": [], "ground_truth": {}})
            e["classes"].append(r["defect_class"])
            if r.get("fixed"):
                e["ground_truth"][r["defect_class"]] = r["fixed"] == "yes"
    return out


def mechanical_fix(defect: str, accepted: dict | None) -> bool | None:
    """Whether the accepted revision fixes ``defect``, where a mechanical check
    can say: a missing reference solution is fixed when the accepted revision
    ships one (the oracle gate then held it to the task); a grader's
    third-party fetch is fixed when the accepted grader fetches none. None
    where only reading the rewrite can tell."""
    if accepted is None:
        return False
    if defect == "no_reference_solution":
        return accepted["has_solution"]
    if defect in ("external_network", "grader_fetch"):
        return not accepted["hosts_after"]
    return None


def repair_report(per_task: dict[str, list[dict]], labels: dict[str, dict]) -> dict:
    """Per defect class over the repair rows the root handled: where they
    ended, how the accepted revision scored, and the fix rate where it is
    mechanical, checked against hand labels where those exist."""
    by: dict[str, dict[str, Any]] = collections.defaultdict(lambda: {"tasks": 0, "final": collections.Counter(), "after": collections.Counter(),
                                                           "declared_repair": 0, "fixed_mechanical": 0, "unfixed_mechanical": 0,
                                                           "ground_truth": {"agree": 0, "disagree": [], "fixed": 0, "labelled": 0}})
    for tid, lab in labels.items():
        if tid not in per_task:
            continue
        rs = per_task[tid]
        acc = next((r for r in reversed(rs) if r["class"] == "accepted"), None)
        for cls in lab["classes"]:
            e = by[cls]
            e["tasks"] += 1
            e["final"][task_final(rs)] += 1
            if acc:
                e["after"][acc.get("after_band") or "unmeasured"] += 1
                e["declared_repair"] += acc["declared_repairs"] > 0
            fix = mechanical_fix(cls, acc)
            if fix is True:
                e["fixed_mechanical"] += 1
            elif fix is False:
                e["unfixed_mechanical"] += 1
            if cls in lab["ground_truth"]:
                gt = e["ground_truth"]
                gt["labelled"] += 1
                gt["fixed"] += lab["ground_truth"][cls]
                if fix is not None:
                    if fix == lab["ground_truth"][cls]:
                        gt["agree"] += 1
                    else:
                        gt["disagree"].append(tid)
    return {k: {**v, "final": dict(v["final"]), "after": dict(v["after"])} for k, v in sorted(by.items())}


# -------------------------------------------------------------------------- tally


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p over the discordant pairs b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]."""
    r1, c1, n = a + b, a + c, a + b + c + d

    def p(x: int) -> float:
        return math.comb(c1, x) * math.comb(n - c1, r1 - x) / math.comb(n, r1)

    obs = p(a)
    lo, hi = max(0, r1 + c1 - n), min(r1, c1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs * (1 + 1e-9)))


def transitions(before: dict, after: dict, tasks: list[str]) -> dict:
    """Bucket a -> bucket b per task, and exact McNemar on "mixed" over the
    tasks both sides graded."""
    t = collections.Counter(f"{before[k]['bucket']}->{after[k]['bucket']}" for k in tasks)
    graded = [k for k in tasks if before[k]["bucket"] in GRADED_BUCKETS and after[k]["bucket"] in GRADED_BUCKETS]
    gained = sum(before[k]["bucket"] != "mixed" and after[k]["bucket"] == "mixed" for k in graded)
    lost = sum(before[k]["bucket"] == "mixed" and after[k]["bucket"] != "mixed" for k in graded)
    return {"tasks": len(tasks), "graded_both": len(graded), "table": dict(sorted(t.items())),
            "mixed_gained": gained, "mixed_lost": lost, "mcnemar_p": mcnemar_exact(gained, lost)}


def tally(
    *,
    evals: list[Path],
    runs: list[Path],
    evalset: Path | None,
    root: Path | None,
    set_dir: Path | None,
    before: list[Path],
    labels: Path | None,
    log: Log,
) -> dict:
    out: dict = {"bench_commit": _bench_commit(),
                 "inputs": {"evals": [], "runs": [str(r.resolve()) for r in runs], "before": [], "arm": None, "set": None, "labels": None}}
    after_tasks: dict = {}
    if evals and runs:
        raise SystemExit("one after-source per tally: evals or training runs")
    if evals:
        after = [read_eval(e, evalset) for e in evals]
        after_tasks = merge_evals(after)
        out["inputs"]["evals"] = [{k: e[k] for k in ("eval", "launch", "evalset")} for e in after]
        out["recipe"] = after[0]["recipe"]
        out["after"] = counts(after_tasks)
        out["after_tasks"] = after_tasks
        for tid, v in after_tasks.items():
            log(f"after task={tid} rev={v['rev']} bucket={v['bucket']} pass={v['pass']}/{v['graded']} infra={v['infra']} finish={v['finish']}")
    first = {}
    if runs:
        first, out["recipe"] = first_samplings(runs)
    if root is not None:
        rec_path = root / "bench_arm.json"
        rec = json.load(open(rec_path)) if rec_path.exists() else None
        set_dir = set_dir or (Path(rec["set_dir"]) if rec else None)
        if set_dir is not None:
            manifest, set_sha = load_set(set_dir, verify=False)
            if rec and rec["set_manifest_sha256"] != set_sha:
                raise SystemExit(f"{set_dir} changed since {root} ran")
            tasks = sorted(json.load(open(set_dir / "selected.json")))
            out["inputs"]["set"] = {"dir": str(set_dir.resolve()), "manifest_sha256": set_sha, "tasks": manifest["tasks"],
                                    "rows": manifest["seed_mix"]["rows"], "rows_by_source": manifest.get("rows_by_source"),
                                    "no_signal": manifest.get("no_signal", {}), "student_harness": manifest.get("student_harness"),
                                    "signals_from": manifest.get("signals_from")}
        else:
            # A live root: every task the loop handled.
            tasks = sorted(p.name for p in (root / "evolution" / "tasks").iterdir() if (p / "rewrites").is_dir())
        out["inputs"]["arm"] = rec
        per_task: dict[str, list[dict]] = {}
        for tid in tasks:
            per_task[tid] = []
            for p in sorted((root / "evolution" / "tasks" / tid / "rewrites").glob("*/rewrite.json")):
                f = rewrite_facts(root, tid, p)
                if f["class"] == "accepted":
                    if runs:
                        s = first.get((tid, f["result_rev"]))
                    else:
                        s = after_tasks.get(tid) if after_tasks.get(tid, {}).get("rev") == f["result_rev"] else None
                    f["after_band"] = s["bucket"] if s else None
                per_task[tid].append(f)
                log(f"rewrite task={tid} {f['rewrite']} job={f['job']} class={f['class']} usd={f['usd']:.4f} "
                    f"after={f.get('after_band')} diff={json.dumps(f.get('diff'))} hosts={f['hosts_before']}->{f.get('hosts_after')}")
        rewrites = [r for rs in per_task.values() for r in rs]
        out["funnel"] = funnel(per_task)
        out["directions"] = direction_table(rewrites)
        out["rewrite_stats"] = rewrite_stats(rewrites)
        mixed = sum(1 for r in rewrites if r.get("after_band") == "mixed")
        out["funnel"]["mixed_after"] = mixed
        out["funnel"]["usd_per_mixed"] = round(out["funnel"]["usd_counted"] / mixed, 2) if mixed else None
        # Yield over the set's rows is defined when one eval of all of them made the signals.
        if out["inputs"]["set"] and "eval" in (out["inputs"]["set"]["signals_from"] or {}):
            no_sig = out["inputs"]["set"]["no_signal"]
            out["set_yield"] = {"rows": out["inputs"]["set"]["rows"], "mixed_as_is": no_sig.get("mixed", 0),
                                "mixed_rewritten": mixed, "usable": no_sig.get("mixed", 0) + mixed}
        lab = defect_labels(root, labels)
        # The mechanical label: a seed grader that fetches from a third-party
        # host, on any row, since hand labels found that class accepted unfixed.
        for tid, rs in per_task.items():
            if rs and rs[0]["hosts_before"]:
                lab.setdefault(tid, {"source": None, "classes": [], "ground_truth": {}})["classes"].append("grader_fetch")
        if lab:
            out["inputs"]["labels"] = str(labels.resolve()) if labels else "metadata.parquet only"
            out["repair"] = repair_report(per_task, lab)
        out["rewrites"] = rewrites
        if evals:
            outside = sorted(set(after_tasks) - set(tasks))
            if outside:
                raise SystemExit(f"the evals score tasks outside the root's tasks: {outside[:3]}")
    if before:
        if not evals:
            raise SystemExit("--before pairs with evals of the accepted rows")
        b_evals = [read_eval(e) for e in before]
        b_tasks = merge_evals(b_evals)
        out["inputs"]["before"] = [{k: e[k] for k in ("eval", "launch", "evalset")} for e in b_evals]
        missing = sorted(set(after_tasks) - set(b_tasks))
        if missing:
            raise SystemExit(f"the before evals do not score {len(missing)} tasks: {missing[:3]}")
        common = sorted(after_tasks)
        out["before"] = counts({k: b_tasks[k] for k in common})
        out["before_after"] = {**transitions(b_tasks, after_tasks, common),
                               "recipe_mismatch": recipe_mismatch(out["recipe"], b_evals[0]["recipe"])}
        for k in common:
            log(f"transition task={k} {b_tasks[k]['bucket']}->{after_tasks[k]['bucket']}")
    return out


# ------------------------------------------------------------------------ compare


def _pct(n, d) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def _share(x) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def compare(results: list[tuple[str, dict]], allow_mismatch: bool) -> tuple[dict, str]:
    """Paired arm-vs-arm against the first, and one markdown table of all."""
    ref_label, ref = results[0]
    set_sha = lambda r: (r["inputs"].get("set") or {}).get("manifest_sha256")  # noqa: E731
    pairs, problems = [], []
    for label, r in results[1:]:
        mm = recipe_mismatch(ref.get("recipe"), r.get("recipe"))
        if set_sha(ref) != set_sha(r):
            mm["set_manifest_sha256"] = [set_sha(ref), set_sha(r)]
        if mm:
            problems.append(f"{ref_label} vs {label}: {mm}")
        pair: dict[str, Any] = {"arms": [ref_label, label], "mismatch": mm}
        if "after_tasks" in ref and "after_tasks" in r:
            common = sorted(set(ref["after_tasks"]) & set(r["after_tasks"]))
            pair["common_accepted"] = transitions(ref["after_tasks"], r["after_tasks"], common)
            a, b = ref["after"], r["after"]
            ga = a["mixed"] + a["all_solved"] + a["none_solved"]
            gb = b["mixed"] + b["all_solved"] + b["none_solved"]
            pair["fisher_mixed_unpaired_p"] = fisher_exact(a["mixed"], ga - a["mixed"], b["mixed"], gb - b["mixed"])
        if "funnel" in ref and "funnel" in r and not mm.get("set_manifest_sha256") and set_sha(ref):
            # Yield over the whole set: a task counts for an arm only when the arm
            # accepted it and its rewrite came out mixed.
            all_tasks = sorted(ref["funnel"]["task_final"])
            ym = lambda res: {x["task"] for x in res["rewrites"] if x.get("after_band") == "mixed"}  # noqa: E731
            ya, yb = ym(ref), ym(r)
            gained = sum(k not in ya and k in yb for k in all_tasks)
            lost = sum(k in ya and k not in yb for k in all_tasks)
            pair["set_yield"] = {"tasks": len(all_tasks), "mixed": [len(ya), len(yb)], "gained": gained, "lost": lost,
                                 "mcnemar_p": mcnemar_exact(gained, lost)}
        pairs.append(pair)
    if problems and not allow_mismatch:
        raise SystemExit("not comparable:\n  " + "\n  ".join(problems))

    lines = []
    if problems:
        lines += ["**NOT COMPARABLE** (compared anyway with --allow-mismatch):", ""] + [f"- {p}" for p in problems] + [""]
    lines += [
        "| arm | source / harness | accepted / handled | give-ups | gate / filter | infra-lost | harder -> mixed / overshoot / stays | easier -> mixed / overshoot / stays | mixed | all-solved | none-solved | no verdict | submit / context / time | $ counted | $ / accepted | $ / mixed | before -> after mixed (p) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, r in results:
        rec = r.get("recipe") or {}
        cells = [label, f"{rec.get('source', '?')} / {rec.get('harness', '?')}"]
        f = r.get("funnel")
        if f:
            fin = f["final"]
            gates = sum(n for k, n in fin.items() if k.startswith("gate:") or k == "provider_filter")
            infra = sum(n for k, n in fin.items() if k.startswith("infra:"))
            cells += [f"{f['accepted']} / {f['tasks']}", f["give_ups"], gates, infra]
            for job in ("harder", "easier"):
                d = r["directions"][job]
                cells.append(f"{_share(d['mixed_share'])} / {_share(d['overshoot_share'])} / {_share(d['stays_share'])} (n={d['graded']})")
        else:
            cells += ["-"] * 6
        a = r.get("after")
        if a:
            n = a["attempts_graded"]
            fin = a["finish"]
            cells += [a["mixed"], a["all_solved"], a["none_solved"], a["ungraded"] + a["no_attempts"],
                      " / ".join(_pct(fin.get(k, 0), n) for k in ("submit", "hit_context_limit", "hit_time_budget"))]
        else:
            cells += ["-"] * 5
        if f:
            cost = [f"{f['usd_counted']:,.2f}", f["usd_per_accepted"], f["usd_per_mixed"]]
            cells += ["unknown"] * 3 if f["rewrites_cost_unknown"] else cost
        else:
            cells += ["-"] * 3
        ba = r.get("before_after")
        cells.append(f"{r['before']['mixed']} -> {a['mixed']} (p={ba['mcnemar_p']:.3g})" if ba else "-")
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    for label, r in results:
        if r.get("set_yield"):
            y = r["set_yield"]
            lines.append(f"\n{label}: set yield {y['usable']} of {y['rows']} rows usable (mixed as-is {y['mixed_as_is']}, rewritten to mixed {y['mixed_rewritten']}).")
        if r.get("repair"):
            lines += ["", f"**{label} repair rows by known defect**", "",
                      "| defect | rows | accepted | after mixed / all / none | declared a repair | fixed (mechanical) | not fixed (mechanical) | hand labels: fixed / agree / disagree |",
                      "|---|---|---|---|---|---|---|---|"]
            for cls, e in r["repair"].items():
                af = e["after"]
                gt = e["ground_truth"]
                lines.append(f"| {cls} | {e['tasks']} | {e['final'].get('accepted', 0)} | "
                             f"{af.get('mixed', 0)} / {af.get('all_solved', 0)} / {af.get('none_solved', 0)} | {e['declared_repair']} | "
                             f"{e['fixed_mechanical']} | {e['unfixed_mechanical']} | "
                             f"{gt['fixed']}/{gt['labelled']} / {gt['agree']} / {', '.join(gt['disagree']) or 0} |")
        if r.get("rewrite_stats"):
            lines += ["", f"**{label} accepted rewrites, mechanically**", "",
                      "| direction | accepted | tests size x (median) | assertions delta (median) | instruction words delta (median) | adds re-run check | Dockerfile changed | claims verifier unchanged but tests differ | grader third-party fetch introduced / kept / removed |",
                      "|---|---|---|---|---|---|---|---|---|"]
            for job, s in r["rewrite_stats"].items():
                g = s["grader_network"]
                lines.append(f"| {job} | {s['accepted']} | {s['tests_size_ratio_median']} | {s['assertion_delta_median']} | "
                             f"{s['instruction_word_delta_median']} | {s['adds_rerun_check']} | {s['dockerfile_changed']} | "
                             f"{s['claim_mismatch']} | {g['introduced']} / {g['kept']} / {g['removed']} |")
    for p in pairs:
        if "common_accepted" in p:
            ca = p["common_accepted"]
            lines += ["", f"**{p['arms'][0]} vs {p['arms'][1]}**, tasks both accepted ({ca['graded_both']} graded by both): "
                      f"mixed gained {ca['mixed_gained']}, lost {ca['mixed_lost']}, exact McNemar p={ca['mcnemar_p']:.3g}; "
                      f"unpaired Fisher on mixed share p={p['fisher_mixed_unpaired_p']:.3g}."]
        if "set_yield" in p:
            y = p["set_yield"]
            lines.append(f"Whole set ({y['tasks']} tasks, mixed only if accepted): {y['mixed'][0]} vs {y['mixed'][1]}, "
                         f"gained {y['gained']}, lost {y['lost']}, exact McNemar p={y['mcnemar_p']:.3g}.")
        if "common_accepted" in p:
            lines += ["", "| " + p["arms"][0] + " -> " + p["arms"][1] + " | tasks |", "|---|---|"]
            lines += [f"| {k} | {v} |" for k, v in p["common_accepted"]["table"].items()]
    return {"arms": [lbl for lbl, _ in results], "pairs": pairs}, "\n".join(lines) + "\n"


# -------------------------------------------------------------------------- audit

AUDIT_LABELS = ("EFFECTIVE", "WEAK", "LEAKY", "UNFAIR", "BROKEN", "GRADER-TIGHTENING-ONLY", "UNSTATED-ENVIRONMENT-TRAP")
# A confirmed finding of these classes makes a reward wrong for some attempt.
DEFECT_LABELS = {"LEAKY", "UNFAIR", "BROKEN", "UNSTATED-ENVIRONMENT-TRAP"}


def _diff(a: Path, b: Path) -> str:
    return subprocess.run(["diff", "-ruN", str(a), str(b)], capture_output=True, text=True).stdout


def audit_bundle(*, arms: list[tuple[str, Path]], out: Path, key: Path, sample: int | None, seed: int, log: Log) -> None:
    """Blinded per-task bundles over the tasks every arm accepted (blind_bundle.py's
    format): the seed package once, and each arm's accepted revision as V1..Vn
    in a per-task random order, with diffs against r0 and the author's closing
    summary. The key that maps V<k> to an arm goes to ``key``, which must sit
    outside ``out``, so an auditor reading ``out`` cannot see it."""
    if out.resolve() in key.resolve().parents:
        raise SystemExit(f"{key} is inside {out}; the key has to stay outside the audit directory")
    if out.exists():
        raise SystemExit(f"{out} exists")
    accepted = {}
    for label, root in arms:
        acc = {}
        for p in (root / "evolution" / "tasks").glob("*/rewrites/*/rewrite.json"):
            rw = json.load(open(p))
            if rw["status"] == "accepted":
                acc[rw["task"]] = (rw, p.parent)
        accepted[label] = acc
    tasks = sorted(set.intersection(*(set(a) for a in accepted.values())))
    if sample is not None and sample < len(tasks):
        tasks = sorted(random.Random(seed).sample(tasks, sample))
    out.mkdir(parents=True)
    shutil.copy2(_EVO / "agents" / "bench_audit.md", out / "INSTRUCTIONS.md")
    shutil.copy2(_EVO / "agents" / "bench_audit_verify.md", out / "VERIFY.md")
    keymap = {}
    for t in tasks:
        order = [label for label, _ in arms]
        random.Random(f"{seed}:{t}").shuffle(order)
        keymap[t] = {f"V{i + 1}": a for i, a in enumerate(order)}
        td = out / t
        first_root = dict(arms)[order[0]]
        shutil.copytree(first_root / "evolution" / "tasks" / t / "r0", td / "original", symlinks=True)
        for v, label in keymap[t].items():
            rw, rw_dir = accepted[label][t]
            tdir = dict(arms)[label] / "evolution" / "tasks" / t
            r0, rn = tdir / f"r{rw['input_rev']}", tdir / f"r{rw['result_rev']}"
            vd = td / v
            vd.mkdir(parents=True)
            shutil.copytree(rn, vd / "package", symlinks=True)
            for part in ("instruction.md", "tests", "solution", "environment"):
                (vd / f"diff_{part.replace('.md', '')}.txt").write_text(_diff(r0 / part, rn / part))
            (vd / "agent_summary.md").write_text(_last_agent_message(rw_dir) or "(no agent summary found)")
            (vd / "direction.txt").write_text(f"{rw['job']}\n")
        log(f"task={t} variants={len(keymap[t])}")
    key.parent.mkdir(parents=True, exist_ok=True)
    layout.write_json_atomic(key, {"arms": {label: str(root.resolve()) for label, root in arms}, "seed": seed,
                                   "sample": sample, "tasks": keymap})
    print(f"{len(tasks)} tasks x {len(arms)} arms -> {out}; key -> {key}")


def audit_tally(*, audit: Path, key: Path, tallies: dict[str, dict]) -> dict:
    """Unblind the audit. ``audit/<task>/<V>/verdict.json`` is the auditor's
    {"label", "findings": [{"id", "class", ...}]}; ``verify.json`` beside it
    is the adversarial verifier's {"<finding id>": "confirmed" | "partly" |
    "refuted"}. A defect counts when confirmed or partly. Per arm: label
    rates, confirmed defects, those inside the mixed band (they feed wrong
    rewards), and $ per clean mixed task."""
    k = json.load(open(key))
    per: dict[str, dict[str, Any]] = {label: {"audited": 0, "labels": collections.Counter(), "defects": collections.Counter(),
                   "defect_tasks": set(), "mixed_defect_tasks": set(), "unverified": 0} for label in k["arms"]}
    for t, variants in k["tasks"].items():
        for v, label in variants.items():
            vp = audit / t / v / "verdict.json"
            if not vp.exists():
                continue
            verdict = json.load(open(vp))
            checks = json.load(open(audit / t / v / "verify.json")) if (audit / t / v / "verify.json").exists() else {}
            e = per[label]
            e["audited"] += 1
            e["labels"][verdict["label"]] += 1
            band = next((r.get("after_band") for r in tallies[label]["rewrites"] if r["task"] == t and r["class"] == "accepted"), None)
            for fnd in verdict.get("findings", []):
                if fnd["class"] not in DEFECT_LABELS:
                    continue
                state = checks.get(fnd["id"])
                if state is None:
                    e["unverified"] += 1
                if state in ("confirmed", "partly"):
                    e["defects"][fnd["class"]] += 1
                    e["defect_tasks"].add(t)
                    if band == "mixed":
                        e["mixed_defect_tasks"].add(t)
    out = {}
    for label, e in per.items():
        f = tallies[label]["funnel"]
        mixed = {r["task"] for r in tallies[label]["rewrites"] if r.get("after_band") == "mixed"}
        # Sampled audits read the defect rate inside the mixed band off the audited mixed tasks.
        audited = {t for t, vs in k["tasks"].items() if label in vs.values()}
        aud_mixed = mixed & audited
        rate = len(e["mixed_defect_tasks"]) / len(aud_mixed) if aud_mixed else None
        clean_mixed = len(mixed) * (1 - rate) if rate is not None else None
        out[label] = {
            "audited": e["audited"],
            "labels": dict(e["labels"]),
            "label_shares": {lab: round(e["labels"][lab] / e["audited"], 3) for lab in AUDIT_LABELS} if e["audited"] else {},
            "confirmed_defects": dict(e["defects"]),
            "defect_tasks": len(e["defect_tasks"]),
            "mixed_audited": len(aud_mixed),
            "mixed_defect_tasks": len(e["mixed_defect_tasks"]),
            "mixed_defect_rate": round(rate, 3) if rate is not None else None,
            "clean_mixed_estimate": round(clean_mixed, 1) if clean_mixed is not None else None,
            "usd_per_clean_mixed": round(f["usd_counted"] / clean_mixed, 2) if clean_mixed else None,
            "findings_unverified": e["unverified"],
        }
    return out


# --------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("freeze", help="snapshot a signal selection as a named set")
    p.add_argument("--bench", type=Path, required=True)
    p.add_argument("--name", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", type=Path, help="a directory in the run shape holding the signals")
    g.add_argument("--eval", type=Path, help="a base-student eval of the set: signals are made from it")
    p.add_argument("--selected", type=Path, help="with --run: task -> {group}; default every signal")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--release", type=Path, help="a data_release.py release: mix, sources and config")
    g.add_argument("--mix", type=Path, help="the seed mix the signals measured (with --sources)")
    p.add_argument("--sources", type=Path, nargs="+", default=[], help="with --mix: corpus directories holding tasks/<id>")
    p = sub.add_parser("run", help="create an arm's root and start its offline drive")
    p.add_argument("--bench", type=Path, required=True)
    p.add_argument("--set", required=True)
    p.add_argument("--arm", type=Path, required=True)
    p = sub.add_parser("evalset", help="an arm's accepted revisions as an evalset")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("eval", help="score an evalset under the pinned student recipe")
    p.add_argument("--evalset", type=Path, required=True)
    p.add_argument("--harness", choices=HARNESSES, required=True, help="the student harness (TMAX_AGENT)")
    p.add_argument("--ckpt", default="base", help="checkpoint directory, or base")
    p.add_argument("--gpus", required=True, help="RL_GPUS: trainer card, then generator card")
    p = sub.add_parser("tally", help="bands, funnel, rewrite stats, cost and repair as one JSON")
    p.add_argument("--eval", type=Path, nargs="*", default=[], help="eval(s) of the accepted revisions")
    p.add_argument("--run", type=Path, nargs="*", default=[], help="training run(s): bands from each revision's first group")
    p.add_argument("--evalset", type=Path, help="only for one eval made before launch.json named it")
    p.add_argument("--root", type=Path, help="the arm's (or a live) root: adds funnel, rewrite stats, cost, repair")
    p.add_argument("--set", type=Path, help="only for a root made before bench_arm.json")
    p.add_argument("--before", type=Path, nargs="*", default=[], help="eval(s) of the original rows")
    p.add_argument("--labels", type=Path, help="TSV task_id, defect_class[, fixed][, note] of known defects")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("compare", help="paired arm-vs-arm against the first, and a markdown table")
    p.add_argument("results", nargs="+", help="<label>=<tally json>; the first is the reference")
    p.add_argument("--out", type=Path, required=True, help="writes <out>.json and <out>.md")
    p.add_argument("--allow-mismatch", action="store_true", help="compare measurements of differing recipes, flagged")
    p = sub.add_parser("audit-bundle", help="blinded bundles of the tasks every arm accepted")
    p.add_argument("arms", nargs="+", help="<label>=<root>")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True, help="outside --out")
    p.add_argument("--sample", type=int, help="audit this many tasks; default all")
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("audit-tally", help="unblind an audit into per-arm rates")
    p.add_argument("--audit", type=Path, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("tallies", nargs="+", help="<label>=<tally json>, labels as in the key")
    p.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    if a.cmd == "freeze":
        if a.selected and not a.run:
            ap.error("--selected filters the signals of --run")
        if a.mix and not a.sources:
            ap.error("--mix needs --sources")
        freeze(bench=a.bench, name=a.name, run=a.run, eval_dir=a.eval, selected=a.selected,
               release=a.release, mix=a.mix, sources=a.sources, log=_log_for(a.bench, "freeze"))
    elif a.cmd == "run":
        run_arm(bench=a.bench, set_name=a.set, arm_path=a.arm, log=_log_for(a.bench, "run"))
    elif a.cmd == "evalset":
        build_evalset(a.root, _log_for(a.root, "evalset"))
    elif a.cmd == "eval":
        run_eval(a.evalset, a.ckpt, a.gpus, a.harness)
    elif a.cmd == "tally":
        if a.evalset and len(a.eval) != 1:
            ap.error("--evalset names the evalset of a single eval")
        if not (a.eval or a.run or a.root):
            ap.error("give --eval, --run or --root")
        log = Log(a.out.with_suffix(".log"))
        res = tally(evals=a.eval, runs=a.run, evalset=a.evalset, root=a.root, set_dir=a.set,
                    before=a.before, labels=a.labels, log=log)
        a.out.parent.mkdir(parents=True, exist_ok=True)
        layout.write_json_atomic(a.out, res)
        _, md = compare([(a.out.stem, res)], allow_mismatch=True)
        print(md, end="")
        print(f"-> {a.out} (log {log.path})")
    elif a.cmd == "compare":
        results = [(s.split("=", 1)[0], json.load(open(s.split("=", 1)[1]))) for s in a.results]
        res, md = compare(results, a.allow_mismatch)
        res["inputs"] = {s.split("=", 1)[0]: s.split("=", 1)[1] for s in a.results}
        res["bench_commit"] = _bench_commit()
        a.out.parent.mkdir(parents=True, exist_ok=True)
        layout.write_json_atomic(a.out.with_suffix(".json"), res)
        a.out.with_suffix(".md").write_text(md)
        print(md, end="")
        print(f"-> {a.out.with_suffix('.json')}, {a.out.with_suffix('.md')}")
    elif a.cmd == "audit-bundle":
        arms = [(s.split("=", 1)[0], Path(s.split("=", 1)[1])) for s in a.arms]
        audit_bundle(arms=arms, out=a.out, key=a.key, sample=a.sample, seed=a.seed, log=_log_for(a.out.parent, "audit-bundle"))
    elif a.cmd == "audit-tally":
        tallies = {s.split("=", 1)[0]: json.load(open(s.split("=", 1)[1])) for s in a.tallies}
        res = audit_tally(audit=a.audit, key=a.key, tallies=tallies)
        res = {"bench_commit": _bench_commit(), "audit": str(a.audit.resolve()), "key": str(a.key.resolve()),
               "tallies": {s.split("=", 1)[0]: s.split("=", 1)[1] for s in a.tallies}, "arms": res}
        a.out.parent.mkdir(parents=True, exist_ok=True)
        layout.write_json_atomic(a.out, res)
        print(json.dumps(res["arms"], indent=1))
        print(f"-> {a.out}")


if __name__ == "__main__":
    main()
