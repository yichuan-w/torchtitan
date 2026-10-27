#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""EvolveBench: one offline round of the rewriter over a frozen signal set, measured.

EVOLVE_BENCH.md says what it measures and why each pin exists. Subcommands, in
the order an arm goes through them:

    freeze        zero-signal rows of a mix, with their existing measurements (evals, training
                  runs' first groups) as signals -> <bench>/sets/<name>/, pinned by sha256
    run           an arm config + a set -> <bench>/arms/<set>--<arm>/, offline_drive.sh as a user unit
    pipeline      the same while the base eval still runs: signals staged as tasks finish (stage),
                  drive rounds picking them up, accepted revisions cut into eval waves (wave)
    evalset       an arm root -> its accepted revisions as data/evalsets/bench-accepted.jsonl
    eval          an evalset -> della/tb2_eval_local.sh under the pinned student recipe
    tally         evals and/or training runs [+ root] -> one JSON: bands, funnel, rewrite stats, cost, repair
    compare       tally JSONs -> paired transitions, exact McNemar, a markdown table
    audit-bundle  arm roots -> blinded per-task bundles for the LLM audit, key kept outside
    audit-run     invoke the isolated auditor or verifier over a bundle
    audit-tally   audit verdicts + key + tallies -> per-arm audit rates, $ per clean mixed task

Every subcommand logs one timestamped line per item to a file and prints where.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import csv
import datetime
import difflib
import glob
import hashlib
import json
import math
import os
import random
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
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
import rollout_record  # noqa: E402

# The student recipes a bench eval runs under. tb2_eval_local.sh exports one
# into the trainer and records it in launch.json's env (SWE_BENCH_RECIPE names
# it); tally reads it back, and compare refuses to pair two evals that differ
# on any of it. A set records the recipe its before-measurements were taken
# under, and every eval of its rewrites runs that one.
_COMMON = {
    "SWE_TB2_VAL_TEMPERATURE": "1.0",
    "SWE_TB2_VAL_TOP_P": "1.0",
    "SWE_MAX_NUM_SEQS": "256",
    "TMAX_TERMINUS_MAX_TURNS": "120",
    # vanillux's action cap (its default is 64), so every harness gets 120.
    "TMAX_CALL_LIMIT": "120",
    "TMAX_EXEC_TIMEOUT_SEC": "120",
    "TT_DAYTONA_CPU": "1",
    "TT_DAYTONA_MEM_GB": "2",
    "TT_DAYTONA_DISK_GB": "2",
    # The eval writes no evolution signals: what it measures never reaches a loop.
    "SWE_EVOLUTION_SIGNALS": "0",
}
STUDENT_RECIPES = {
    # A validation attempt rolled out the way a training rollout is, so a band
    # says whether the task gives a training group gradient. The values are
    # runbook/rltrain.env's and the training defaults of config_registry and
    # terminus.py; each SWE_EVAL_* below switches a validation-only behaviour
    # back to training's.
    "train-2026-10-25": {
        **_COMMON,
        # SWE_GROUP_SIZE in rltrain.env.
        "SWE_TB2_VAL_K": "16",
        # 80K context: the generator's max_model_len, the agent stopping 2048
        # tokens short of it (config_registry's headroom).
        "SWE_TMAX_CONTEXT": "81920",
        "SWE_MAX_CONTEXT_LEN": "79872",
        "TMAX_TURN_MAX_TOKENS": "16384",
        # 2 h per attempt: a row declaring nothing gets the budget, one declaring
        # its own max(declared, floor). The eval config reads the SWE_EVAL_
        # pair; SWE_TIME_BUDGET_SEC is training's name for the same value.
        "SWE_EVAL_TIME_BUDGET_SEC": "7200",
        "SWE_EVAL_BUDGET_FLOOR_SEC": "7200",
        "SWE_TIME_BUDGET_SEC": "7200",
        # Validation-only caps off, so turns and context are training's.
        "SWE_EVAL_MAX_TURNS": "0",
        "SWE_EVAL_MAX_CONTEXT_LEN": "0",
        "SWE_EVAL_AGENT_BUDGET_CAP_SEC": "0",
        "SWE_EVAL_VERIFIER_BUDGET_CAP_SEC": "0",
        # Training shows the context warning and the per-turn status line;
        # validation hides them unless asked. Style and thresholds are
        # terminus.py's defaults, which rltrain.env leaves in place.
        "SWE_EVAL_CONTEXT_NOTICE": "1",
        "TMAX_CONTEXT_NOTICE_STYLE": "checked",
        "TMAX_CONTEXT_WARN_TOKENS_LEFT": "24576",
        "TMAX_CONTEXT_REPEAT_TOKENS_LEFT": "8192",
        "TMAX_CONTEXT_STATUS_LINES": "1",
        # Training scores an attempt that never submitted 0; validation would
        # grade it at the verifier. The shadow verdict of a timed-out attempt
        # is not run, and a shadow verdict never enters the score.
        "SWE_EVAL_GRADE_UNSUBMITTED": "0",
        "TMAX_GRADE_UNSUBMITTED_TRAIN": "0",
        "SWE_EVAL_SHADOW_GRADE_TIMEOUTS": "0",
        # Training ends an episode whose opening turn breaks the format
        # (SWE_STRICT_OPENING defaults to 1); validation does not. Every-turn
        # format and truncation rules are off in both.
        "SWE_EVAL_STRICT_OPENING": "1",
        "SWE_EVAL_STRICT_FORMAT": "0",
        "SWE_EVAL_STRICT_TRUNCATION": "0",
        # The training dataset loads rows as they are; the eval config would
        # bake tmux into each row's Dockerfile.
        "SWE_TB2_BAKE_TMUX": "0",
    },
    # What the repair342 and r100 evals and both arms of agentpick400-measured
    # ran: the eval path's own behaviour, a 65536 context (the code's default,
    # so unset here), 2400 s for rows that declare nothing.
    "legacy-2400": {
        **_COMMON,
        "SWE_TB2_VAL_K": "5",
        "TMAX_TURN_MAX_TOKENS": "32768",
        "SWE_MAX_CONTEXT_LEN": "63488",
        "SWE_EVAL_TIME_BUDGET_SEC": "2400",
        "SWE_EVAL_BUDGET_FLOOR_SEC": "7200",
        "SWE_TIME_BUDGET_SEC": "2400",
    },
}
DEFAULT_RECIPE = "train-2026-10-25"
# Every key either recipe pins, so a recipe missing one reads as different.
RECIPE_KEYS = sorted({k for r in STUDENT_RECIPES.values() for k in r})


def set_recipe(manifest: dict) -> str:
    """The student recipe a set was measured under. A set frozen before sets
    recorded one was measured under the legacy recipe."""
    return manifest.get("student_recipe", "legacy-2400")


# The student harness is part of the recipe but chosen per eval (`eval --harness`).
HARNESSES = ("vanillux", "terminus", "miniswe")
# tb2_eval_local.sh runs one generator.
EVAL_GENERATORS = 1
# Attempts queue for a decode slot while their wall clock runs and read as
# time-budget failures: the repair342 base eval at 600 on one generator had 356
# of its last 400 attempts at the 2400 s budget, and at 128 it had 36 over all
# 1710. 128 per generator is the measured value; 256 (max_num_seqs) never was.
ROLLOUT_CONCURRENCY_PER_GENERATOR = 128
ROLLOUT_CONCURRENCY = str(ROLLOUT_CONCURRENCY_PER_GENERATOR * EVAL_GENERATORS)
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
# What `salvage` writes for an eval stopped before the controller wrote INDEX:
# the tasks whose k attempts all have records, in INDEX's entry shape.
SALVAGED = Path("salvaged-index.json")
RECORD_NAME = re.compile(r"g(-?\d+)-r(\d+)\.jsonl")

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
# RFC 2606 / 6761 names never resolve to a real host.
RESERVED_SUFFIXES = (".local", ".example", ".test", ".invalid", ".localhost")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "example.com", "example.org"}
FETCH_RE = re.compile(
    r"\bgit['\"]?[\s,]+['\"]?(clone|fetch|pull|ls-remote)\b|clone_from\(|\bcurl\b|\bwget\b"
    r"|requests\.(get|post|head)\(|urlopen\(|urlretrieve\(|httpx\.|aiohttp|pip\s+install\s+git\+"
)
URL_RE = re.compile(r"\b(?:https?|git|ssh)://([A-Za-z0-9.-]+)|\bgit@([A-Za-z0-9.-]+):")
# A new check that runs the submitted program on inputs the grader writes.
EXEC_RE = re.compile(r"subprocess\.(run|call|check_call|check_output|Popen)|os\.system\(|\bbash\s+-c\b|\bpython3?\s+/(app|home|tmp|workspace)")
WRITE_RE = re.compile(r"tempfile\.|mkdtemp|NamedTemporaryFile|write_text\(|write_bytes\(|open\([^)]*['\"][wa]b?['\"]|cat\s*>|>\s*/tmp/|shutil\.copy")

