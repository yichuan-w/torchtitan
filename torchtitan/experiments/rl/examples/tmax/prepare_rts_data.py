# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a training JSONL from the ``Zhongzhi1228/Recursive-Task-Synthesis`` corpus.

RTS (arXiv:2608.05466, "Recursive Synthesis for Long-Horizon Terminal Tasks") is a
37,484-task synthetic terminal-agent corpus laid out as a Harbor task tree, the
same shape as Terminal-Bench 2.0::

    <task>/instruction.md          # the agent instruction
    <task>/task.toml               # [verifier]/[agent]/[environment] timeouts
    <task>/environment/Dockerfile  # the task env -- NOT a published image
    <task>/tests/test.sh           # verifier: writes /logs/verifier/reward.txt (0/1)
    <task>/tests/test_state.py     # + grade-time helpers
    <task>/solution/solve.sh       # oracle solution (unused for training)

The verifier contract is identical to tmax, so ``grading.py`` grades these rows
unchanged. The one difference is the environment: RTS publishes no docker image
(only 198 of 37,484 task.toml carry ``docker_image``), so each row carries its
``dockerfile`` text instead and the sandbox backend builds it server-side --
Daytona caches the build, so only the first sandbox per distinct Dockerfile pays
for it (measured: ~20-40s cold, ~1-2s warm).

A task whose Dockerfile copies local files with COPY also carries them as ``build_context``
({relpath: base64}); the sandbox writes them back beside the Dockerfile so the SDK
uploads them. Only tasks that need a host we do not control (an init system, the
docker socket, ``--privileged``) are dropped.

Difficulty: **use ``oracle_commands``, not the ``difficulty`` field.** The field is
inherited from the synthesis seed and still reads "easy" for round-15 tasks. What
bounds RL is how many commands the reference solution runs -- a rollout capped at
T turns cannot solve a task whose oracle needs more than T of them (median 44 in
shard 0, 212 in shard 7). ``--max-oracle-commands`` filters on it.

Run against an extracted corpus (``tar xf tasks-0000N.tar``)::

    python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
        --tasks-root /path/to/s0/tasks --max-oracle-commands 64 \
        --out mast_rl/swe_assets/rts_train.jsonl
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import re
import shlex
import sys

from torchtitan.experiments.rl.examples.tmax.prepare_tmax_data import (
    _DEFAULT_IMAGE_PREFIX,
    _pretest_tmax_fields,
    _REWARD_PATH,
    selfcheck_env_identities,
)

# TerminalWorld/harbor ships a canary comment in instruction.md so a model trained
# on the corpus can be detected. The dataset card asks consumers to keep it in the
# build and grading files (which no model sees) and strip it from what the policy
# actually reads, so it is removed from the instruction here and nowhere else.
_CANARY_RE = re.compile(r"^.*harbor-canary.*$\n?", re.MULTILINE)


def _strip_canary(text: str) -> str:
    """Drop the harbor canary comment lines from an agent-visible instruction."""
    return _CANARY_RE.sub("", text)


# A COPY/ADD whose source is local needs the task's ``environment/`` shipped with
# the row; ``--from=`` pulls from another image or stage and needs nothing. Matched
# against the backslash-JOINED Dockerfile (see ``_join_continuations``), so a
# multi-line COPY yields all of its sources rather than just the first line.
_LOCAL_COPY = re.compile(r"^\s*(?:COPY|ADD)\s+(?!--from=)(.+?)\s*$", re.M | re.I)
# Flags that only affect ownership/permissions of the copied files.
_COPY_FLAG = re.compile(r"^--(chown|chmod|link)=")
# BuildKit here-document (``COPY <<'EOF' /app/f``): the content is inline in the
# Dockerfile, so there is no local source to ship.
_COPY_HEREDOC = re.compile(r"^<<-?")

# Tasks that need a host we do not control. Measured, not guessed: a substring
# match on "systemd"/"docker-compose" rejects ~1700 tasks of which 0 actually run
# an init system -- the corpus mentions them in comments explaining solve.sh. Only
# these signals correlate with a task that really cannot run in a plain container.
# Matched against the COMMENT-STRIPPED Dockerfile for the same reason: on the
# TerminalWorld corpus 87 of 89 ``--privileged`` / ``docker.sock`` hits were inside
# comments describing how the task was authored, and those tasks build and grade
# fine in a plain container (a Dockerfile cannot grant privilege anyway).
_REJECT_PRIVILEGED = re.compile(
    r"^\s*(?:ENTRYPOINT|CMD)\s+.*(?:/sbin/init|systemd)"
    r"|^\s*RUN\s+.*systemctl\s+(?:enable|start)"
    r"|/var/run/docker\.sock"
    r"|--privileged",
    re.M | re.I,
)
_COMMENT_LINE = re.compile(r"^\s*#.*$", re.M)

# Byte ceiling for an inlined build context. The corpus is tiny here (p50 ~5 KB,
# p90 ~15 KB) but a handful of tasks carry multi-MB fixtures that would bloat the
# JSONL for no benefit.
_MAX_CONTEXT_BYTES = 1 << 20

_DEFAULT_WORKDIR = "/app"

# A latency optimization, NOT a requirement. Terminus-2 installs tmux itself at
# session bring-up (harbor ``TmuxSession.start`` -> ``_attempt_tmux_installation``:
# package manager first, then a from-source build), so an image without tmux is not
# unsolvable. Measured against un-injected TerminalWorld-Seeds images, harbor's
# runtime install succeeded on 6/6 bases -- ubuntu:16.04 5.0s, centos:7 (yum) 10.9s,
# ubuntu:22.04 9.1s. Baking the step in moves those seconds off every rollout and
# onto the once-per-Dockerfile Daytona build, at the cost of coupling the JSONL to
# one harness; hence opt-in, off by default.
#
# The archive-mirror rewrites keep the EOL bases (centos:7, ubuntu:16.04, debian
# buster/stretch) buildable if their default mirrors go away.
# Non-fatal by design: the whole install runs in a subshell whose failure is caught by
# `|| echo ...`, so the RUN always exits 0 and a tmux preinstall failure never fails the
# image build. Rationale: some source bases have a broken package path we don't control
# (e.g. an EOL mirror, or a distro whose pkg manager we don't branch on), and a hard
# `exit 1` there dropped the ENTIRE image to BUILD_FAILED -- e.g. tw_473991 (archlinux)
# fell through to the old `else: exit 1` because pacman had no branch, and its 192
# rollouts all BUILD_FAILED and burned Daytona create quota. When tmux is not baked in,
# Terminus self-installs it at runtime, so a miss is degraded-but-recoverable, not fatal.
# @andy: once the environment/base images are fixed so every task builds tmux, flip this
# back to strict (drop the `|| echo` catch and restore `exit 1` in the else) to surface
# real regressions instead of silently shipping images without tmux.
_AGENT_RUNTIME_BLOCK = """
# harbor-agent-runtime: tmux is required by the terminal agent (non-fatal preinstall)
RUN ( if command -v tmux >/dev/null 2>&1; then \\
        exit 0; \\
      elif command -v apt-get >/dev/null 2>&1; then \\
        (apt-get update || ( \\
          sed -i -e 's|http://deb.debian.org/debian|http://archive.debian.org/debian|g' \\
                 -e 's|http://security.debian.org/debian-security|http://archive.debian.org/debian-security|g' \\
                 -e 's|http://deb.debian.org/debian-security|http://archive.debian.org/debian-security|g' \\
                 -e 's|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \\
                 -e 's|http://security.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \\
                 -e 's|http://.*archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \\
                 /etc/apt/sources.list 2>/dev/null; \\
          echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99harbor-archive; \\
          apt-get update \\
        )) && (apt-get install -y tmux || apt-get install -y --allow-unauthenticated tmux) \\
        && rm -rf /var/lib/apt/lists/*; \\
      elif command -v yum >/dev/null 2>&1; then \\
        (yum install -y tmux || ( \\
          sed -i -e 's|^mirrorlist=|#mirrorlist=|g' \\
                 -e 's|^#baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' \\
                 /etc/yum.repos.d/CentOS-*.repo 2>/dev/null; \\
          yum install -y tmux \\
        )) && yum clean all; \\
      elif command -v dnf >/dev/null 2>&1; then \\
        dnf install -y tmux && dnf clean all; \\
      elif command -v microdnf >/dev/null 2>&1; then \\
        microdnf install -y tmux && microdnf clean all; \\
      elif command -v apk >/dev/null 2>&1; then \\
        apk add --no-cache tmux; \\
      elif command -v pacman >/dev/null 2>&1; then \\
        pacman -Sy --noconfirm tmux; \\
      elif command -v zypper >/dev/null 2>&1; then \\
        zypper install -y tmux; \\
      else \\
        echo 'ERROR: no supported package manager to install tmux' >&2; exit 1; \\
      fi ) \\
    || echo 'harbor-agent-runtime: tmux preinstall failed (non-fatal); Terminus will self-install at runtime' >&2
"""