# Package-install signals are deliberately mechanical.  The rewrite metric
# reads only added instruction lines, while the rollout metric reads only the
# student's terminal actions (``rollout_record``), never the harness command
# that starts tmux or a sandbox.
INSTALL_GUIDANCE_RE = re.compile(
    r"(?:\bpip3?\b|\buv\b|\bconda\b|\bapt(?:-get)?\b).*\binstall\b"
    r"|\binstall\b|\bdownload\b|\bfetch\b|\b[A-Za-z0-9_.-]+==[0-9][^\s,;)]*\b",
    re.I,
)
INSTALL_COMMAND_RE = re.compile(
    r"(?:^|[;&|]\s*|\bsudo\s+|\bpython(?:\d+(?:\.\d+)?)?\s+-m\s+)"
    r"(?:pip3?|uv(?:\s+pip)?|conda|apt(?:-get)?|npm|cargo)\s+install\b",
    re.I | re.M,
)
DISK_FULL_RE = re.compile(r"no space left on device|errno\s*28", re.I)
DOCKER_INSTALL_RE = re.compile(
    r"\b(?:pip3?|uv(?:\s+pip)?|conda|apt(?:-get)?|npm|cargo)\s+(?:install|add)\b",
    re.I,
)
PACKAGE_SPEC_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]*)(?:==|@|:)([^\s\\'\"]+)")
WATCH_POLL_SEC = 60
GPU_POOL = Path.home() / ".claude" / "skills" / "gpu-pool" / "tool" / "pool.py"
ASSERT_RE = re.compile(r"\bassert\w*\b")
# The rewrite saying, of the verifier itself, that it did not change. A partial
# "otherwise unchanged" is not that claim, and neither is an untouched workspace.
_SUBJ = r"(?:verifier|tests?|checker|grader|test\.sh|test_\w+\.py)"
CLAIM_UNCHANGED_RE = re.compile(
    rf"\b{_SUBJ}\s+(?:remains?|remained|stays?|stayed|is|are|was|were)\s+(?:entirely\s+|completely\s+)?(?:unchanged|untouched)\b"
    rf"|\b(?:left|leaves?|keeps?|kept)\s+(?:the\s+)?(?:[\w-]+,?\s+(?:and\s+)?){{0,3}}{_SUBJ}\s+(?:unchanged|untouched|as[- ]is)\b"
    rf"|\b(?:no\s+changes?\s+to|without\s+(?:changing|modifying|touching))\s+(?:the\s+)?{_SUBJ}\b(?!['\u2019]s)",
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


def eval_index(ev: Path) -> list[dict]:
    """An eval's per-attempt index: the controller's, or for an eval stopped
    before writing it, what `salvage` kept."""
    return json.load(open(ev / INDEX if (ev / INDEX).exists() else ev / SALVAGED))


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


def _measured_by_eval(ev: Path) -> dict[str, dict]:
    """Each task an eval scored: all its attempts, banded as scored, with the
    test_sh of the row the eval read (the evalset launch.json names)."""
    launch = json.load(open(ev / "launch.json"))
    rows = {_iid(r): r for r in _rows(Path(launch["env"]["SWE_TB2_DATA"]))}
    per: dict[str, list[dict]] = collections.defaultdict(list)
    for e in eval_index(ev):
        state = "INFRA" if e.get("infra_failed") else e["state"]
        per[e["task"]].append({"state": state, "rel": e["record"], "src": _record_path(ev, e["record"])})
    out = {}
    for tid, attempts in per.items():
        b, passed, graded = _attempts_bucket(attempts)
        groups = {int(re.findall(r"g(-?\d+)-r\d+\.jsonl$", a["rel"])[0]) for a in attempts}
        if len(groups) != 1:
            raise SystemExit(f"{tid}: the eval's records span groups {sorted(groups)}")
        out[tid] = {
            "bucket": b, "solved": passed, "graded": graded, "group": groups.pop(),
            "records": {f"rollouts/{tid}/{Path(a['rel']).name}": a["src"] for a in sorted(attempts, key=lambda x: x["rel"])},
            "test_sh": rows[tid]["metadata"]["tmax"]["test_sh"],
            "source": {"kind": "eval", "dir": str(ev.resolve()), "tt_commit": launch.get("tt_commit"),
                       "harness": _harness(launch), "k": len(attempts)},
        }
    return out


def _measured_by_first_group(run: Path, k: int) -> dict[str, dict]:
    """Each task a training run sampled at rev 0: its first group (lowest group
    id), banded on its first ``k`` graded attempts in rollout order so the band
    means what a k-attempt eval's does; every record of the group is staged.
    The test_sh is the row of the mix the run trained on."""
    launch = json.load(open(run / "launch.json"))
    rows = {_iid(r): r for r in _rows(run / "inputs" / "mix.jsonl")}
    out = {}
    for tdir in sorted((run / "rollouts").iterdir()):
        names = [f.name for f in tdir.iterdir() if RECORD_NAME.fullmatch(f.name)]
        if not names:
            continue
        first = min(int(RECORD_NAME.fullmatch(n).group(1)) for n in names)
        recs = sorted((int(RECORD_NAME.fullmatch(n).group(2)), tdir / n) for n in names
                      if int(RECORD_NAME.fullmatch(n).group(1)) == first)
        heads = []
        for idx, f in recs:
            with open(f) as fh:
                heads.append((idx, f, json.loads(fh.readline())))
        if any(int(h.get("rev") or 0) != 0 for _, _, h in heads):
            continue  # the task's first group already ran a rewritten revision
        graded = [h for _, _, h in heads
                  if not h.get("infra_failed") and h.get("reward") is not None
                  and not (isinstance(h["reward"], float) and math.isnan(h["reward"]))][:k]
        passed = sum(h["reward"] >= 1.0 for h in graded)
        tid = tdir.name
        out[tid] = {
            "bucket": bucket(passed, len(graded)) if len(graded) == k else "ungraded",
            "solved": passed, "graded": len(graded), "group": first,
            "records": {f"rollouts/{tid}/{f.name}": f for _, f, _ in heads},
            "test_sh": rows[tid]["metadata"]["tmax"]["test_sh"] if tid in rows else None,
            "source": {"kind": "run_first_group", "dir": str(run.resolve()), "tt_commit": launch.get("tt_commit"),
                       "harness": _harness(launch), "k": k, "group_size": len(heads)},
        }
    return out


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
    evals: list[Path],
    first_group: list[Path],
    selected: Path | None,
    release: Path | None,
    mix: Path | None,
    sources: list[Path],
    log: Log,
    recipe: str = DEFAULT_RECIPE,
) -> Path:
    """Snapshot a signal selection as ``<bench>/sets/<name>/``.

    Signals come either from ``run``, a directory that already holds them
    (``selected`` filters it), or from existing measurements of the mix's
    rows: ``evals`` (each task's attempts in an eval) and ``first_group``
    (each task's first rev-0 group in a training run, banded on its first k
    graded attempts). For each row the first source that measured it, in the
    order given, is its before-measurement. Only zero-signal rows enter the
    set (none solved -> easier, all solved -> harder); every other row is
    listed with why it is out: mixed (it already carries gradient), ungraded,
    not measured, measured on a different test_sh than the row's, or a
    package whose grader is not the row's.

    The seed rows and packages come from a data release (``release``: its
    config, excludes included, goes into the manifest) or from ``mix`` +
    ``sources``. The set holds the signals and a hardlink of every rollout
    record they name, a hardlink of the seed mix, the selected rows, and a
    manifest pinning each of those by sha256, each task's seed package by a
    tree digest, and each task's before-measurement by its source.
    Everything is checked before anything is written.
    """
    out = bench / "sets" / name
    if out.exists():
        raise SystemExit(f"{out} exists; a set is frozen once")
    release_info = None
    if release is not None:
        rm = json.load(open(release / "manifest.json"))
        release_info = {"path": str(release.resolve()), "release_sha256": rm["release_sha256"],
                        "config": json.load(open(release / "config.json"))}
        mix = release / "mix.jsonl"
        sources = sorted((release / "sources").iterdir())
    assert mix is not None
    rows = _rows(mix)
    by_id = {_iid(r): r for r in rows}
    stamp = layout.stamp()
    dropped: dict[str, str] = {}
    measured: dict[str, dict] = {}
    signals: dict[str, tuple[dict, dict]] = {}
    if run is not None:
        signals = _signals_from_run(run, selected)
        launches = [json.load(open(run / "launch.json"))]
        src_launch = run / "launch.json"
    else:
        found: dict[str, dict] = {}
        for ev in evals:
            ran = json.load(open(ev / "launch.json"))["env"].get("SWE_BENCH_RECIPE")
            if ran is not None and ran != recipe:
                raise SystemExit(f"{ev} ran the {ran} recipe, not --recipe {recipe}")
            for tid, m in _measured_by_eval(ev).items():
                found.setdefault(tid, m)
        for r in first_group:
            for tid, m in _measured_by_first_group(r, int(STUDENT_RECIPES[recipe]["SWE_TB2_VAL_K"])).items():
                found.setdefault(tid, m)
        launches = [json.load(open(p / "launch.json")) for p in [*evals, *first_group]]
        src_launch = (evals or first_group)[0] / "launch.json"
        for tid in sorted(by_id):
            m = found.get(tid)
            if m is None:
                dropped[tid] = "not_measured"
            elif m["test_sh"] != by_id[tid]["metadata"]["tmax"]["test_sh"]:
                dropped[tid] = "measured_other_test_sh"
            elif m["bucket"] not in ("all_solved", "none_solved"):
                dropped[tid] = m["bucket"]
            else:
                measured[tid] = m
                sig = {"attempts": list(m["records"]), "created": stamp,
                       "direction": "harder" if m["bucket"] == "all_solved" else "easier",
                       "group": m["group"], "rev": 0, "run": name,
                       "solved": m["solved"], "task": tid, "total": m["graded"]}
                signals[tid] = (sig, m["records"])
            if tid in dropped:
                log(f"task={tid} dropped: {dropped[tid]}")
    harnesses = sorted({_harness(lc) for lc in launches})
    if len(harnesses) != 1:
        raise SystemExit(f"the before-measurements ran under different harnesses {harnesses}")
    outside = sorted(set(signals) - set(by_id))
    if outside:
        raise SystemExit(f"{len(outside)} tasks are not rows of {mix}: {outside[:5]}")

    packages: dict[str, dict] = {}
    for tid, (sig, _) in sorted(signals.items()):
        if sig["task"] != tid or sig["direction"] not in ("harder", "easier"):
            raise SystemExit(f"{tid}: not a harder/easier signal: {sig}")
        # An arm root starts at the seed, and the loop takes only a signal at a
        # task's current revision.
        if sig["rev"] != 0:
            raise SystemExit(f"{tid}: signal at rev {sig['rev']}, a fresh root is at 0")
        pkgs = [s for s in sources if (s / "tasks" / tid).is_dir()]
        if len(pkgs) != 1:
            raise SystemExit(f"{tid}: package under {len(pkgs)} of the sources, need exactly 1")
        pkg = pkgs[0] / "tasks" / tid
        # r0 is copied from this package and the signal measured the row: a
        # package grader that is not the row's would be rewritten from a
        # grader the student never met.
        if (pkg / "tests" / "test.sh").read_text() != by_id[tid]["metadata"]["tmax"]["test_sh"]:
            dropped[tid] = "package_not_row"
            log(f"task={tid} dropped: {pkg}/tests/test.sh is not the mix row's test_sh")
            continue
        packages[tid] = {"source": pkgs[0].name, "tree_sha256": tree_sha256(pkg)}
    for tid in [t for t in signals if dropped.get(t) == "package_not_row"]:
        del signals[tid]
        measured.pop(tid, None)

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
        log(f"task={tid} group={sig['group']} direction={sig['direction']} solved={sig['solved']}/{sig['total']} "
            f"records={len(records)} package={packages[tid]['source']}:{packages[tid]['tree_sha256'][:12]}")
    shutil.copy2(src_launch, dst / "launch.json")
    files[f"runs/{name}/launch.json"] = layout.sha256_file(dst / "launch.json")
    os.link(mix, out / "mix.jsonl")
    files["mix.jsonl"] = layout.sha256_file(out / "mix.jsonl")
    layout.write_json_atomic(out / "selected.json", {t: {"group": s["group"]} for t, (s, _) in sorted(signals.items())})
    files["selected.json"] = layout.sha256_file(out / "selected.json")
    original = [r for r in rows if _iid(r) in signals]
    m = _write_evalset(out / "original.jsonl", original, source=str(Path(mix).resolve()),
                       source_rows=f"the {len(original)} selected tasks' rows of the seed mix, in mix order",
                       set=name, set_dir=str(out.resolve()))
    files["original.jsonl"] = m["sha256"]
    # Which corpus each mix row came from, so a report can count repair rows.
    row_source = {}
    for s in sources:
        for t in (s / "tasks").iterdir():
            if t.name in by_id:
                row_source[t.name] = s.name
    by_corpus = collections.defaultdict(collections.Counter)
    for tid, (sig, _) in signals.items():
        by_corpus[row_source[tid]][sig["direction"]] += 1
    for tid, why in dropped.items():
        by_corpus[row_source.get(tid, "?")][f"dropped:{why}"] += 1
    manifest = {
        "name": name,
        "created": stamp,
        "bench_commit": _bench_commit(),
        "signals_from": {"run": str(run.resolve())} if run else
        {"evals": [str(e.resolve()) for e in evals], "first_group": [str(r.resolve()) for r in first_group]},
        "selected_from": str(selected.resolve()) if selected else None,
        "student_harness": harnesses[0],
        # The recipe the before-measurements were taken under; evals of the
        # set's rewrites run the same one.
        "student_recipe": recipe,
        "release": release_info,
        "seed_mix": {"path": str(Path(mix).resolve()), "sha256": files["mix.jsonl"], "rows": len(rows)},
        "sources": [str(s.resolve()) for s in sources],
        "rows_by_source": dict(sorted(collections.Counter(row_source.values()).items())),
        "row_source": row_source,
        "run": f"runs/{name}",
        "tasks": len(signals),
        "directions": dict(directions),
        "by_corpus": {k: dict(v) for k, v in sorted(by_corpus.items())},
        "dropped": dict(sorted(dropped.items())),
        "measured": {t: {k: v for k, v in mm.items() if k not in ("records", "test_sh")}
                     | {"test_sh_sha256": hashlib.sha256(mm["test_sh"].encode()).hexdigest()}
                     for t, mm in sorted(measured.items()) if t in signals},
        "packages": packages,
        "files": files,
    }
    layout.write_json_atomic(out / "manifest.json", manifest)
    log(f"done set={out} tasks={len(signals)} directions={dict(directions)} dropped={dict(collections.Counter(dropped.values()))}")
    print(f"{out}: {len(signals)} signals {dict(directions)}; by corpus {manifest['by_corpus']}; {len(files)} files pinned")
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


def _arm_checkout(arm: dict) -> tuple[Path, str, str]:
    """The arm's checkout (its profile's TRL_TT), its full commit, and the codex
    version in the arm's bin; refused when the checkout is not at the arm's
    commit or has uncommitted changes."""
    tt = Path(_profile(arm["profile"])["TRL_TT"])
    head = _git(tt, "rev-parse", "HEAD")
    if not head.startswith(arm["commit"]):
        raise SystemExit(f"profile {arm['profile']} checks out {tt} at {head[:12]}, the arm names {arm['commit']}")
    if _git(tt, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit(f"{tt} has uncommitted changes; an arm runs a commit")
    codex_version = subprocess.run(
        [str(Path(arm["bin"]) / "codex"), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return tt, head, codex_version


def _arm_root(*, root: Path, arm: dict, arm_path: Path, tt: Path, head: str, codex_version: str,
              mix: Path, sources: list[str], record: dict, log: Log) -> None:
    """Create the arm's root (new_root.py of the arm's checkout) and write
    bench_arm.json, or check that an existing root holds this same arm and set."""
    record = {"arm": arm, "arm_file": str(arm_path.resolve()), "arm_commit": head,
              "codex_version": codex_version, **record, "bench_commit": _bench_commit()}
    rec_path = root / "bench_arm.json"
    if root.exists():
        prev = json.load(open(rec_path))
        keys = ("arm_commit", "codex_version", "set", "stream")
        # The worker count is how fast the arm runs, not what it measures: a
        # relaunch may change it (fewer workers after provider 429s), recorded.
        same_arm = {k: v for k, v in prev["arm"].items() if k != "workers"} == \
            {k: v for k, v in record["arm"].items() if k != "workers"}
        if not same_arm or {k: prev.get(k) for k in keys} != {k: record.get(k) for k in keys}:
            raise SystemExit(f"{root} holds another arm or set ({rec_path}); a new config is a new arm name")
        prev.setdefault("relaunches", []).append({"at": layout.stamp(), "workers": record["arm"]["workers"]})
        layout.write_json_atomic(rec_path, prev)
        log(f"resume root={root} workers={record['arm']['workers']}")
        return
    py = Path(os.environ.get("TRL_VENV", "/scratch/gpfs/TRIDAO/al9080/titan-rl")) / "bin" / "python"
    subprocess.run(
        [str(py), str(tt / "torchtitan/experiments/rl/examples/tmax/new_root.py"),
         "--base", str(root), "--mix", str(mix), "--sources", *sources, "--bin", arm["bin"],
         "--profile", arm["profile"],
         "--purpose", f"EvolveBench arm {arm['name']} on set {record['set']}, {arm['commit']}"],
        check=True,
    )
    record["created"] = layout.stamp()
    layout.write_json_atomic(rec_path, record)
    log(f"created root={root} arm={arm['name']} commit={head[:12]} codex={codex_version!r}")


def _start_unit(unit: str, cwd: Path, env: dict[str, str], argv: list[str], log: Log) -> None:
    """A systemd user unit, the way RUNBOOK starts the drive on the login node."""
    cmd = ["systemd-run", "--user", f"--unit={unit}", "--collect", f"--working-directory={cwd}"]
    cmd += [f"--setenv={k}={v}" for k, v in sorted(env.items())]
    log(f"launch unit={unit} argv={argv} env={json.dumps(env, sort_keys=True)}")
    subprocess.run(cmd + argv, check=True)


def _drive_env(root: Path, arm: dict, pins: dict[str, str], source_run: Path, selected: Path) -> dict[str, str]:
    return {
        **arm["env"],
        **pins,
        "TRL_PROFILE": arm["profile"],
        "TRL_BASE": str(root),
        "SOURCE_RUN": str(source_run),
        "SELECTED": str(selected),
        "WORKERS": str(arm["workers"]),
        "ATTEMPTS_SINCE": time.strftime("%Y%m%d-%H%M", time.gmtime()),
        "EVOLVE_SOCK_DIR": f"/dev/shm/{os.environ['USER']}-evolve-{root.name}",
        "TMPDIR": str(root / "tmp"),
    }


# The drive script is the bench checkout's (its staging, and STREAM_DONE); the
# loop it runs is the arm checkout's, which evolveloop_env.sh finds through the
# arm's profile.
DRIVE = _EVO / "offline_drive.sh"


def run_arm(*, bench: Path, set_name: str, arm_path: Path, log: Log, workers: int | None = None,
            before: list[Path] | None = None, every: int = 40, minutes: int = 60,
            eval_host: str = "della-tridao", eval_gpus: str = "auto", no_auto_eval: bool = False) -> Path:
    """Create the arm's root from the set and start offline_drive.sh for it as a
    systemd user unit (RUNBOOK, "Offline evolution of a signal subset"). A root
    that exists with the same arm and set is resumed: the drive restages only
    the tasks an infrastructure loss took, under a fresh ATTEMPTS_SINCE."""
    set_dir = bench / "sets" / set_name
    manifest, set_sha = load_set(set_dir, verify=True)
    arm = load_arm(arm_path)
    if workers is not None:
        arm = {**arm, "workers": workers}
    tt, head, codex_version = _arm_checkout(arm)
    root = bench / "arms" / f"{set_name}--{arm['name']}"
    before = [p.resolve() for p in (before or [])]
    if root.exists() and (root / "bench_arm.json").exists():
        previous = json.load(open(root / "bench_arm.json"))
        requested = {"before": [str(p) for p in before],
                     "auto_eval": not no_auto_eval, "every": every, "minutes": minutes,
                     "eval_host": eval_host, "eval_gpus": eval_gpus}
        existing = {k: previous.get(k) for k in requested}
        if any(existing[k] is not None and existing[k] != v for k, v in requested.items()):
            raise SystemExit(f"{root} has a different watcher configuration; use the original run arguments")
    _arm_root(root=root, arm=arm, arm_path=arm_path, tt=tt, head=head, codex_version=codex_version,
              mix=set_dir / "mix.jsonl", sources=manifest["sources"], log=log,
              record={"set": set_name, "set_dir": str(set_dir.resolve()), "set_manifest_sha256": set_sha,
                      "drive_pins": DRIVE_PINS, "before": [str(p) for p in before],
                      "auto_eval": not no_auto_eval, "every": every, "minutes": minutes,
                      "eval_host": eval_host, "eval_gpus": eval_gpus})
    (root / "tmp").mkdir(exist_ok=True)
    unit = f"bench-{root.name}"
    _start_unit(unit, root, _drive_env(root, arm, DRIVE_PINS, set_dir / manifest["run"], set_dir / "selected.json"),
                ["bash", str(DRIVE)], log)
    if not no_auto_eval:
        py = Path(os.environ.get("TRL_VENV", "/scratch/gpfs/TRIDAO/al9080/titan-rl")) / "bin" / "python"
        _start_unit(f"bench-watch-{root.name}", root, {},
                    [str(py), str(Path(__file__).resolve()), "watch", "--root", str(root),
                     "--every", str(every), "--minutes", str(minutes), "--eval-host", eval_host,
                     "--eval-gpus", eval_gpus], log)
        print(f"{unit} and bench-watch-{root.name} started -> {root}; drive log {root}/logs/offline_drive--*.log")
    else:
        print(f"{unit} started -> {root}; drive log {root}/logs/offline_drive--*.log")
    return root


# ----------------------------------------------------------------------- streaming

# Streaming runs as many rounds as signals arrive in; MAX_ATTEMPTS still bounds
# each task's retries, so the round cap only has to stay out of the way.
STREAM_DRIVE_PINS = {**DRIVE_PINS, "MAX_ROUNDS": "30"}


def _stream_dir(bench: Path, name: str) -> Path:
    return bench / "sets" / f"{name}.stream"


def _eval_attempts(ev: Path, *, final: bool) -> dict[str, list[dict]]:
    """Each task's attempts: state (PASS / FAIL / INFRA), the record's path
    relative to its eval directory, and where it is. ``final`` reads the
    eval's own index.json; otherwise the records the controller has written so
    far (an attempt's record lands when it is graded, so a task with all k is
    decided; on the repair342 base eval the records' verdicts matched the
    index on 1710 of 1710 attempts)."""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    if final:
        for e in json.load(open(ev / INDEX)):
            state = "INFRA" if e.get("infra_failed") else e["state"]
            out[e["task"]].append({"state": state, "rel": e["record"], "src": _record_path(ev, e["record"])})
        return out
    for d in [ev, *sorted(Path(p) for p in glob.glob(glob.escape(str(ev)) + "--attempt-*"))]:
        for f in sorted(d.glob("validation_rollouts/*/*.jsonl")):
            try:
                with open(f) as fh:
                    h = json.loads(fh.readline())
            except (OSError, ValueError):
                continue  # being written
            state = "INFRA" if h.get("infra_failed") else ("PASS" if (h.get("reward") or 0) >= 1.0 else "FAIL")
            out[h["task"]].append({"state": state, "rel": str(f.relative_to(d)), "src": f})
    return out


def _attempts_bucket(attempts: list[dict]) -> tuple[str, int, int]:
    graded = [a for a in attempts if a["state"] != "INFRA"]
    passed = sum(a["state"] == "PASS" for a in graded)
    return bucket(passed, len(graded)), passed, len(graded)


def stage(*, bench: Path, name: str, eval_dir: Path, release: Path, root: Path, poll_sec: int, log: Log) -> None:
    """Stage first-round signals while the base eval runs: a task is decided
    once all k of its attempts have records, and its signal (harder for all
    solved, easier for none, at rev 0) goes into the stream's run directory,
    SELECTED growing with it. When the eval writes index.json, the set is
    frozen from that index (``freeze --eval``), every streamed decision is
    checked against it, the root's bench_arm.json gets the set's manifest
    sha256, and the done marker tells the drive SELECTED is complete."""
    sd = _stream_dir(bench, name)
    run = sd / "runs" / name
    state_path = sd / "stream.json"
    st = json.load(open(state_path))
    k = int(st["k"])
    rows = {_iid(r): r for r in _rows(release / "mix.jsonl")}
    sources = sorted((release / "sources").iterdir())
    while True:
        final = (eval_dir / INDEX).exists()
        per = _eval_attempts(eval_dir, final=False)
        new = 0
        for tid in sorted(per):
            if tid in st["decided"] or len(per[tid]) < k:
                continue
            b, passed, graded = _attempts_bucket(per[tid])
            if tid not in rows:
                raise SystemExit(f"{eval_dir} scores {tid}, not a row of {release}/mix.jsonl")
            st["decided"][tid] = b
            if b not in ("all_solved", "none_solved"):
                log(f"task={tid} {passed}/{graded} {b}: no signal")
                continue
            pkg = next((s / "tasks" / tid for s in sources if (s / "tasks" / tid).is_dir()), None)
            if pkg is None or (pkg / "tests" / "test.sh").read_text() != rows[tid]["metadata"]["tmax"]["test_sh"]:
                st["package_not_row"].append(tid)
                log(f"task={tid} left out: its package grader is not the mix row's test_sh")
                continue
            groups = {int(re.findall(r"g(-?\d+)-r\d+\.jsonl$", a["rel"])[0]) for a in per[tid]}
            if len(groups) != 1:
                raise SystemExit(f"{tid}: the eval's records span groups {sorted(groups)}")
            group = groups.pop()
            attempts = []
            for a in sorted(per[tid], key=lambda x: x["rel"]):
                rel_dst = f"rollouts/{tid}/{Path(a['rel']).name}"
                (run / rel_dst).parent.mkdir(parents=True, exist_ok=True)
                if not (run / rel_dst).exists():
                    os.link(a["src"], run / rel_dst)
                attempts.append(rel_dst)
            sig = {"attempts": attempts, "created": layout.stamp(),
                   "direction": "harder" if b == "all_solved" else "easier",
                   "group": group, "rev": 0, "run": name, "solved": passed, "task": tid, "total": graded}
            layout.write_json_atomic(run / "signals" / f"{tid}--g{group}.json", sig)
            st["selected"][tid] = {"group": group}
            new += 1
            log(f"task={tid} group={group} direction={sig['direction']} solved={passed}/{graded} staged")
        # The signal files exist before SELECTED names them.
        layout.write_json_atomic(sd / "selected.json", dict(sorted(st["selected"].items())))
        st["updated"] = layout.stamp()
        layout.write_json_atomic(state_path, st)
        log(f"poll decided={len(st['decided'])} staged={len(st['selected'])} new={new} final={final}")
        if final:
            break
        time.sleep(poll_sec)
    # The eval's own index is the record: freeze the set from it and check the stream.
    # A stager restarted after the freeze finds the set already there.
    set_dir = bench / "sets" / name
    if not (set_dir / "manifest.json").exists():
        set_dir = freeze(bench=bench, name=name, run=None, evals=[eval_dir], first_group=[], selected=None,
                         release=release, mix=None, sources=[], log=log)
    manifest, set_sha = load_set(set_dir, verify=False)
    final_attempts = _eval_attempts(eval_dir, final=True)
    mismatch = {t: [b, _attempts_bucket(final_attempts.get(t, []))[0]] for t, b in st["decided"].items()
                if _attempts_bucket(final_attempts.get(t, []))[0] != b}
    staged_not_set = sorted(set(st["selected"]) - set(manifest["packages"]))
    set_not_staged = sorted(set(manifest["packages"]) - set(st["selected"]))
    st["final_check"] = {"set_manifest_sha256": set_sha, "bucket_mismatch": mismatch,
                         "staged_not_in_set": staged_not_set, "set_not_staged": set_not_staged}
    layout.write_json_atomic(state_path, st)
    log(f"final check: bucket_mismatch={len(mismatch)} staged_not_in_set={staged_not_set} set_not_staged={set_not_staged}")
    # Tasks the stream had not decided when the index landed get their signal now.
    for tid in set_not_staged:
        sig = json.load(open(set_dir / manifest["run"] / "signals" / f"{tid}--g{json.load(open(set_dir / 'selected.json'))[tid]['group']}.json"))
        for rel in sig["attempts"]:
            (run / rel).parent.mkdir(parents=True, exist_ok=True)
            if not (run / rel).exists():
                os.link(set_dir / manifest["run"] / rel, run / rel)
        layout.write_json_atomic(run / "signals" / f"{tid}--g{sig['group']}.json", {**sig, "run": name})
        st["selected"][tid] = {"group": sig["group"]}
        log(f"task={tid} staged from the final index")
    layout.write_json_atomic(sd / "selected.json", dict(sorted(st["selected"].items())))
    rec_path = root / "bench_arm.json"
    rec = json.load(open(rec_path))
    rec["set_manifest_sha256"] = set_sha
    layout.write_json_atomic(rec_path, rec)
    (sd / "done").write_text(layout.stamp() + "\n")
    log(f"done set={set_dir} sha256={set_sha}")


def pipeline(*, bench: Path, name: str, eval_dir: Path, release: Path, arm_path: Path, poll_sec: int, log: Log) -> Path:
    """Rewrite while the base eval still runs. Starts two systemd user units:
    the stager (``stage``), which turns each task's finished attempts into a
    signal, and offline_drive.sh over the stream, whose rounds pick up what
    was staged since the last one. Accepted revisions are scored in waves
    (``wave``) by evals launched on a GPU the operator gives; those evals feed
    only the tally, never a signal or an acceptance."""
    if not (eval_dir / "launch.json").exists():
        raise SystemExit(f"{eval_dir} has no launch.json yet; start the base eval first")
    if _release_of(release / "mix.jsonl") is None:
        raise SystemExit(f"{release} is not a data release")
    launch = json.load(open(eval_dir / "launch.json"))
    if launch["env"].get("SWE_TB2_DATA") != str((release / "mix.jsonl").resolve()):
        raise SystemExit(f"{eval_dir} does not score {release}/mix.jsonl")
    arm = load_arm(arm_path)
    tt, head, codex_version = _arm_checkout(arm)
    sd = _stream_dir(bench, name)
    run = sd / "runs" / name
    if not sd.exists():
        (run / "signals").mkdir(parents=True)
        (run / "rollouts").mkdir()
        shutil.copy2(eval_dir / "launch.json", run / "launch.json")
        layout.write_json_atomic(sd / "selected.json", {})
        layout.write_json_atomic(sd / "stream.json", {
            "name": name, "created": layout.stamp(), "eval": str(eval_dir.resolve()),
            "release": str(release.resolve()), "k": int(launch["env"]["SWE_TB2_VAL_K"]),
            "student_harness": _harness(launch), "decided": {}, "selected": {}, "package_not_row": []})
    root = bench / "arms" / f"{name}--{arm['name']}"
    _arm_root(root=root, arm=arm, arm_path=arm_path, tt=tt, head=head, codex_version=codex_version,
              mix=release / "mix.jsonl", sources=[str(s) for s in sorted((release / "sources").iterdir())], log=log,
              record={"set": name, "set_dir": str((bench / "sets" / name).resolve()), "set_manifest_sha256": None,
                      "stream": str(sd.resolve()), "drive_pins": STREAM_DRIVE_PINS})
    (root / "tmp").mkdir(exist_ok=True)
    py = Path(os.environ.get("TRL_VENV", "/scratch/gpfs/TRIDAO/al9080/titan-rl")) / "bin" / "python"
    _start_unit(f"bench-stage-{name}", bench, {},
                [str(py), str(Path(__file__).resolve()), "stage", "--bench", str(bench), "--name", name,
                 "--eval", str(eval_dir), "--release", str(release), "--root", str(root),
                 "--poll", str(poll_sec)], log)
    env = _drive_env(root, arm, STREAM_DRIVE_PINS, run, sd / "selected.json")
    env["STREAM_DONE"] = str(sd / "done")
    _start_unit(f"bench-{root.name}", root, env, ["bash", str(DRIVE)], log)
    print(f"bench-stage-{name} and bench-{root.name} started -> {root}; stream {sd}")
    return root


def cut_wave(*, root: Path, every: int, minutes: int, log: Log) -> Path | None:
    """The next eval wave: accepted revisions of staged tasks that no earlier
    wave holds, cut once ``every`` of them have accumulated, or ``minutes``
    after the previous wave, or when the drive has finished. Returns the
    wave's evalset, or None when none is due."""
    rec = json.load(open(root / "bench_arm.json"))
    sd = Path(rec["stream"]) if rec.get("stream") else None
    staged = set(json.load(open((sd or Path(rec["set_dir"])) / "selected.json")))
    harness = json.load(open(sd / "stream.json"))["student_harness"] if sd else \
        json.load(open(Path(rec["set_dir"]) / "manifest.json"))["student_harness"]
    ev_dir = root / "data" / "evalsets"
    waves = sorted(ev_dir.glob("bench-wave-*.manifest.json"))
    done = {t for w in waves for t in json.load(open(w))["labels"]}
    live = layout.Root(root).mix.live_version()
    new = [r for r in _rows(live[1]) if _iid(r) in staged and _iid(r) not in done and int(r["metadata"].get("rev") or 0) > 0]
    last = waves[-1].stat().st_mtime if waves else (root / "bench_arm.json").stat().st_mtime
    age_min = (time.time() - last) / 60
    finished = _drive_finished(root)
    due = len(new) >= every or (new and (age_min >= minutes or finished))
    log(f"wave check new={len(new)} age_min={age_min:.0f} drive_finished={finished} due={bool(due)}")
    if not due:
        print(f"NO-WAVE new={len(new)} age_min={age_min:.0f} drive_finished={finished}")
        return None
    path = ev_dir / f"bench-wave-{len(waves) + 1:02d}.jsonl"
    m = _write_evalset(path, new, source=str(live[1]), source_rows=f"live mix v{live[0]}: accepted revisions no earlier wave holds",
                       set=rec["set"], set_dir=rec["set_dir"], arm=rec["arm"]["name"], student_harness=harness)
    for r in new:
        log(f"wave={path.name} task={_iid(r)} rev={r['metadata']['rev']}")
    print(f"WAVE {path} rows={len(new)} sha256={m['sha256'][:12]}")
    return path


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


def _release_of(evalset: Path) -> dict | None:
    """The data release whose mix.jsonl ``evalset`` is, or None."""
    m = evalset.parent / "manifest.json"
    if evalset.name != "mix.jsonl" or not m.exists():
        return None
    manifest = json.load(open(m))
    return manifest if "release_sha256" in manifest else None


def eval_env(evalset: Path, harness: str, environ: dict[str, str],
             concurrency: int = ROLLOUT_CONCURRENCY_PER_GENERATOR) -> dict[str, str]:
    """The default student recipe over ``environ``; a caller value that
    disagrees with a pin is refused rather than silently replaced. Every eval
    runs the default recipe, also for a set measured under another one: its
    before-measurement is then taken again under this recipe (`tally
    --before`), since tally and compare refuse to pair two recipes. The
    evalset is checked against what pinned it: a data release's manifest, or
    its own manifest, whose set (or wave) fixes the harness."""
    recipe = DEFAULT_RECIPE
    # A card whose KV cache holds fewer sequences than the cap queues the rest
    # in vLLM while their wall clock runs, the failure the cap exists to
    # prevent; such a card runs fewer. Never more than the measured cap.
    if not 1 <= concurrency <= ROLLOUT_CONCURRENCY_PER_GENERATOR:
        raise SystemExit(f"--concurrency {concurrency}: 1 to {ROLLOUT_CONCURRENCY_PER_GENERATOR} per generator")
    release = _release_of(evalset)
    if release is not None:
        want = release["files"]["mix.jsonl"]
    else:
        manifest = json.load(open(evalset.with_name(evalset.stem + ".manifest.json")))
        want = manifest["sha256"]
        set_manifest = (json.load(open(Path(manifest["set_dir"]) / "manifest.json"))
                        if (Path(manifest["set_dir"]) / "manifest.json").exists() else {})
        # A wave is cut before a streamed set is frozen, so it names both itself.
        set_harness = manifest.get("student_harness") or set_manifest["student_harness"]
        if harness != set_harness:
            raise SystemExit(f"the set's signals were measured under {set_harness}, not {harness}")
    pins = {**STUDENT_RECIPES[recipe], "SWE_BENCH_RECIPE": recipe,
            "SWE_ROLLOUT_CONCURRENCY": str(concurrency * EVAL_GENERATORS), "TMAX_AGENT": harness}
    clash = {k: environ[k] for k in pins if k in environ and environ[k] != pins[k]}
    if clash:
        raise SystemExit(f"the environment sets {clash}, which the {recipe} recipe pins to {[pins[k] for k in clash]}")
    if layout.sha256_file(evalset) != want:
        raise SystemExit(f"{evalset} no longer matches the sha256 its manifest pinned")
    return {**environ, **pins, "SWE_TB2_DATA": str(evalset)}


def salvage(eval_dir: Path, log: Log) -> Path:
    """An eval stopped before the controller wrote its index: keep each task
    whose k attempts all have records (the records the stager reads; on the
    repair342 base eval they matched the index on 1710 of 1710 attempts) as
    ``salvaged-index.json`` in the index's entry shape, and write the evalset's
    other rows as ``<evalset>.rest.jsonl`` for a new eval. tally and freeze read
    the salvaged index where the eval has no index of its own."""
    if (eval_dir / INDEX).exists():
        raise SystemExit(f"{eval_dir} finished with its own index; nothing to salvage")
    launch = json.load(open(eval_dir / "launch.json"))
    k = int(launch["env"]["SWE_TB2_VAL_K"])
    evalset = Path(launch["env"]["SWE_TB2_DATA"])
    if layout.sha256_file(evalset) != launch["evalset_sha256"]:
        raise SystemExit(f"{evalset} is not the file {eval_dir} scored")
    index, kept = [], []
    for tid, attempts in sorted(_eval_attempts(eval_dir, final=False).items()):
        rollouts = {RECORD_NAME.search(a["rel"]).group(2) for a in attempts}
        if len(attempts) != k or len(rollouts) != k:
            log(f"task={tid} records={len(attempts)} of {k}: rerun")
            continue
        for a in attempts:
            with open(a["src"]) as fh:
                h = json.loads(fh.readline())
            index.append({"task": tid, "state": "PASS" if a["state"] == "PASS" else "FAIL",
                          "infra_failed": a["state"] == "INFRA", "finish_reason": h.get("finish_reason"),
                          "record": a["rel"]})
        kept.append(tid)
        log(f"task={tid} records={k} of {k}: kept {_attempts_bucket(attempts)[0]}")
    layout.write_json_atomic(eval_dir / SALVAGED, index)
    parent = json.load(open(evalset.with_name(evalset.stem + ".manifest.json")))
    rest = [r for r in _rows(evalset) if _iid(r) not in set(kept)]
    meta = {k_: v for k_, v in parent.items() if k_ not in ("name", "rows", "sha256", "created", "labels")}
    meta.update(source=str(evalset), source_rows=f"the rows of {evalset.name} that {eval_dir.name} did not finish",
                parent_sha256=parent["sha256"], salvaged_from=str(eval_dir.resolve()))
    path = evalset.with_name(evalset.stem + ".rest.jsonl")
    if path.exists():
        raise SystemExit(f"{path} exists")
    _write_evalset(path, rest, **meta)
    log(f"done kept={len(kept)} rest={len(rest)} -> {eval_dir / SALVAGED}, {path}")
    print(f"{eval_dir}: kept {len(kept)} tasks ({len(index)} attempts); {len(rest)} rows to rerun in {path}")
    return path


def rest_evalset(evalset: Path, done: list[Path], log: Log) -> Path:
    """The rows of ``evalset`` that none of the ``done`` evals scored, as
    ``<evalset>.rest.jsonl``: what is left of an evalset some of whose shards
    have run, to score in one eval rather than one per shard."""
    parent = json.load(open(evalset.with_name(evalset.stem + ".manifest.json")))
    if layout.sha256_file(evalset) != parent["sha256"]:
        raise SystemExit(f"{evalset} no longer matches the sha256 its manifest pinned")
    scored = set()
    for ev in done:
        e = read_eval(ev)
        if not set(e["tasks"]) <= {_iid(r) for r in _rows(evalset)}:
            raise SystemExit(f"{ev} scored tasks that are not rows of {evalset}")
        scored |= set(e["tasks"])
        log(f"done eval={ev} tasks={len(e['tasks'])}")
    rest = [r for r in _rows(evalset) if _iid(r) not in scored]
    meta = {k: v for k, v in parent.items() if k not in ("name", "rows", "sha256", "created", "labels")}
    meta.update(source=str(evalset), source_rows=f"the rows of {evalset.name} the listed evals did not score",
                parent_sha256=parent["sha256"], done_evals=[str(e.resolve()) for e in done])
    path = evalset.with_name(evalset.stem + ".rest.jsonl")
    if path.exists():
        raise SystemExit(f"{path} exists")
    _write_evalset(path, rest, **meta)
    log(f"done rest={len(rest)} -> {path}")
    print(f"{path}: {len(rest)} of {len(_rows(evalset))} rows")
    return path


def shard_evalset(evalset: Path, shard: str, base: Path) -> Path:
    """Shard ``i/n`` of an evalset: its rows i, i+n, i+2n, ... as an evalset of
    their own, so one evalset is scored by n evals on n cards at once and
    tally merges them (disjoint tasks, one recipe). It goes beside the evalset
    when that sits in an evalsets/ directory, else under <base>/evalsets/, so
    a frozen set's directory is never written. Asking for the same shard again
    returns the same file."""
    i, n = (int(x) for x in shard.split("/"))
    if not 1 <= i <= n:
        raise SystemExit(f"--shard {shard}: need 1 <= i <= n")
    parent = json.load(open(evalset.with_name(evalset.stem + ".manifest.json")))
    if layout.sha256_file(evalset) != parent["sha256"]:
        raise SystemExit(f"{evalset} no longer matches the sha256 its manifest pinned")
    rows = _rows(evalset)[i - 1::n]
    out_dir = evalset.parent if evalset.parent.name == "evalsets" else base / "evalsets" / evalset.parent.name
    path = out_dir / f"{evalset.stem}.shard-{i:02d}-of-{n:02d}.jsonl"
    labels = [_iid(r) for r in rows]
    if path.exists():
        have = json.load(open(path.with_name(path.stem + ".manifest.json")))
        if have["labels"] != labels or have.get("parent_sha256") != parent["sha256"] \
                or layout.sha256_file(path) != have["sha256"]:
            raise SystemExit(f"{path} exists and is not shard {shard} of {evalset}")
        return path
    meta = {k: v for k, v in parent.items() if k not in ("name", "rows", "sha256", "created", "labels")}
    meta.update(source=str(evalset), source_rows=f"rows {i}, {i}+{n}, ... of {evalset.name} (shard {i} of {n})",
                parent_sha256=parent["sha256"], shard=[i, n])
    _write_evalset(path, rows, **meta)
    return path


def run_eval(evalset: Path, ckpt: str, gpus: str, harness: str, base: Path | None, shard: str | None = None,
             concurrency: int = ROLLOUT_CONCURRENCY_PER_GENERATOR) -> None:
    """exec della/tb2_eval_local.sh under the recipe. The eval lands in
    <base>/evals/: by default the root or set the evalset belongs to; a data
    release's mix names one (its own directory must stay as built). Launch it
    through gpu-pool on della-tridao; TRL_PROFILE names the checkout."""
    evalset = evalset.resolve()
    if base is None:
        if _release_of(evalset) is not None:
            raise SystemExit("a release's mix is scored into --base <dir>/evals/, never into the release")
        base = evalset.parent.parent.parent if evalset.parent.name == "evalsets" else evalset.parent
    if shard:
        if _release_of(evalset) is not None:
            raise SystemExit("--shard splits an evalset with its own manifest, not a release's mix")
        evalset = shard_evalset(evalset, shard, base)
    env = eval_env(evalset, harness, dict(os.environ), concurrency)
    env.update(TRL_BASE=str(base), RL_GPUS=gpus)
    script = _EVO / "della" / "tb2_eval_local.sh"
    os.execve("/bin/bash", ["bash", str(script), ckpt, gpus.split(",")[0]], env)


# ----------------------------------------------------------------------- watcher


def _watch_path(root: Path) -> Path:
    return root / "watcher.json"


def _load_watch_state(root: Path) -> dict:
    path = _watch_path(root)
    if path.exists():
        return json.load(open(path))
    return {"version": 1, "waves": {}, "tally": None}


def _save_watch_state(root: Path, state: dict) -> None:
    layout.write_json_atomic(_watch_path(root), state)


def _wave_path(manifest: Path) -> Path:
    return manifest.with_name(manifest.name.removesuffix(".manifest.json") + ".jsonl")


def _wave_entries(root: Path, state: dict) -> list[dict]:
    ev_dir = root / "data" / "evalsets"
    for manifest in sorted(ev_dir.glob("bench-wave-*.manifest.json")):
        path = _wave_path(manifest)
        key = str(path.resolve())
        state["waves"].setdefault(key, {"evalsets": [key], "evals": [], "launches": {}, "complete": False})
    return [state["waves"][key] for key in sorted(state["waves"])]


def _eval_dirs_for(evalset: Path, base: Path) -> list[Path]:
    """Find eval directories whose launch record names this exact evalset."""
    found = []
    want = str(evalset.resolve())
    for d in sorted(base.glob("*")):
        if not d.is_dir() or "--attempt-" in d.name or not (d / "launch.json").is_file():
            continue
        try:
            launch = json.load(open(d / "launch.json"))
            got = str(Path(launch.get("env", {}).get("SWE_TB2_DATA", "")).resolve())
        except (OSError, ValueError):
            continue
        if got == want:
            found.append(d)
    return found


def _eval_complete(path: Path) -> bool:
    return (path / INDEX).is_file() or (path / SALVAGED).is_file()


def _pool_label(about: str, log: Log) -> str:
    proc = subprocess.run([str(GPU_POOL), "label", about], capture_output=True, text=True, check=False)
    label = (proc.stdout or "").strip()
    log(f"pool label about={about!r} result={label!r} rc={proc.returncode}")
    return label or about


def _pool_pick(host: str, log: Log) -> str:
    proc = subprocess.run(
        [str(GPU_POOL), "pick", "--gpus", "1", "--time", "8h", "--json"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode:
        raise RuntimeError(f"pool pick exited {proc.returncode}: {(proc.stderr or '').strip()[-300:]}")
    try:
        options = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"pool pick returned non-JSON: {proc.stdout[-300:]}") from exc
    for option in options:
        if option.get("where") == host and option.get("gpus") is not None:
            card = str(option["gpus"]).split(",")[0]
            log(f"pool pick host={host} card={card} wait={option.get('wait')}")
            return card
    raise RuntimeError(f"pool has no free card on {host}")


def _eval_command(*, root: Path, evalset: Path, harness: str, card: str, marker: Path,
                  profile: str, py: Path, base: Path) -> str:
    """Build the remote command run under ``pool.py run`` for one wave."""
    script = _EVO / "evolve_bench.py"
    started = marker.with_suffix(".started")
    done = marker.with_suffix(".done")
    argv = [str(py), str(script), "eval", "--evalset", str(evalset), "--harness", harness,
            "--ckpt", "base", "--gpus", card, "--base", str(base)]
    command = " ".join(shlex.quote(x) for x in argv)
    return (
        f"mkdir -p {shlex.quote(str(marker.parent))}; date -Is > {shlex.quote(str(started))}; "
        f"export TRL_PROFILE={shlex.quote(profile)} TRL_BASE={shlex.quote(str(base))}; "
        f"set +e; {command}; rc=$?; printf '%s\\n' \"$rc\" > {shlex.quote(str(done))}; exit $rc"
    )


def _launch_wave_eval(*, root: Path, evalset: Path, wave: str, card: str, host: str,
                      harness: str, profile: str, py: Path, log: Log) -> dict:
    base = root
    marker = root / "logs" / "evals" / f"{Path(wave).stem}--gpu-{card}"
    marker.with_suffix(".started").unlink(missing_ok=True)
    marker.with_suffix(".done").unlink(missing_ok=True)
    about = f"EvolveBench {root.name} {Path(wave).stem} student eval"
    label = _pool_label(about, log)
    command = _eval_command(root=root, evalset=evalset, harness=harness, card=card, marker=marker,
                             profile=profile, py=py, base=base)
    argv = [str(GPU_POOL), "run", host, card, "--note", "Zhifei: eval", "--about", label,
            "--hours", "8", "--exclusive", "--", command]
    log(f"wave={wave} launch card={card} host={host} evalset={evalset} argv={argv}")
    subprocess.run(argv, check=True)
    return {"evalset": str(evalset.resolve()), "card": card, "host": host,
            "marker": str(marker), "launched": layout.stamp()}


def _recover_eval(*, root: Path, evalset: Path, launch: dict, log: Log) -> Path | None:
    """Salvage a failed eval and return the rest evalset, if any rows remain."""
    evals = _eval_dirs_for(evalset, root / "evals")
    if not evals:
        log(f"evalset={evalset} failed without an eval directory; retrying whole evalset")
        return evalset
    ev = evals[-1]
    if not (ev / INDEX).exists() and not (ev / SALVAGED).exists():
        rest = salvage(ev, log)
    else:
        rest = evalset
    if rest is None or not _rows(rest):
        return None
    log(f"evalset={evalset} salvage={ev} rest={rest}")
    return rest


def watch_once(*, root: Path, every: int, minutes: int, eval_host: str, eval_gpus: str,
               log: Log, py: Path | None = None) -> bool:
    """Advance wave cutting/eval recovery once; return true after final tally."""
    rec = json.load(open(root / "bench_arm.json"))
    state = _load_watch_state(root)
    _wave_entries(root, state)
    wave = cut_wave(root=root, every=every, minutes=minutes, log=log)
    if wave is not None:
        key = str(wave.resolve())
        state["waves"].setdefault(key, {"evalsets": [key], "evals": [], "launches": {}, "complete": False})
        _save_watch_state(root, state)
        log(f"wave={wave.name} cut")
    py = py or Path(os.environ.get("TRL_VENV", "/scratch/gpfs/TRIDAO/al9080/titan-rl")) / "bin" / "python"
    cards = [x.strip() for x in eval_gpus.split(",") if x.strip()] if eval_gpus != "auto" else None
    used_cards = set()
    for key, entry in sorted(state["waves"].items()):
        evalsets = [Path(x) for x in entry["evalsets"]]
        # Completed evals are discovered from their launch.json, so a watcher
        # restart never reruns a finished wave.
        for evalset in list(evalsets):
            found = [x for x in _eval_dirs_for(evalset, root / "evals") if _eval_complete(x)]
            for ev in found:
                if str(ev.resolve()) not in entry["evals"]:
                    entry["evals"].append(str(ev.resolve()))
                    log(f"wave={Path(key).name} eval_done={ev}")
            launch = entry["launches"].get(str(evalset.resolve()))
            if launch:
                marker = Path(launch["marker"])
                done = marker.with_suffix(".done")
                if done.exists():
                    rc = done.read_text().strip()
                    if rc != "0" and not any(_eval_complete(x) for x in _eval_dirs_for(evalset, root / "evals")):
                        rest = _recover_eval(root=root, evalset=evalset, launch=launch, log=log)
                        for ev in _eval_dirs_for(evalset, root / "evals"):
                            if _eval_complete(ev) and str(ev.resolve()) not in entry["evals"]:
                                entry["evals"].append(str(ev.resolve()))
                        entry["launches"].pop(str(evalset.resolve()), None)
                        if rest is not None and str(rest.resolve()) not in entry["evalsets"]:
                            entry["evalsets"].append(str(rest.resolve()))
                    else:
                        entry["launches"].pop(str(evalset.resolve()), None)
                else:
                    used_cards.add(launch["card"])
        pending = [Path(x) for x in entry["evalsets"]
                   if not any(str(Path(ev).resolve()) in entry["evals"] for ev in _eval_dirs_for(Path(x), root / "evals"))
                   and str(Path(x).resolve()) not in entry["launches"]]
        harness = json.load(open(Path(key).with_name(Path(key).stem + ".manifest.json"))).get("student_harness", "terminus")
        for evalset in pending:
            available = [c for c in cards or [] if c not in used_cards]
            if not available and cards is not None:
                break
            try:
                card = available[0] if available else _pool_pick(eval_host, log)
                launch = _launch_wave_eval(root=root, evalset=evalset, wave=Path(key).name, card=card,
                                           host=eval_host, harness=harness, profile=rec["arm"]["profile"], py=py, log=log)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
                log(f"wave={Path(key).name} evalset={evalset} launch deferred: {exc}")
                continue
            entry["launches"][str(evalset.resolve())] = launch
            used_cards.add(card)
        entry["complete"] = bool(entry["evalsets"]) and all(
            any(str(Path(ev).resolve()) in entry["evals"] for ev in _eval_dirs_for(Path(es), root / "evals"))
            for es in entry["evalsets"]
        ) and not entry["launches"]
        log(f"wave={Path(key).name} evalsets={len(entry['evalsets'])} evals={len(entry['evals'])} "
            f"active={len(entry['launches'])} complete={entry['complete']}")
    _save_watch_state(root, state)
    finished = _drive_finished(root)
    if not finished or not all(x["complete"] for x in state["waves"].values()):
        log(f"watch wait drive_finished={finished} waves={len(state['waves'])}")
        return False
    result = root.parent.parent / "results" / f"{rec['arm']['name']}.json"
    if result.exists():
        state["tally"] = str(result)
        _save_watch_state(root, state)
        return True
    evals = [Path(x) for entry in state["waves"].values() for x in entry["evals"]]
    # With no accepted revisions there is no after eval to pair with --before;
    # tally still writes the arm funnel and keeps the recorded before paths in
    # bench_arm.json for the manual path.
    before = [Path(x) for x in rec.get("before", [])] if evals else []
    log(f"tally start evals={evals} before={before} result={result}")
    labels_path = _EVO / "data_evolve_bench_defects.tsv"
    labels = labels_path if labels_path.is_file() else None
    log(f"tally labels={labels}")
    res = tally(evals=evals, runs=[], evalset=None, root=root, set_dir=None, before=before,
                labels=labels, log=log)
    result.parent.mkdir(parents=True, exist_ok=True)
    layout.write_json_atomic(result, res)
    state["tally"] = str(result)
    _save_watch_state(root, state)
    log(f"tally done result={result}")
    return True


def watch(*, root: Path, every: int, minutes: int, eval_host: str, eval_gpus: str, poll_sec: int = WATCH_POLL_SEC) -> None:
    """Watch an arm until its waves are scored and its result is tallied."""
    log = _log_for(root, "watcher")
    while not watch_once(root=root, every=every, minutes=minutes, eval_host=eval_host,
                         eval_gpus=eval_gpus, log=log):
        time.sleep(poll_sec)


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
    index = eval_index(eval_dir)
    if not (eval_dir / INDEX).exists():
        # A salvaged eval covers only the tasks it finished; its evalset's
        # other rows are another eval's.
        kept = {e["task"] for e in index}
        rows = [r for r in rows if _iid(r) in kept]
    per: dict[str, dict[str, Any]] = {
        _iid(r): {"pass": 0, "graded": 0, "infra": 0, "finish": collections.Counter(),
                  "finish_failed": collections.Counter(), "rev": int(r["metadata"].get("rev") or 0),
                  "attempts": []}
        for r in rows
    }
    for e in index:
        if e["task"] not in per:
            raise SystemExit(f"{eval_dir}: index scores {e['task']}, not in {evalset}")
        t = per[e["task"]]
        if e.get("infra_failed"):
            t["infra"] += 1
            t["attempts"].append({"record": e["record"], "state": "INFRA"})
            continue
        t["graded"] += 1
        t["pass"] += e["state"] == "PASS"
        t["attempts"].append({"record": e["record"], "state": e["state"]})
        t["finish"][e.get("finish_reason") or "?"] += 1
        if e["state"] != "PASS":
            t["finish_failed"][e.get("finish_reason") or "?"] += 1
    tasks = {}
    for tid, v in per.items():
        b = "no_attempts" if v["graded"] + v["infra"] == 0 else bucket(v["pass"], v["graded"])
        tasks[tid] = {**v, "finish": dict(v["finish"]), "finish_failed": dict(v["finish_failed"]), "bucket": b,
                      "attempts": list(v["attempts"])}
    recipe = None
    if launch is not None:
        recipe = {"source": "eval", "harness": _harness(launch)}
        recipe.update({k: launch.get(k) for k in RECIPE_FIELDS})
        recipe["student_recipe"] = launch["env"].get("SWE_BENCH_RECIPE")
        recipe.update({k: launch["env"].get(k) for k in RECIPE_KEYS})
        recipe["SWE_ROLLOUT_CONCURRENCY"] = launch["env"].get("SWE_ROLLOUT_CONCURRENCY")
    return {
        "eval": str(eval_dir.resolve()),
        "salvaged": not (eval_dir / INDEX).exists(),
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
            if h not in PACKAGE_HOSTS and h not in LOCAL_HOSTS and not h.endswith(RESERVED_SUFFIXES):
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
    old_instruction = (old / "instruction.md").read_text(errors="replace") if (old / "instruction.md").is_file() else ""
    new_instruction = (new / "instruction.md").read_text(errors="replace") if (new / "instruction.md").is_file() else ""
    old_docker = dock_old.read_text(errors="replace") if dock_old.is_file() else ""
    new_docker = dock_new.read_text(errors="replace") if dock_new.is_file() else ""
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
        "instruction_adds_install_guidance": instruction_adds_install_guidance(old_instruction, new_instruction),
        "dockerfile_removes_or_changes_install": dockerfile_removes_or_changes_install(old_docker, new_docker),
        "solution_changed": _texts(old / "solution") != _texts(new / "solution"),
        "claims_verifier_unchanged": claims_unchanged,
        "claim_mismatch": claims_unchanged and tests_changed,
    }


def _added_text(old: str, new: str) -> str:
    """The added side of a zero-context diff, without the ``+++`` header."""
    return "\n".join(
        line[1:] for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
        if line.startswith("+") and not line.startswith("+++")
    )


def instruction_adds_install_guidance(old: str, new: str) -> bool:
    """Whether an instruction diff adds package/tool acquisition guidance."""
    return bool(INSTALL_GUIDANCE_RE.search(_added_text(old, new)))


def _docker_install_specs(text: str) -> dict[str, set[str]]:
    """Installed package specs grouped by package name from Dockerfile lines."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for line in text.splitlines():
        if not DOCKER_INSTALL_RE.search(line):
            continue
        for name, version in PACKAGE_SPEC_RE.findall(line):
            out[name.lower()].add(version)
        # Unpinned package names still matter for removal.  Keep the token after
        # install, stopping at shell punctuation and common option flags.
        m = re.search(r"\b(?:install|add)\s+(.+)$", line, re.I)
        if m:
            for token in re.split(r"\s+|&&|[;|]", m.group(1)):
                token = token.strip("\\'\"(),")
                if token and not token.startswith("-"):
                    name = re.split(r"[<>=@:]", token, maxsplit=1)[0]
                    if re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                        out[name.lower()].add(token)
    return out


def dockerfile_removes_or_changes_install(old: str, new: str) -> bool:
    """Whether an accepted Dockerfile removes an install or changes its pin."""
    before, after = _docker_install_specs(old), _docker_install_specs(new)
    for name, specs in before.items():
        if name not in after or not specs & after[name]:
            return True
    return False


def _student_commands(record: Path) -> tuple[list[str], str]:
    """Read student actions and visible output from one rollout record."""
    try:
        _header, turns = rollout_record.read_record(record)
    except (OSError, ValueError, json.JSONDecodeError):
        return [], ""
    commands: list[str] = []
    output: list[str] = []
    for turn in turns:
        output.append(str(turn.get("output") or ""))
        keys = turn.get("keystrokes")
        if isinstance(keys, list):
            commands.extend(str(x) for x in keys)
        elif isinstance(turn.get("raw"), str):
            command = rollout_record.vanillux_command(turn["raw"])
            if command:
                commands.append(command)
    return commands, "\n".join(output)


def _attempt_install_fact(eval_dir: Path, task: str, attempt: dict) -> dict[str, Any]:
    record = _record_path(eval_dir, attempt["record"])
    commands, output = _student_commands(record)
    install_count = sum(len(INSTALL_COMMAND_RE.findall(cmd)) for cmd in commands)
    _header, turns = rollout_record.read_record(record)
    visible = "\n".join([output, *[str(t.get("raw") or "") for t in turns]])
    return {"task": task, "record": attempt["record"], "install_commands": install_count,
            "installed": install_count > 0, "disk_full": bool(DISK_FULL_RE.search(visible)),
            "passed": attempt.get("state") == "PASS"}


def _summarize_install_facts(facts: list[dict]) -> dict:
    """Summarize rollout-level install and disk-full facts."""
    n = len(facts)
    with_install = [x for x in facts if x["installed"]]
    without_install = [x for x in facts if not x["installed"]]
    counts = sorted(x["install_commands"] for x in facts)
    med = (counts[(len(counts) - 1) // 2] + counts[len(counts) // 2]) / 2 if counts else None
    rate = lambda xs: round(sum(x["passed"] for x in xs) / len(xs), 3) if xs else None  # noqa: E731
    return {
        "attempts": n,
        "install_attempts": len(with_install),
        "install_share": round(len(with_install) / n, 3) if n else None,
        "median_install_commands": med,
        "disk_full_attempts": sum(x["disk_full"] for x in facts),
        "disk_full_share": round(sum(x["disk_full"] for x in facts) / n, 3) if n else None,
        "pass_rate_with_install": rate(with_install),
        "pass_rate_without_install": rate(without_install),
    }


def _install_facts(eval_obj: dict, task_ids: set[str] | None = None) -> list[dict]:
    facts = []
    eval_dir = Path(eval_obj["eval"])
    for task, value in eval_obj["tasks"].items():
        if task_ids is not None and task not in task_ids:
            continue
        for attempt in value.get("attempts", []):
            if attempt.get("state") != "INFRA":
                facts.append(_attempt_install_fact(eval_dir, task, attempt))
    return facts


def install_metrics(after: list[dict], before: list[dict], rewrites: list[dict]) -> dict:
    """Report rollout install behaviour overall, by direction and guidance."""
    after_facts = [f for e in after for f in _install_facts(e)]
    before_facts = [f for e in before for f in _install_facts(e)]
    out = {"before": _summarize_install_facts(before_facts),
           "after": _summarize_install_facts(after_facts),
           "delta": _metric_delta(_summarize_install_facts(before_facts), _summarize_install_facts(after_facts))}
    groups: dict[str, set[str]] = {}
    for label, predicate in (
        ("instruction_guidance_added", lambda r: r.get("diff", {}).get("instruction_adds_install_guidance")),
        ("instruction_guidance_not_added", lambda r: not r.get("diff", {}).get("instruction_adds_install_guidance")),
    ):
        groups[label] = {r["task"] for r in rewrites if r.get("class") == "accepted" and predicate(r)}
    out["directions"] = {}
    for direction in ("harder", "easier"):
        ids = {r["task"] for r in rewrites if r.get("class") == "accepted" and r.get("job") == direction}
        b = _summarize_install_facts([f for e in before for f in _install_facts(e, ids)])
        a = _summarize_install_facts([f for e in after for f in _install_facts(e, ids)])
        out["directions"][direction] = {"before": b, "after": a, "delta": _metric_delta(b, a), "tasks": sorted(ids)}
    out["guidance"] = {}
    for label, ids in groups.items():
        b = _summarize_install_facts([f for e in before for f in _install_facts(e, ids)])
        a = _summarize_install_facts([f for e in after for f in _install_facts(e, ids)])
        out["guidance"][label] = {"before": b, "after": a, "delta": _metric_delta(b, a), "tasks": sorted(ids)}
    return out


def _metric_delta(before: dict | None, after: dict | None) -> dict:
    """After-minus-before for numeric/share fields, preserving empty groups."""
    before, after = before or {}, after or {}
    out = {}
    for key in ("install_share", "median_install_commands", "disk_full_share",
                "pass_rate_with_install", "pass_rate_without_install"):
        if before.get(key) is not None and after.get(key) is not None:
            out[key] = round(after[key] - before[key], 3)
        else:
            out[key] = None
    return out


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
    usd, unpriced = offline_cost.rewrite_usd(str(rw_dir))
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
        # turn records none and cost nothing); a Claude Code arm's do not, and
        # a model offline_cost has no list price for is not guessed at.
        "cost_known": (rw.get("arm") or "codex") == "codex" and not unpriced,
        "unpriced_models": sorted(unpriced),
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
            "package_install": {
                "instruction_guidance_added": {
                    "count": sum(x["instruction_adds_install_guidance"] for x in d),
                    "tasks": sorted(r["task"] for r in rs if r["diff"]["instruction_adds_install_guidance"]),
                },
                "dockerfile_install_removed_or_version_changed": {
                    "count": sum(x["dockerfile_removes_or_changes_install"] for x in d),
                    "tasks": sorted(r["task"] for r in rs if r["diff"]["dockerfile_removes_or_changes_install"]),
                },
            },
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
        # Every task that lost an attempt to infrastructure, and to what: the
        # drive's MAX_ATTEMPTS counts those attempts too.
        "infra_lost_attempts": {t: [r["class"] for r in rs if r["class"].startswith("infra:")]
                                for t, rs in sorted(per_task.items())
                                if any(r["class"].startswith("infra:") for r in rs)},
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


MECHANICAL_DEFECTS = ("no_reference_solution", "external_network", "grader_fetch")


def mechanical_fix(defect: str, accepted: dict | None) -> bool | None:
    """Whether the accepted revision fixes ``defect``, where a mechanical check
    can say: a missing reference solution is fixed when the accepted revision
    ships one (the oracle gate then held it to the task); a grader's
    third-party fetch is fixed when the accepted grader fetches none. None
    where only reading the rewrite can tell."""
    if defect not in MECHANICAL_DEFECTS:
        return None
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
    after_evals: list[dict] = []
    before_evals: list[dict] = []
    if evals:
        after_evals = [read_eval(e, evalset) for e in evals]
        after_tasks = merge_evals(after_evals)
        out["inputs"]["evals"] = [{k: e[k] for k in ("eval", "salvaged", "launch", "evalset")} for e in after_evals]
        out["recipe"] = after_evals[0]["recipe"]
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
        set_measured = None
        if set_dir is not None:
            manifest, set_sha = load_set(set_dir, verify=False)
            if rec and rec["set_manifest_sha256"] != set_sha:
                raise SystemExit(f"{set_dir} changed since {root} ran")
            tasks = sorted(json.load(open(set_dir / "selected.json")))
            out["inputs"]["set"] = {"dir": str(set_dir.resolve()), "manifest_sha256": set_sha, "tasks": manifest["tasks"],
                                    "rows": manifest["seed_mix"]["rows"], "rows_by_source": manifest.get("rows_by_source"),
                                    "dropped": dict(collections.Counter(manifest.get("dropped", {}).values())),
                                    "by_corpus": manifest.get("by_corpus"), "student_harness": manifest.get("student_harness"),
                                    "signals_from": manifest.get("signals_from"),
                                    "student_recipe": set_recipe(manifest)}
            # The recipe of the measurement this tally reports: the evals', or
            # with no eval the set's own before-measurements'.
            out["student_recipe"] = (out.get("recipe") or {}).get("student_recipe") if evals else set_recipe(manifest)
            out["legacy_recipe"] = out["student_recipe"] != DEFAULT_RECIPE
            set_measured = manifest.get("measured")
            if evals and set_measured and not before and set_recipe(manifest) != out["student_recipe"]:
                raise SystemExit(f"{set_dir} was measured under {set_recipe(manifest)}, the evals under "
                                 f"{out['student_recipe']}: give --before evals of the original rows under "
                                 f"{out['student_recipe']}")
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
        if after_evals:
            out["install_metrics"] = install_metrics(after_evals, before_evals, rewrites)
        if evals:
            outside = sorted(set(after_tasks) - set(tasks))
            if outside:
                raise SystemExit(f"the evals score tasks outside the root's tasks: {outside[:3]}")
    if not before and evals and root is not None and set_measured:
        # The set's own before-measurement of each task (its manifest's measured),
        # in the per-task shape of an eval.
        b_tasks = {t: {"bucket": m["bucket"], "pass": m["solved"], "graded": m["graded"], "infra": 0,
                       "finish": {}, "finish_failed": {}} for t, m in set_measured.items()}
        missing = sorted(set(after_tasks) - set(b_tasks))
        if missing:
            raise SystemExit(f"the set has no before-measurement of {len(missing)} tasks: {missing[:3]}")
        common = sorted(after_tasks)
        out["before"] = counts({k: b_tasks[k] for k in common})
        out["before_after"] = {**transitions(b_tasks, after_tasks, common),
                               "recipe_mismatch": {"before": "the set's measurements; their sources are in its manifest"}}
        for k in common:
            log(f"transition task={k} {b_tasks[k]['bucket']}->{after_tasks[k]['bucket']}")
    if before:
        if not evals:
            raise SystemExit("--before pairs with evals of the accepted rows")
        before_evals = [read_eval(e) for e in before]
        b_tasks = merge_evals(before_evals)
        out["inputs"]["before"] = [{k: e[k] for k in ("eval", "salvaged", "launch", "evalset")} for e in before_evals]
        missing = sorted(set(after_tasks) - set(b_tasks))
        if missing:
            raise SystemExit(f"the before evals do not score {len(missing)} tasks: {missing[:3]}")
        common = sorted(after_tasks)
        mm = recipe_mismatch(out["recipe"], before_evals[0]["recipe"])
        # A before and an after of different recipes do not pair; a code commit
        # or GPU difference is recorded with the transitions.
        hard = {k: v for k, v in mm.items() if k in RECIPE_KEYS or k in ("recipe", "student_recipe", "harness")}
        if hard:
            raise SystemExit(f"the before evals ran another recipe than the evals: {hard}")
        out["before"] = counts({k: b_tasks[k] for k in common})
        out["before_after"] = {**transitions(b_tasks, after_tasks, common), "recipe_mismatch": mm}
        for k in common:
            log(f"transition task={k} {b_tasks[k]['bucket']}->{after_tasks[k]['bucket']}")
    return out


# ------------------------------------------------------------------------ compare


def _pct(n, d) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def _share(x) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def overshoot_pairs(a: dict, b: dict, tasks: list[str]) -> dict:
    """Exact McNemar on overshoot over tasks both arms accepted and graded:
    a harder rewrite that came out none solved, or an easier one that came out
    all solved."""
    def over(res: dict) -> dict[str, bool]:
        out = {}
        for x in res["rewrites"]:
            if x["class"] == "accepted" and x.get("after_band") in GRADED_BUCKETS:
                out[x["task"]] = x["after_band"] == ("none_solved" if x["job"] == "harder" else "all_solved")
        return out
    oa, ob = over(a), over(b)
    both = [t for t in tasks if t in oa and t in ob]
    gained = sum(not oa[t] and ob[t] for t in both)
    lost = sum(oa[t] and not ob[t] for t in both)
    return {"tasks": len(both), "overshoot": [sum(oa[t] for t in both), sum(ob[t] for t in both)],
            "gained": gained, "lost": lost, "mcnemar_p": mcnemar_exact(gained, lost)}


def compare(results: list[tuple[str, dict]], allow_mismatch: bool) -> tuple[dict, str]:
    """Paired arm-vs-arm against the first, and one markdown table of all."""
    ref_label, ref = results[0]
    set_sha = lambda r: (r["inputs"].get("set") or {}).get("manifest_sha256")  # noqa: E731
    pairs, problems = [], []
    for label, r in results[1:]:
        mm = recipe_mismatch(ref.get("recipe"), r.get("recipe"))
        if set_sha(ref) != set_sha(r):
            mm["set_manifest_sha256"] = [set_sha(ref), set_sha(r)]
        if ref.get("student_recipe") != r.get("student_recipe"):
            mm["student_recipe"] = [ref.get("student_recipe"), r.get("student_recipe")]
        if mm:
            problems.append(f"{ref_label} vs {label}: {mm}")
        pair: dict[str, Any] = {"arms": [ref_label, label], "mismatch": mm}
        if "after_tasks" in ref and "after_tasks" in r:
            common = sorted(set(ref["after_tasks"]) & set(r["after_tasks"]))
            pair["common_accepted"] = transitions(ref["after_tasks"], r["after_tasks"], common)
            if "rewrites" in ref and "rewrites" in r:
                pair["overshoot"] = overshoot_pairs(ref, r, common)
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
            pair["whole_set"] = {"tasks": len(all_tasks), "mixed": [len(ya), len(yb)], "gained": gained, "lost": lost,
                                 "mcnemar_p": mcnemar_exact(gained, lost)}
        pairs.append(pair)
    if problems and not allow_mismatch:
        raise SystemExit("not comparable:\n  " + "\n  ".join(problems))

    lines = []
    if problems:
        lines += ["**NOT COMPARABLE** (compared anyway with --allow-mismatch):", ""] + [f"- {p}" for p in problems] + [""]
    lines += [
        "| arm | source / harness / recipe | accepted / handled | give-ups | gate / filter | infra-lost | harder -> mixed / overshoot / stays | easier -> mixed / overshoot / stays | mixed | all-solved | none-solved | no verdict | submit / context / time | $ counted | $ / accepted | $ / mixed | before -> after mixed (p) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for label, r in results:
        rec = r.get("recipe") or {}
        cells = [label, f"{rec.get('source', '?')} / {rec.get('harness', '?')} / {r.get('student_recipe') or rec.get('student_recipe') or '?'}"]
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
        if "overshoot" in p:
            o = p["overshoot"]
            lines.append(f"Overshoot on the {o['tasks']} tasks both accepted and graded: {o['overshoot'][0]} vs {o['overshoot'][1]}, "
                         f"gained {o['gained']}, lost {o['lost']}, exact McNemar p={o['mcnemar_p']:.3g}.")
        if "whole_set" in p:
            y = p["whole_set"]
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
AUDIT_VERIFY_STATES = {"confirmed", "partly", "refuted"}
AUDIT_MAX_ATTEMPTS = 3
AUDIT_TIMEOUT_SEC = 900
AUDIT_TOOLS = ("Read", "Grep", "Glob")


def _diff(task_dir: Path, a: str, b: str) -> str:
    """diff -ruN of two paths relative to the task directory, so the headers
    name r<N>/ and never the root, whose name would unblind the arm; the
    headers' mtimes, which date the arm's run, are dropped."""
    out = subprocess.run(["diff", "-ruN", a, b], capture_output=True, text=True, errors="replace", cwd=task_dir).stdout
    return re.sub(r"^((?:---|\+\+\+) \S+)\t.*$", r"\1", out, flags=re.M)


def _scrub(text: str, roots: list[Path]) -> str:
    """Every arm root's path and name replaced, so a quoted cwd unblinds nothing."""
    for r in roots:
        for needle in (str(r.resolve()), str(r), r.resolve().name):
            text = text.replace(needle, "<root>")
    return text


def audit_bundle(*, arms: list[tuple[str, Path]], out: Path, key: Path, sample: int | None, seed: int,
                 log: Log, union: bool = False) -> None:
    """Write blinded bundles, using the union only when ``union`` is requested.

    The historical default is the intersection of accepted tasks, so existing
    bundles remain reproducible. ``--union`` keeps every task accepted by at
    least one arm and puts only the variants that exist for that arm in its
    task directory. The seed package is copied once, and each variant is a
    random V<k> in a per-task order. The key stays outside ``out`` so the
    auditor cannot recover arm labels.
    """
    if key.resolve() == out.resolve() or out.resolve() in key.resolve().parents:
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
    task_sets = [set(a) for a in accepted.values()]
    tasks = sorted(set.union(*task_sets) if union else set.intersection(*task_sets))
    if sample is not None and sample < len(tasks):
        tasks = sorted(random.Random(seed).sample(tasks, sample))
    out.mkdir(parents=True)
    shutil.copy2(_EVO / "agents" / "bench_audit.md", out / "INSTRUCTIONS.md")
    shutil.copy2(_EVO / "agents" / "bench_audit_verify.md", out / "VERIFY.md")
    keymap = {}
    for t in tasks:
        order = [label for label, _ in arms if t in accepted[label]]
        random.Random(f"{seed}:{t}").shuffle(order)
        keymap[t] = {f"V{i + 1}": a for i, a in enumerate(order)}
        td = out / t
        first_root = dict(arms)[order[0]]
        shutil.copytree(first_root / "evolution" / "tasks" / t / "r0", td / "original", symlinks=True)
        for v, label in keymap[t].items():
            rw, rw_dir = accepted[label][t]
            tdir = dict(arms)[label] / "evolution" / "tasks" / t
            r0, rn = f"r{rw['input_rev']}", f"r{rw['result_rev']}"
            vd = td / v
            vd.mkdir(parents=True)
            shutil.copytree(tdir / rn, vd / "package", symlinks=True)
            for part in ("instruction.md", "tests", "solution", "environment"):
                (vd / f"diff_{part.replace('.md', '')}.txt").write_text(_diff(tdir, f"{r0}/{part}", f"{rn}/{part}"))
            roots = [root for _, root in arms]
            (vd / "agent_summary.md").write_text(_scrub(_last_agent_message(rw_dir) or "(no agent summary found)", roots))
            (vd / "direction.txt").write_text(f"{rw['job']}\n")
        log(f"task={t} variants={len(keymap[t])}")
    key.parent.mkdir(parents=True, exist_ok=True)
    layout.write_json_atomic(key, {"arms": {label: str(root.resolve()) for label, root in arms}, "seed": seed,
                                   "sample": sample, "tasks": keymap})
    width = "up to " if union else ""
    print(f"{len(tasks)} tasks x {width}{len(arms)} arms -> {out}; key -> {key}")


def _audit_env_file(path: Path) -> dict[str, str]:
    """Read only the AWS credentials an audit call is allowed to receive."""
    allowed = {"AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read --env-file {path}: {exc}") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SystemExit(f"invalid --env-file line (expected KEY=VALUE): {path}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    if not values.get("AWS_BEARER_TOKEN_BEDROCK") and not (
        values.get("AWS_ACCESS_KEY_ID") and values.get("AWS_SECRET_ACCESS_KEY")
    ):
        raise SystemExit(
            f"--env-file {path} must contain AWS_BEARER_TOKEN_BEDROCK or "
            "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
        )
    return values


def _audit_path() -> str:
    """A minimal PATH that still finds the caller-selected Claude binary."""
    claude = shutil.which("claude")
    dirs = [str(Path(claude).parent)] if claude else []
    dirs.extend(("/usr/local/bin", "/usr/bin", "/bin"))
    return os.pathsep.join(dict.fromkeys(dirs))


def _audit_env(credentials: dict[str, str], region: str, home: Path) -> dict[str, str]:
    """Build a clean Bedrock environment; no caller environment is inherited."""
    config = home / "claude-config"
    config.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": _audit_path(),
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": region,
    }
    env.update(credentials)
    return env


def _audit_argv(model: str, system_prompt_file: Path) -> list[str]:
    """The complete clean, read-only Claude Code command for one call."""
    return [
        "claude", "-p", "--bare", "--restricted", "--setting-sources", "", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
        "--no-session-persistence", "--system-prompt-file", str(system_prompt_file),
        "--model", model, "--tools", *AUDIT_TOOLS, "--output-format", "json",
        "--permission-prompts", "none",
    ]


def _audit_cli_version(env: dict[str, str]) -> str:
    proc = subprocess.run(["claude", "--version"], env=env, capture_output=True, text=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"claude --version exited {proc.returncode}")
    version = (proc.stdout or proc.stderr).strip()
    if not version:
        raise RuntimeError("claude --version returned no version")
    return version


def _audit_json_reply(stdout: str) -> tuple[dict, dict]:
    """Decode Claude's JSON envelope and its JSON result string."""
    envelope = json.loads(stdout)
    payload = envelope.get("structured_output", envelope.get("result", envelope))
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("Claude result is not a JSON object")
    return payload, envelope


def _valid_verdict(value: object) -> bool:
    if not isinstance(value, dict) or value.get("label") not in AUDIT_LABELS:
        return False
    findings = value.get("findings")
    if not isinstance(findings, list):
        return False
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or not finding.get("id")
            or finding.get("class") not in (*AUDIT_LABELS, "SEED")
        ):
            return False
    return True


def _valid_verify(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(k): v for k, v in value.items()
        if k != "metadata" and isinstance(v, str) and v in AUDIT_VERIFY_STATES
    }


def _audit_metadata(*, model: str, cli_version: str, rubric_sha256: str, argv: list[str], envelope: dict) -> dict:
    return {
        "model": model,
        "cli_version": cli_version,
        "rubric_sha256": rubric_sha256,
        "argv": list(argv),
        "usage": envelope.get("usage"),
        "cost_usd": envelope.get("total_cost_usd"),
        "total_cost_usd": envelope.get("total_cost_usd"),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _assert_clean_audit_path(audit: Path) -> None:
    """Reject any CLAUDE.md that could be discovered from a task cwd."""
    audit = audit.resolve()
    if not audit.is_dir():
        raise SystemExit(f"audit bundle does not exist: {audit}")
    found = list(audit.rglob("CLAUDE.md"))
    for parent in (audit, *audit.parents):
        candidate = parent / "CLAUDE.md"
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    if found:
        raise SystemExit(f"refusing audit bundle with CLAUDE.md: {found[0]}")


def _audit_prompt(stage: str, variant: str, finding: dict | None = None) -> str:
    if stage == "audit":
        return (
            f"Read original/ and {variant}/ in this task bundle and judge only {variant}. "
            "Return exactly one JSON object with shape "
            '{"label":"<one rubric label>","findings":[{"id":"f1","class":"<class>",'
            '"file":"<path>","line":1,"evidence":"<evidence>","would_fail":"<solution>"}],'
            '"notes":"<short notes>"}. Do not use markdown or add commentary.'
        )
    assert finding is not None
    return (
        f"Read original/ and {variant}/ in this task bundle. Re-evaluate finding {finding['id']} "
        "under the verifier rubric and try to refute it. Return exactly one JSON object of the form "
        f'{{"{finding["id"]}":"confirmed|partly|refuted"}}. Do not use markdown or add commentary. '
        f"The finding is: {json.dumps(finding, ensure_ascii=False, sort_keys=True)}"
    )


def _audit_call(*, task_dir: Path, prompt: str, argv: list[str], credentials: dict[str, str],
                region: str) -> tuple[dict, dict]:
    with tempfile.TemporaryDirectory(prefix="evolve-bench-audit-") as raw_home:
        env = _audit_env(credentials, region, Path(raw_home))
        proc = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, cwd=task_dir, env=env,
            timeout=AUDIT_TIMEOUT_SEC, check=False,
        )
    if proc.returncode:
        raise RuntimeError(f"claude exited {proc.returncode}")
    return _audit_json_reply(proc.stdout)


def _audit_run_item(item: tuple, *, stage: str, argv: list[str], credentials: dict[str, str], region: str,
                    rubric_sha256: str, cli_version: str, log, log_lock: threading.Lock,
                    verify_locks: dict[Path, threading.Lock]) -> bool:
    task_dir, variant_dir, output, finding = item
    variant = variant_dir.name
    prompt = _audit_prompt(stage, variant, finding)
    for attempt in range(1, AUDIT_MAX_ATTEMPTS + 1):
        try:
            payload, envelope = _audit_call(task_dir=task_dir, prompt=prompt, argv=argv,
                                            credentials=credentials, region=region)
            metadata = _audit_metadata(model=argv[argv.index("--model") + 1], cli_version=cli_version,
                                       rubric_sha256=rubric_sha256, argv=argv, envelope=envelope)
            if stage == "audit":
                if not _valid_verdict(payload):
                    raise ValueError("auditor result does not match verdict schema")
                payload = {**payload, "metadata": metadata}
                layout.write_json_atomic(output, payload)
            else:
                finding_id = finding["id"]
                state = payload.get(finding_id)
                if state not in AUDIT_VERIFY_STATES:
                    raise ValueError(f"verifier result for {finding_id} is invalid")
                lock = verify_locks[output]
                with lock:
                    current = _valid_verify(json.loads(output.read_text()) if output.exists() else {})
                    current[finding_id] = state
                    layout.write_json_atomic(output, {**current, "metadata": metadata})
            with log_lock:
                log(f"stage={stage} task={task_dir.name} variant={variant} finding={finding.get('id') if finding else '-'} done attempt={attempt}")
            return True
        except Exception as exc:
            with log_lock:
                log(f"stage={stage} task={task_dir.name} variant={variant} finding={finding.get('id') if finding else '-'} failed attempt={attempt} error={type(exc).__name__}")
            if attempt == AUDIT_MAX_ATTEMPTS:
                return False
    return False


def audit_run(*, audit: Path, stage: str, model: str, concurrency: int, env_file: Path,
              region: str, log: Log) -> dict[str, int]:
    """Run one isolated, resumable audit or verification stage over a bundle."""
    if stage not in {"audit", "verify"}:
        raise SystemExit(f"unknown audit stage {stage}")
    if concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    _assert_clean_audit_path(audit)
    audit = audit.resolve()
    rubric = audit / ("INSTRUCTIONS.md" if stage == "audit" else "VERIFY.md")
    if not rubric.is_file():
        raise SystemExit(f"missing {rubric}")
    credentials = _audit_env_file(env_file)
    with tempfile.TemporaryDirectory(prefix="evolve-bench-version-") as raw_home:
        cli_env = _audit_env(credentials, region, Path(raw_home))
        cli_version = _audit_cli_version(cli_env)
    rubric_sha256 = layout.sha256_file(rubric)
    manifest_path = audit / "audit_run.json"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest = {
        "model": model, "rubric_sha256": rubric_sha256, "cli_version": cli_version,
        "bench_commit": _bench_commit(), "stage": stage, "concurrency": concurrency,
        "env_file": str(env_file.resolve()), "timestamp": timestamp,
    }
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        for key in ("model", "cli_version", "bench_commit"):
            if old.get(key) not in (None, manifest[key]):
                raise SystemExit(f"audit_run.json {key} differs: {old.get(key)} vs {manifest[key]}")
        history = old.get("stages", {})
        history[stage] = {"rubric_sha256": rubric_sha256, "timestamp": timestamp}
        manifest["stages"] = history
    layout.write_json_atomic(manifest_path, manifest)

    items = []
    for task_dir in sorted(p for p in audit.iterdir() if p.is_dir()):
        variants = sorted(p for p in task_dir.iterdir() if p.is_dir() and re.fullmatch(r"V\d+", p.name))
        if not variants:
            continue
        if stage == "audit":
            for variant_dir in variants:
                output = variant_dir / "verdict.json"
                try:
                    if _valid_verdict(json.loads(output.read_text())):
                        continue
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
                items.append((task_dir, variant_dir, output, None))
        else:
            for variant_dir in variants:
                verdict_path = variant_dir / "verdict.json"
                try:
                    verdict = json.loads(verdict_path.read_text())
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if not _valid_verdict(verdict):
                    continue
                output = variant_dir / "verify.json"
                existing = {}
                try:
                    existing = _valid_verify(json.loads(output.read_text()))
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
                for finding in verdict["findings"]:
                    if finding.get("class") in DEFECT_LABELS and finding.get("id") not in existing:
                        items.append((task_dir, variant_dir, output, finding))

    log_lock = threading.Lock()
    verify_locks = collections.defaultdict(threading.Lock)
    system_prompt_file = audit / ("INSTRUCTIONS.md" if stage == "audit" else "VERIFY.md")
    argv = _audit_argv(model, system_prompt_file)
    log(f"stage={stage} start items={len(items)} model={model} cli_version={cli_version}")
    done = failed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_audit_run_item, item, stage=stage, argv=argv, credentials=credentials,
                               region=region, rubric_sha256=rubric_sha256, cli_version=cli_version,
                               log=log, log_lock=log_lock, verify_locks=verify_locks) for item in items]
        for future in futures:
            if future.result():
                done += 1
            else:
                failed += 1
    log(f"stage={stage} finished done={done} failed={failed}")
    print(f"audit-run {stage}: {done} done, {failed} failed; manifest -> {manifest_path}")
    return {"done": done, "failed": failed}


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
    p.add_argument("--run", type=Path, help="a directory in the run shape that already holds the signals")
    p.add_argument("--selected", type=Path, help="with --run: task -> {group}; default every signal")
    p.add_argument("--eval", type=Path, nargs="*", default=[], help="evals measuring the rows (each task's attempts)")
    p.add_argument("--recipe", choices=sorted(STUDENT_RECIPES), default=DEFAULT_RECIPE,
                   help="the student recipe the measurements were taken under")
    p.add_argument("--first-group", type=Path, nargs="*", default=[],
                   help="training runs measuring the rows (each task's first rev-0 group, first k graded attempts)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--release", type=Path, help="a data_release.py release: mix, sources and config")
    g.add_argument("--mix", type=Path, help="the seed mix the signals measured (with --sources)")
    p.add_argument("--sources", type=Path, nargs="+", default=[], help="with --mix: corpus directories holding tasks/<id>")
    p = sub.add_parser("run", help="create an arm's root, drive it, and watch waves by default")
    p.add_argument("--bench", type=Path, required=True)
    p.add_argument("--set", required=True)
    p.add_argument("--arm", type=Path, required=True)
    p.add_argument("--workers", type=int, help="relaunch an existing root at this worker count")
    p.add_argument("--before", type=Path, nargs="+", default=[], help="original-row eval(s) for the final tally")
    p.add_argument("--every", type=int, default=40, help="cut a wave after this many accepted revisions")
    p.add_argument("--minutes", type=int, default=60, help="cut a wave after this many minutes")
    p.add_argument("--eval-host", default="della-tridao", help="gpu-pool target for student evals")
    p.add_argument("--eval-gpus", default="auto", help="comma-separated cards, or auto for pool pick")
    p.add_argument("--no-auto-eval", action="store_true", help="start only the drive; keep manual wave/eval/tally")
    p = sub.add_parser("pipeline", help="rewrite while the base eval runs: stager + drive as user units")
    p.add_argument("--bench", type=Path, required=True)
    p.add_argument("--name", required=True, help="the set's name")
    p.add_argument("--eval", type=Path, required=True, help="the running base-student eval of the release")
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--arm", type=Path, required=True)
    p.add_argument("--poll", type=int, default=120, help="seconds between the stager's scans")
    p = sub.add_parser("stage", help="the pipeline's stager (pipeline starts it)")
    p.add_argument("--bench", type=Path, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--eval", type=Path, required=True)
    p.add_argument("--release", type=Path, required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--poll", type=int, required=True)
    p = sub.add_parser("wave", help="cut the next eval wave of accepted revisions, if one is due")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--every", type=int, required=True, help="cut once this many accepted revisions wait")
    p.add_argument("--minutes", type=int, required=True, help="or this long after the previous wave")
    p = sub.add_parser("watch", help="cut, score, recover and tally waves until an arm is complete")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--every", type=int, required=True)
    p.add_argument("--minutes", type=int, required=True)
    p.add_argument("--eval-host", default="della-tridao")
    p.add_argument("--eval-gpus", default="auto")
    p.add_argument("--poll", type=int, default=WATCH_POLL_SEC)
    p = sub.add_parser("evalset", help="an arm's accepted revisions as an evalset")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("eval", help="score an evalset under the pinned student recipe")
    p.add_argument("--evalset", type=Path, required=True)
    p.add_argument("--harness", choices=HARNESSES, required=True, help="the student harness (TMAX_AGENT)")
    p.add_argument("--ckpt", default="base", help="checkpoint directory, or base")
    p.add_argument("--gpus", required=True, help="RL_GPUS: trainer card, then generator card")
    p.add_argument("--base", type=Path, help="where evals/ goes; required for a release's mix")
    p.add_argument("--shard", help="i/n: score only rows i, i+n, ... (n evals on n cards; tally merges them)")
    p.add_argument("--concurrency", type=int, default=ROLLOUT_CONCURRENCY_PER_GENERATOR,
                   help="rollouts in flight on the generator, at most 128: lower on a card whose KV cache "
                        "runs fewer sequences at once, so the rest do not wait while their clock runs")
    p = sub.add_parser("rest", help="the rows of an evalset that finished evals did not score, as an evalset")
    p.add_argument("--evalset", type=Path, required=True)
    p.add_argument("--done", type=Path, nargs="+", required=True)
    p = sub.add_parser("salvage", help="a stopped eval's finished tasks as its index, the rest as an evalset")
    p.add_argument("--eval", type=Path, required=True)
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
    p.add_argument("--union", action="store_true", help="include tasks accepted by any arm, with only existing variants")
    p = sub.add_parser("audit-run", help="run the isolated auditor or verifier over a bundle")
    p.add_argument("--audit", type=Path, required=True)
    p.add_argument("--stage", choices=("audit", "verify"), required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--concurrency", type=int, required=True)
    p.add_argument("--env-file", type=Path, required=True, help="Bedrock credentials; values are never logged")
    p.add_argument("--aws-region", "--region", dest="aws_region", default="us-east-1")
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
        if bool(a.run) == bool(a.eval or a.first_group):
            ap.error("give --run, or measurements (--eval / --first-group)")
        freeze(bench=a.bench, name=a.name, run=a.run, evals=a.eval, first_group=a.first_group, selected=a.selected,
               recipe=a.recipe,
               release=a.release, mix=a.mix, sources=a.sources, log=_log_for(a.bench, "freeze"))
    elif a.cmd == "run":
        run_arm(bench=a.bench, set_name=a.set, arm_path=a.arm, log=_log_for(a.bench, "run"), workers=a.workers,
                before=a.before, every=a.every, minutes=a.minutes, eval_host=a.eval_host,
                eval_gpus=a.eval_gpus, no_auto_eval=a.no_auto_eval)
    elif a.cmd == "pipeline":
        pipeline(bench=a.bench, name=a.name, eval_dir=a.eval, release=a.release, arm_path=a.arm,
                 poll_sec=a.poll, log=_log_for(a.bench, "pipeline"))
    elif a.cmd == "stage":
        stage(bench=a.bench, name=a.name, eval_dir=a.eval, release=a.release, root=a.root,
              poll_sec=a.poll, log=_log_for(a.bench, f"stage-{a.name}"))
    elif a.cmd == "wave":
        cut_wave(root=a.root, every=a.every, minutes=a.minutes, log=_log_for(a.root, "wave"))
    elif a.cmd == "watch":
        watch(root=a.root, every=a.every, minutes=a.minutes, eval_host=a.eval_host,
              eval_gpus=a.eval_gpus, poll_sec=a.poll)
    elif a.cmd == "evalset":
        build_evalset(a.root, _log_for(a.root, "evalset"))
    elif a.cmd == "eval":
        run_eval(a.evalset, a.ckpt, a.gpus, a.harness, a.base, a.shard, a.concurrency)
    elif a.cmd == "rest":
        rest_evalset(a.evalset, a.done, Log(a.evalset.with_name(a.evalset.stem + ".rest.log")))
    elif a.cmd == "salvage":
        salvage(a.eval, Log(a.eval.parent / f"{a.eval.name}.salvage.log"))
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
        audit_bundle(arms=arms, out=a.out, key=a.key, sample=a.sample, seed=a.seed, union=a.union,
                     log=_log_for(a.out.parent, "audit-bundle"))
    elif a.cmd == "audit-run":
        audit_run(audit=a.audit, stage=a.stage, model=a.model, concurrency=a.concurrency,
                  env_file=a.env_file, region=a.aws_region,
                  log=_log_for(a.audit, f"audit-run-{a.stage}"))
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