def _join_continuations(dockerfile: str) -> str:
    """Fold backslash-continued Dockerfile lines into one physical line each.

    ``COPY a.yml \\<newline> b.yml dest`` is one instruction; matching it line by
    line sees only ``a.yml \\``, whose trailing backslash then makes ``shlex.split``
    raise (reported as an unbuildable task) and hides every source but the first.
    """
    return re.sub(r"\\\n\s*", " ", dockerfile)


def _strip_comments(dockerfile: str) -> str:
    """Drop whole-line ``#`` comments so instruction scans ignore prose."""
    return _COMMENT_LINE.sub("", dockerfile)


def _workdir_from_dockerfile(text: str) -> str:
    """Last ``WORKDIR`` in the Dockerfile -- where the agent's commands must run."""
    workdir = _DEFAULT_WORKDIR
    for line in text.splitlines():
        m = re.match(r"\s*WORKDIR\s+(\S+)", line)
        if m:
            workdir = m.group(1)
    return workdir


def _argv_from_instruction(rest: str) -> list[str]:
    """Parse the argument of an ENTRYPOINT/CMD into argv.

    Both take an exec form (``["/entrypoint.sh", "-x"]``, a JSON array) and a shell
    form (``/entrypoint.sh -x``, which docker wraps in ``/bin/sh -c``).
    """
    rest = rest.strip()
    if rest.startswith("["):
        try:
            argv = json.loads(rest)
        except json.JSONDecodeError:
            return []
        return [str(a) for a in argv] if isinstance(argv, list) else []
    return ["/bin/sh", "-c", rest]


def _entrypoint_command(dockerfile: str) -> str | None:
    """The image's ENTRYPOINT as a shell command, or None when it has none.

    Docker runs ENTRYPOINT as PID 1 with CMD appended as its arguments; our sandbox
    backends exec commands directly and never run it. Tasks that rely on it (serving
    a bundled file over localhost, seeding /etc/hosts, starting a daemon the
    instruction assumes) are then unsolvable no matter what the agent does, and
    their own reference solution scores 0 -- which is the whole gap between this
    corpus's published oracle pass rate and ours.

    CMD supplies ``"$@"`` for the near-universal ``exec "$@"`` tail. ``sleep
    infinity`` stands in when there is no CMD, so that tail has something to exec
    that neither exits nor does anything.
    """
    text = _strip_comments(_join_continuations(dockerfile))
    entry: list[str] = []
    cmd: list[str] = []
    for line in text.splitlines():
        m = re.match(r"\s*(ENTRYPOINT|CMD)\s+(.+)$", line, re.I)
        if not m:
            continue
        # Last one wins, as in docker.
        argv = _argv_from_instruction(m.group(2))
        if m.group(1).upper() == "ENTRYPOINT":
            entry = argv
        else:
            cmd = argv
    if not entry:
        return None
    return shlex.join(entry + (cmd or ["sleep", "infinity"]))


def _build_context(env_dir: str, dockerfile: str) -> dict[str, str]:
    """``{relpath: base64}`` for every local COPY/ADD source under ``environment/``.

    ``DaytonaSandbox._declarative_image`` writes these back next to the Dockerfile
    so the SDK resolves the COPY sources and uploads them as the build context.
    Base64 because the corpus copies binaries (images, archives) as well as text.

    Raises FileNotFoundError when a source is missing (an unbuildable task) and
    ValueError when the context exceeds ``_MAX_CONTEXT_BYTES``.
    """
    context: dict[str, str] = {}
    total = 0
    for rest in _LOCAL_COPY.findall(_join_continuations(dockerfile)):
        if _COPY_HEREDOC.match(rest):
            continue
        parts = [p for p in shlex.split(rest) if not _COPY_FLAG.match(p)]
        for src in parts[:-1]:
            abspath = os.path.normpath(os.path.join(env_dir, src))
            if not os.path.exists(abspath):
                raise FileNotFoundError(src)
            files = (
                [os.path.join(dp, fn) for dp, _d, fs in os.walk(abspath) for fn in fs]
                if os.path.isdir(abspath)
                else [abspath]
            )
            for path in files:
                rel = os.path.relpath(path, env_dir)
                if rel.startswith(".."):
                    raise FileNotFoundError(f"{src} escapes environment/")
                with open(path, "rb") as f:
                    blob = f.read()
                total += len(blob)
                if total > _MAX_CONTEXT_BYTES:
                    raise ValueError(f"context > {_MAX_CONTEXT_BYTES} bytes")
                context[rel] = base64.b64encode(blob).decode()
    return context


def _oracle_commands(solve_sh: str) -> int:
    """Approximate the number of shell commands the reference solution executes.

    This is the task's own difficulty measure and the one that matters for RL: a
    rollout capped at N turns cannot solve a task whose oracle needs more than N
    commands. Heredoc bodies are data, not commands, so they are skipped; ``&&``,
    ``;`` and ``|`` chains each count as another command.
    """
    count, in_heredoc, terminator = 0, False, None
    for raw in solve_sh.splitlines():
        line = raw.strip()
        if in_heredoc:
            if terminator and line == terminator:
                in_heredoc = False
            continue
        opener = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        if opener:
            in_heredoc, terminator = True, opener.group(1)
        if not line or line.startswith("#"):
            continue
        if line in ("fi", "done", "esac", "else", "}", "{", ")", ";;"):
            continue
        count += 1 + len(re.findall(r"&&|\|\||;(?!;)", line))
    return count


def _grading_fixtures(task_dir: str) -> dict[str, str]:
    """``{relpath: content}`` for every text file under ``tests/`` except test.sh
    (uploaded separately). grading.py maps ``tests/*`` -> ``/tests/*`` at grade time."""
    fixtures: dict[str, str] = {}
    base = os.path.join(task_dir, "tests")
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, task_dir)
            if rel == os.path.join("tests", "test.sh"):
                continue
            try:
                with open(abspath, encoding="utf-8") as f:
                    fixtures[rel] = f.read()
            except (UnicodeDecodeError, OSError):
                continue
    return fixtures


# Per-task Daytona sizing floors. The dataset's declared/estimated numbers are
# lower bounds (TerminalWorld's est_disk_mb is explicitly "a floor with slack"),
# and an RL agent explores far more than the oracle solution, so clamp each
# resource up to a safe minimum. CPU is the binding Daytona quota, so honoring the
# median req_cpus=1 (vs the flat default 2) is where the savings come from; disk
# keeps a generous floor because agent writes dwarf the oracle's est_disk_mb.
_DAYTONA_CPU_FLOOR = 1
_DAYTONA_MEM_GB_FLOOR = 2
_DAYTONA_DISK_GB_FLOOR = 10


def _load_resource_map(parquet_path: str) -> dict[str, dict[str, int]]:
    """Map task_id -> {daytona_cpu, daytona_mem_gb, daytona_disk_gb} from the
    dataset's own resource columns (req_cpus / req_memory_mb / est_disk_mb),
    each clamped to the floors above. A missing/null cell is omitted so the sandbox
    falls back to that field's TT_DAYTONA_* env default."""
    import pandas as pd  # local import: only needed with --metadata-parquet

    df = pd.read_parquet(parquet_path)
    id_col = "task_id" if "task_id" in df.columns else df.columns[0]
    out: dict[str, dict[str, int]] = {}
    for _, row in df.iterrows():
        tid = row.get(id_col)
        if not isinstance(tid, str):
            continue
        res: dict[str, int] = {}
        cpus = row.get("req_cpus")
        if cpus is not None and not pd.isna(cpus):
            res["daytona_cpu"] = max(_DAYTONA_CPU_FLOOR, int(round(float(cpus))))
        mem_mb = row.get("req_memory_mb")
        if mem_mb is not None and not pd.isna(mem_mb):
            res["daytona_mem_gb"] = max(
                _DAYTONA_MEM_GB_FLOOR, math.ceil(float(mem_mb) / 1024)
            )
        disk_mb = row.get("est_disk_mb")
        if disk_mb is not None and not pd.isna(disk_mb):
            res["daytona_disk_gb"] = max(
                _DAYTONA_DISK_GB_FLOOR, math.ceil(float(disk_mb) / 1024)
            )
        if res:
            out[tid] = res
    return out


def _load_pretest_map(parquet_path: str) -> dict[str, tuple[str, str]]:
    """Map task_id -> (pre_test_sh, pre_test_env_identity) from the dataset's own hook columns.

    Same file as the resource columns and the same rule: the columns are OPTIONAL, an absent column or an empty
    cell simply yields no entry and the task grades exactly as it does today. The hook travels with the data, so
    a corpus that gains checks later needs no change here (see prepare_tmax_data._PRETEST_COLUMNS)."""
    import pandas as pd  # local import: only needed with --metadata-parquet

    df = pd.read_parquet(parquet_path)
    if "pre_test_sh" not in df.columns:
        return {}
    id_col = "task_id" if "task_id" in df.columns else df.columns[0]
    out: dict[str, tuple[str, str]] = {}
    for _, row in df.iterrows():
        tid = row.get(id_col)
        script = row.get("pre_test_sh")
        if not isinstance(tid, str) or not isinstance(script, str) or not script:
            continue
        stamp = row.get("pre_test_env_identity")
        out[tid] = (script, stamp if isinstance(stamp, str) else "")
    return out


def _to_row(
    task_dir: str,
    *,
    task_id: str | None = None,
    inject_agent_runtime: bool = False,
    resources: dict[str, int] | None = None,
    pretest: tuple[str, str] | None = None,
) -> tuple[dict | None, str]:
    """Build one output row, or ``(None, reason)`` when the task is filtered out.

    ``task_id`` defaults to the directory's name, which is the id in a corpus
    tree; a revision directory (``tasks/<task>/r3/``) passes the id explicitly.
    """
    task_id = task_id or os.path.basename(task_dir.rstrip("/"))
    paths = {
        "instruction": os.path.join(task_dir, "instruction.md"),
        "test_sh": os.path.join(task_dir, "tests", "test.sh"),
        "dockerfile": os.path.join(task_dir, "environment", "Dockerfile"),
    }
    for name, p in paths.items():
        if not os.path.exists(p):
            return None, f"missing_{name}"

    with open(paths["dockerfile"], encoding="utf-8") as f:
        dockerfile = f.read()
    if _REJECT_PRIVILEGED.search(_strip_comments(_join_continuations(dockerfile))):
        return None, "needs_privileged"

    env_dir = os.path.join(task_dir, "environment")
    try:
        build_context = _build_context(env_dir, dockerfile)
    except FileNotFoundError:
        return None, "copy_source_missing"
    except ValueError:
        return None, "build_context_too_large"
    # After _build_context: the appended step has no COPY sources of its own, and a
    # trailing RUN in the final stage leaves WORKDIR/ENTRYPOINT/CMD untouched.
    if inject_agent_runtime:
        dockerfile = dockerfile.rstrip("\n") + "\n" + _AGENT_RUNTIME_BLOCK

    with open(paths["instruction"], encoding="utf-8") as f:
        instruction = _strip_canary(f.read())
    with open(paths["test_sh"], encoding="utf-8") as f:
        test_sh = f.read()
    if not instruction.strip() or not test_sh.strip():
        return None, "empty_instruction_or_verifier"
    # The whole reward signal is this file; a verifier that never writes it would
    # silently score 0 for every rollout.
    if "reward.txt" not in test_sh and "reward.json" not in test_sh:
        return None, "verifier_writes_no_reward"

    # Per-task sandbox sizing from the task's own declaration ([environment]
    # cpus / memory_mb in task.toml). Only emitted when it EXCEEDS the fleet
    # defaults (2 vCPU / 4 GiB): absent fields keep the env-default sizing, so
    # rows for ordinary tasks are byte-identical to before.
    daytona_mem_gb = daytona_cpu = agent_timeout_sec = None
    toml_path = os.path.join(task_dir, "task.toml")
    if os.path.exists(toml_path):
        try:
            import tomllib

            with open(toml_path, "rb") as f:
                _toml = tomllib.load(f)
            env = _toml.get("environment", {})
            # The task's own agent budget. TerminalWorld declares one for every
            # task ([agent] timeout_sec: 600s for the short ones, 3600s for the
            # long), sized at roughly 3x the expert_time_estimate_min it also
            # carries. Dropping it made every task take the flat
            # SWE_TIME_BUDGET_SEC, which is 2x the corpus total and lets an agent
            # burn 40 minutes on a task the benchmark allots 10 -- and a rollout
            # that runs long also ages its whole group toward the batcher's
            # staleness drop, since group age is measured from its OLDEST turn.
            _ts = (_toml.get("agent") or {}).get("timeout_sec")
            if isinstance(_ts, (int, float)) and _ts > 0:
                agent_timeout_sec = float(_ts)
            mb = env.get("memory_mb")
            if isinstance(mb, (int, float)) and mb > 4096:
                daytona_mem_gb = -(-int(mb) // 1024)
            cp = env.get("cpus")
            if isinstance(cp, (int, float)) and cp > 2:
                daytona_cpu = int(cp)
        except Exception:
            pass  # sizing is an optimization; a bad toml never blocks the row

    solve_path = os.path.join(task_dir, "solution", "solve.sh")
    oracle_commands = 0
    if os.path.exists(solve_path):
        with open(solve_path, encoding="utf-8", errors="replace") as f:
            oracle_commands = _oracle_commands(f.read())

    metadata = {
        "instance_id": task_id,
        "image": "",
        "dockerfile": dockerfile,
        "workdir": _workdir_from_dockerfile(dockerfile),
        "problem_statement": instruction,
        # The reference solution's command count: the turn budget a rollout needs
        # before it can possibly solve this task. Used by --max-oracle-commands.
        "oracle_commands": oracle_commands,
        "tmax": {
            "test_sh": test_sh,
            "fixtures": _grading_fixtures(task_dir),
            "reward_path": _REWARD_PATH,
            # The pre-verify hook, when the dataset carries one for this task. The episode identity is computed
            # from the bundle: a seeds-layout package whose Dockerfile is a bare single FROM resolves to
            # "image:<base>" and matches an "image:<ref>" stamp, so the check fires on this path too; a
            # Dockerfile that builds resolves to its sha and the check is skipped, as on the tmax path.
            **_pretest_tmax_fields(
                task_id,
                task_dir,
                "",
                # A packaged one-line `FROM docker.io/<repo>:<tag>` must resolve to the UNPREFIXED
                # "image:<repo>:<tag>" to match env_stamp.env_identity (which carries no registry prefix);
                # passing "" here left the prefix on and skipped the hook for every prefixed-FROM row.
                _DEFAULT_IMAGE_PREFIX,
                pre_test_sh=(pretest or ("", ""))[0],
                stamped_identity=(pretest or ("", ""))[1],
            ),
        },
    }
    if build_context:
        metadata["build_context"] = build_context
    if daytona_mem_gb:
        metadata["daytona_mem_gb"] = daytona_mem_gb
    if daytona_cpu:
        metadata["daytona_cpu"] = daytona_cpu
    # Off by default for TRAINING rows: declared budgets are sized ~3x an
    # EXPERT's time, and the policy is not an expert -- when 692 backfilled
    # rows went live on 08-29 (mostly 600-1800s, floored to 900), 75-85% of
    # rollouts started dying at the budget with few or zero turns (the boot
    # before: 7108 completed / 30 errors; every boot after: 3-25% completion).
    # Training rows keep the launcher's SWE_TIME_BUDGET_SEC, exactly as the
    # rollouter's budget note says; the TB-2.0 eval prep opts in.
    if agent_timeout_sec and os.environ.get("SWE_EMIT_AGENT_TIMEOUT", "0") == "1":
        metadata["agent_timeout_sec"] = agent_timeout_sec
    entrypoint = _entrypoint_command(dockerfile)
    if entrypoint:
        metadata["entrypoint"] = entrypoint
    # Per-task Daytona sizing from the dataset's resource columns (only the fields
    # present for this task; absent ones fall back to the TT_DAYTONA_* defaults).
    if resources:
        metadata.update(resources)
    return {"prompt": instruction, "label": task_id, "metadata": metadata}, "ok"


def build_rows(
    tasks_roots: list[str],
    *,
    limit: int | None = None,
    seed: int = 42,
    max_oracle_commands: int | None = None,
    inject_agent_runtime: bool = False,
    resource_map: dict[str, dict[str, int]] | None = None,
    pretest_map: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Convert every task dir under ``tasks_roots`` to a row, applying the filters.

    Task dirs are shuffled before the ``limit`` cut so a subset spans the whole
    corpus rather than one alphabetical corner of it.
    """
    dirs: list[str] = []
    for root in tasks_roots:
        dirs.extend(
            os.path.join(root, d)
            for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d))
        )
    random.Random(seed).shuffle(dirs)

    rows: list[dict] = []
    reasons: dict[str, int] = {}
    for d in dirs:
        task_id = os.path.basename(d.rstrip("/"))
        row, reason = _to_row(
            d,
            inject_agent_runtime=inject_agent_runtime,
            resources=(resource_map or {}).get(task_id),
            pretest=(pretest_map or {}).get(task_id),
        )
        if (
            row is not None
            and max_oracle_commands is not None
            and row["metadata"]["oracle_commands"] > max_oracle_commands
        ):
            row, reason = None, "oracle_over_turn_budget"
        reasons[reason] = reasons.get(reason, 0) + 1
        if row is not None:
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
    return rows, reasons


def _write_jsonl(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output rts_train.jsonl path")
    ap.add_argument(
        "--tasks-root",
        action="append",
        required=True,
        metavar="DIR",
        help="extracted RTS 'tasks/' dir (repeat to mix shards / difficulties)",
    )
    ap.add_argument("--limit", type=int, default=None, help="emit at most N tasks")
    ap.add_argument("--seed", type=int, default=42, help="task-order shuffle seed")
    ap.add_argument(
        "--max-oracle-commands",
        type=int,
        default=None,
        metavar="N",
        help="drop tasks whose reference solve.sh runs more than N commands -- a "
        "rollout capped at T turns cannot solve one needing more than T of them, "
        "so pair this with SWE_MAX_TURNS (e.g. N=64 under a 128-turn cap)",
    )
    ap.add_argument(
        "--inject-agent-runtime",
        action="store_true",
        help="append a tmux install step to each Dockerfile -- required for corpora "
        "that ship the upstream task content verbatim (TerminalWorld-Seeds) rather "
        "than RTS, whose Dockerfiles already carry it",
    )
    ap.add_argument(
        "--metadata-parquet",
        default=None,
        metavar="PATH",
        help="dataset metadata/tasks.parquet -- read per-task req_cpus / "
        "req_memory_mb / est_disk_mb and emit daytona_cpu/mem_gb/disk_gb so each "
        "sandbox is sized to the task instead of the flat TT_DAYTONA_* defaults "
        "(missing fields fall back to those defaults), and the pre_test_sh / "
        "pre_test_env_identity columns when the dataset carries them",
    )
    ap.add_argument(
        "--smoke-size",
        type=int,
        default=0,
        help="also write rts_smoke.jsonl with N rows",
    )
    args = ap.parse_args()

    resource_map = (
        _load_resource_map(args.metadata_parquet) if args.metadata_parquet else None
    )
    if resource_map is not None:
        print(f"loaded per-task resources for {len(resource_map)} tasks")
    pretest_map = (
        _load_pretest_map(args.metadata_parquet) if args.metadata_parquet else None
    )
    if pretest_map:
        print(f"loaded a pre_test check for {len(pretest_map)} tasks")

    rows, reasons = build_rows(
        args.tasks_root,
        limit=args.limit,
        seed=args.seed,
        max_oracle_commands=args.max_oracle_commands,
        inject_agent_runtime=args.inject_agent_runtime,
        resource_map=resource_map,
        pretest_map=pretest_map,
    )
    if not rows:
        print(f"ERROR: produced 0 rows (filters: {reasons})", file=sys.stderr)
        sys.exit(1)
    # Same guard prepare_tmax_data runs: if any row carries a pre_test but 0 of the stamped rows match their
    # episode identity, every task would skip the pin check from round 0 (e.g. a registry-prefix mismatch) --
    # raise at prep time rather than ship a silently-disabled hook on this path too.
    _sc_matched, _sc_total, _sc_unstamped = selfcheck_env_identities(rows)
    if _sc_total:
        print(
            f"env-identity self-check: {_sc_matched}/{_sc_total} stamped rows match their episode identity"
        )
    if _sc_unstamped:
        print(
            f"WARNING: {_sc_unstamped} row(s) carry pre_test_sh with no usable env identity -- their pin "
            "check can never run",
            file=sys.stderr,
        )
    _write_jsonl(rows, args.out)
    print(f"wrote {len(rows)} RTS tasks -> {args.out}")
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:32s} {n}")

    if args.smoke_size > 0:
        smoke = os.path.join(
            os.path.dirname(os.path.abspath(args.out)), "rts_smoke.jsonl"
        )
        _write_jsonl(rows[: args.smoke_size], smoke)
        print(f"wrote {min(args.smoke_size, len(rows))} smoke tasks -> {smoke}")


if __name__ == "__main__":
    main()
