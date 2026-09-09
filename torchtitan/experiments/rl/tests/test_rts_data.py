# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset, TMaxSample
from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
    _entrypoint_command,
    _oracle_commands,
    build_rows,
)

_DOCKERFILE = """FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends gcc
WORKDIR /app
WORKDIR /srv/final
"""

_TEST_SH = """#!/bin/bash
mkdir -p /logs/verifier
echo 1 > /logs/verifier/reward.txt
"""


def _write_task(
    root, task_id: str, *, dockerfile: str = _DOCKERFILE, test_sh: str = _TEST_SH
):
    """Lay out one RTS-shaped task dir under ``root``."""
    task = root / task_id
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir(parents=True)
    (task / "solution").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text(dockerfile)
    (task / "instruction.md").write_text(f"do the thing for {task_id}")
    (task / "tests" / "test.sh").write_text(test_sh)
    (task / "tests" / "test_state.py").write_text("def test_x(): assert True\n")
    (task / "solution" / "solve.sh").write_text("true\n")
    return task


def test_row_carries_dockerfile_and_last_workdir(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_aaa")

    rows, reasons = build_rows([str(root)])

    assert reasons == {"ok": 1}
    (row,) = rows
    md = row["metadata"]
    assert md["dockerfile"] == _DOCKERFILE
    # No published image: the sandbox backend must build the Dockerfile instead.
    assert md["image"] == ""
    # The LAST WORKDIR wins -- that is where the agent's commands land.
    assert md["workdir"] == "/srv/final"
    assert md["tmax"]["test_sh"] == _TEST_SH
    # test.sh is uploaded separately, never as a grading fixture.
    assert set(md["tmax"]["fixtures"]) == {"tests/test_state.py"}


@pytest.mark.parametrize(
    "dockerfile",
    [
        # Needs a real init system as PID 1.
        'FROM ubuntu:22.04\nWORKDIR /app\nENTRYPOINT ["/sbin/init"]\n',
        "FROM ubuntu:22.04\nWORKDIR /app\nRUN systemctl enable nginx\n",
        # Needs the host docker daemon.
        "FROM ubuntu:22.04\nWORKDIR /app\nRUN ls /var/run/docker.sock\n",
    ],
)
def test_dockerfiles_needing_a_privileged_host_are_filtered(tmp_path, dockerfile):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_bbb", dockerfile=dockerfile)

    rows, reasons = build_rows([str(root)])

    assert rows == []
    assert reasons == {"needs_privileged": 1}


@pytest.mark.parametrize(
    "dockerfile",
    [
        # Only MENTIONS systemd/compose -- the corpus does this in comments that
        # explain solve.sh. A substring filter rejected ~1700 such tasks, of which
        # none actually run an init system.
        "FROM ubuntu:22.04\nWORKDIR /app\n"
        "# systemd provides systemctl used in solve.sh\n"
        "RUN apt-get install -y systemd\n",
        # EOL base images still build: the corpus rewrites sources.list to the
        # Debian archive itself.
        "FROM centos:7\nWORKDIR /app\nRUN yum install -y gcc\n",
        # COPY --from pulls from another image, so it needs no build context.
        "FROM ubuntu:22.04\nWORKDIR /app\n"
        "COPY --from=composer:latest /usr/bin/composer /usr/bin/composer\n",
    ],
)
def test_buildable_dockerfiles_are_kept(tmp_path, dockerfile):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_ccc", dockerfile=dockerfile)

    rows, reasons = build_rows([str(root)])

    assert reasons == {"ok": 1}
    assert rows[0]["metadata"].get("build_context") is None


def test_local_copy_sources_are_carried_as_build_context(tmp_path):
    """A local COPY ships its sources with the row, so no filter is needed."""
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(
        root,
        "rts_task_ddd",
        dockerfile="FROM ubuntu:22.04\nWORKDIR /app\n"
        "COPY seed.bin /app/seed.bin\nCOPY fixtures /app/fixtures\n",
    )
    (task / "environment" / "seed.bin").write_bytes(b"\x00\x01\x02binary")
    (task / "environment" / "fixtures").mkdir()
    (task / "environment" / "fixtures" / "a.txt").write_text("hello")

    rows, reasons = build_rows([str(root)])

    assert reasons == {"ok": 1}
    ctx = rows[0]["metadata"]["build_context"]
    assert set(ctx) == {"seed.bin", "fixtures/a.txt"}
    # Base64 so binary COPY sources survive the JSONL round-trip.
    assert base64.b64decode(ctx["seed.bin"]) == b"\x00\x01\x02binary"
    assert base64.b64decode(ctx["fixtures/a.txt"]) == b"hello"


@pytest.mark.parametrize(
    "dockerfile,expected",
    [
        # Exec form, no CMD: sleep infinity stands in for "$@".
        (
            'FROM ubuntu:22.04\nWORKDIR /app\nENTRYPOINT ["/entrypoint.sh"]\n',
            "/entrypoint.sh sleep infinity",
        ),
        # CMD supplies the arguments the entrypoint execs.
        (
            'FROM ubuntu:22.04\nWORKDIR /app\nENTRYPOINT ["/entrypoint.sh"]\n'
            'CMD ["sleep", "3600"]\n',
            "/entrypoint.sh sleep 3600",
        ),
        # Shell form is what docker wraps in /bin/sh -c.
        (
            "FROM ubuntu:22.04\nWORKDIR /app\nENTRYPOINT /start.sh --serve\n",
            "/bin/sh -c '/start.sh --serve' sleep infinity",
        ),
        # Last ENTRYPOINT wins, as in docker.
        (
            'FROM ubuntu:22.04\nENTRYPOINT ["/a.sh"]\nENTRYPOINT ["/b.sh"]\n',
            "/b.sh sleep infinity",
        ),
        # A CMD-only image has nothing to start: docker would run CMD as PID 1, but
        # it is the container's main process, not environment setup.
        ('FROM ubuntu:22.04\nCMD ["sleep", "infinity"]\n', None),
        ("FROM ubuntu:22.04\nWORKDIR /app\n", None),
    ],
)
def test_entrypoint_command(dockerfile, expected):
    assert _entrypoint_command(dockerfile) == expected


def test_entrypoint_reaches_the_row_and_the_dataset(tmp_path):
    """The rollouter reads it off TMaxSample, so it has to survive the JSONL."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(
        root,
        "rts_task_jjj",
        dockerfile='FROM ubuntu:22.04\nWORKDIR /app\nENTRYPOINT ["/entrypoint.sh"]\n',
    )

    rows, _reasons = build_rows([str(root)])
    assert rows[0]["metadata"]["entrypoint"] == "/entrypoint.sh sleep infinity"

    path = tmp_path / "rts.jsonl"
    path.write_text(json.dumps(rows[0]) + "\n")
    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))
    assert next(iter(dataset)).entrypoint == "/entrypoint.sh sleep infinity"


def test_agent_runtime_is_not_injected_by_default(tmp_path):
    """RTS Dockerfiles already install tmux; injecting would be a no-op RUN layer."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_ggg")

    rows, _reasons = build_rows([str(root)])

    assert rows[0]["metadata"]["dockerfile"] == _DOCKERFILE


def test_injected_agent_runtime_installs_tmux_without_touching_the_workdir(tmp_path):
    """Corpora carrying upstream task content verbatim (TerminalWorld-Seeds) ship no
    tmux, which Terminus-2 needs; injected RUNs must not shift WORKDIR."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_hhh")

    rows, reasons = build_rows([str(root)], inject_agent_runtime=True)

    assert reasons == {"ok": 1}
    md = rows[0]["metadata"]
    assert md["dockerfile"].startswith("FROM ubuntu:22.04\n")
    assert "tmux" in md["dockerfile"]
    # Repair must precede the source Dockerfile's own apt RUN. Appending it after
    # that RUN cannot rescue an EOL Debian/Ubuntu image.
    assert md["dockerfile"].index("repair obsolete apt sources") < md[
        "dockerfile"
    ].index("RUN apt-get update")
    # EOL Debian images can keep a dead *-security suite after their main mirror
    # moves to archive.debian.org. A second update failure must disable that
    # optional source so tmux can still come from the main archive.
    assert "/etc/apt/sources.list.d/*.list" in md["dockerfile"]
    assert "disabled obsolete security suite" in md["dockerfile"]
    # A trailing RUN leaves the last WORKDIR in force.
    assert md["workdir"] == "/srv/final"


def test_agent_runtime_repairs_each_apt_multistage_before_its_first_run(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    dockerfile = """FROM debian:buster AS build
RUN apt-get update && apt-get install -y make
FROM scratch AS assets
COPY --from=build /tmp/out /out
FROM node:18-bullseye
RUN apt-get update && apt-get install -y git
WORKDIR /app
"""
    _write_task(root, "rts_task_multistage", dockerfile=dockerfile)

    rows, reasons = build_rows([str(root)], inject_agent_runtime=True)

    assert reasons == {"ok": 1}
    injected = rows[0]["metadata"]["dockerfile"]
    assert injected.count("repair obsolete apt sources") == 2
    assert "FROM scratch AS assets\n# harbor-agent-runtime" not in injected
    for stage in ("FROM debian:buster AS build", "FROM node:18-bullseye"):
        stage_text = injected[injected.index(stage) :]
        assert stage_text.index("repair obsolete apt sources") < stage_text.index(
            "RUN apt-get update"
        )


def test_injection_does_not_add_build_context_sources(tmp_path):
    """The injected step has no COPY of its own, so the row's context is unchanged."""
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(
        root,
        "rts_task_iii",
        dockerfile="FROM ubuntu:22.04\nWORKDIR /app\nCOPY seed.bin /app/seed.bin\n",
    )
    (task / "environment" / "seed.bin").write_bytes(b"seed")

    rows, _reasons = build_rows([str(root)], inject_agent_runtime=True)

    assert set(rows[0]["metadata"]["build_context"]) == {"seed.bin"}


def test_a_backslash_continued_copy_carries_every_source(tmp_path):
    """One instruction split over lines is still one instruction.

    Scanning line by line sees only ``a.yml \\``, whose trailing backslash makes
    ``shlex.split`` raise -- reported as an unbuildable task -- and hides every
    source after the first.
    """
    root = tmp_path / "tasks"
    root.mkdir()
    task = _write_task(
        root,
        "rts_task_cont",
        dockerfile="FROM ubuntu:22.04\nWORKDIR /app\n"
        "COPY a.yml \\\n     b.yml \\\n     /app/\n",
    )
    (task / "environment" / "a.yml").write_text("a: 1")
    (task / "environment" / "b.yml").write_text("b: 2")

    rows, reasons = build_rows([str(root)])

    assert reasons == {"ok": 1}
    assert set(rows[0]["metadata"]["build_context"]) == {"a.yml", "b.yml"}


def test_a_copy_heredoc_needs_no_build_context(tmp_path):
    """BuildKit ``COPY <<'EOF'`` inlines its content, so there is no local source.

    Treating ``<<'EOF'`` as a filename makes the task look like it references a file
    the corpus does not ship.
    """
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(
        root,
        "rts_task_heredoc",
        dockerfile="FROM ubuntu:22.04\nWORKDIR /app\n"
        "COPY <<'EOF' /app/config.yml\nkey: value\nEOF\n",
    )

    rows, reasons = build_rows([str(root)])

    assert reasons == {"ok": 1}
    assert rows[0]["metadata"].get("build_context") is None


@pytest.mark.parametrize(
    "dockerfile",
    [
        # 87 of 89 --privileged / docker.sock hits in the corpus are prose about how
        # the task was authored; the task itself builds and grades in a plain
        # container (and a Dockerfile cannot grant privilege anyway).
        "FROM ubuntu:22.04\nWORKDIR /app\n"
        "# the reference solution was developed with --privileged\nRUN ls /app\n",
        "FROM ubuntu:22.04\nWORKDIR /app\n"
        "# NOTE: do not mount /var/run/docker.sock here\nRUN ls /app\n",
    ],
)
def test_privileged_mentions_inside_comments_are_not_filtered(tmp_path, dockerfile):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_prose", dockerfile=dockerfile)

    rows, reasons = build_rows([str(root)])

    assert reasons == {"ok": 1}
    assert len(rows) == 1


def test_missing_copy_source_is_filtered(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(
        root,
        "rts_task_eee",
        dockerfile="FROM ubuntu:22.04\nWORKDIR /app\nCOPY gone.txt /app/\n",
    )

    rows, reasons = build_rows([str(root)])

    assert rows == []
    assert reasons == {"copy_source_missing": 1}


@pytest.mark.parametrize(
    "solve_sh,expected",
    [
        ("#!/bin/bash\nls\n", 1),
        # && and ; chain separate commands (2 + 2), while a | pipeline is a single
        # command -- the agent issues it in one turn.
        ("ls && pwd\ncd /tmp; ls\ncat a | wc -l\n", 5),
        # Comments, blank lines and block terminators are not commands.
        ("# note\n\nif true; then\n  ls\nfi\n", 3),
        # A heredoc body is data, not commands.
        ("cat <<'EOF' > f\nls && pwd\nrm -rf /\nEOF\necho done\n", 2),
    ],
)
def test_oracle_command_count(solve_sh, expected):
    assert _oracle_commands(solve_sh) == expected


def test_max_oracle_commands_drops_tasks_over_the_turn_budget(tmp_path):
    """A rollout capped at T turns cannot solve a task whose oracle needs > T."""
    root = tmp_path / "tasks"
    root.mkdir()
    short = _write_task(root, "rts_task_short")
    long = _write_task(root, "rts_task_long")
    (short / "solution" / "solve.sh").write_text("ls\npwd\n")
    (long / "solution" / "solve.sh").write_text(
        "".join(f"echo {i}\n" for i in range(50))
    )

    rows, reasons = build_rows([str(root)], max_oracle_commands=10)

    assert [r["label"] for r in rows] == ["rts_task_short"]
    assert reasons["oracle_over_turn_budget"] == 1
    assert rows[0]["metadata"]["oracle_commands"] == 2


def test_verifier_without_reward_file_is_filtered(tmp_path):
    """A verifier that never writes reward.txt would silently score every rollout 0."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_ccc", test_sh="#!/bin/bash\npytest /tests\n")

    rows, reasons = build_rows([str(root)])

    assert rows == []
    assert reasons == {"verifier_writes_no_reward": 1}


def test_limit_is_applied_after_shuffle(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    for i in range(8):
        _write_task(root, f"rts_task_{i:03d}")

    first = [r["label"] for r in build_rows([str(root)], limit=3, seed=1)[0]]
    same = [r["label"] for r in build_rows([str(root)], limit=3, seed=1)[0]]
    other = [r["label"] for r in build_rows([str(root)], limit=3, seed=2)[0]]

    assert len(first) == 3
    assert first == same, "same seed must give a reproducible subset"
    assert first != other, "the subset must span the corpus, not one fixed corner"


def test_dataset_loads_a_dockerfile_only_row(tmp_path):
    """TMaxDataset must accept rows with no image, and reject rows with neither."""
    root = tmp_path / "tasks"
    root.mkdir()
    _write_task(root, "rts_task_ddd")
    rows, _ = build_rows([str(root)])
    path = tmp_path / "rts.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    dataset = TMaxDataset(TMaxDataset.Config(data_path=str(path), shuffle=False))
    sample = next(iter(dataset))
    assert sample.dockerfile == _DOCKERFILE
    assert sample.image == ""
    assert sample.workdir == "/srv/final"

    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({"metadata": {"instance_id": "x", "tmax": {"a": 1}}}) + "\n"
    )
    with pytest.raises(ValueError, match="missing image/dockerfile/tmax"):
        TMaxDataset(TMaxDataset.Config(data_path=str(bad), shuffle=False))


@pytest.mark.parametrize(
    "declared,expected",
    [
        # TB-2.0's common values are all below the floor, so they are raised to it:
        # the declared numbers are sized for a fast local runner, not for a 120-turn
        # episode whose wall clock is ~98% in-sandbox command execution.
        (900.0, 7200),
        (1800.0, 7200),
        (3600.0, 7200),
        (7200.0, 7200),
        # Above the floor -> its own value is kept (the floor only ever raises).
        (12000.0, 12000),
        # Corpora that declare nothing (RTS, TerminalWorld) keep the configured
        # budget: training wall clock stays a launcher decision.
        (None, 2400),
    ],
)
def test_agent_budget_policy(declared, expected):
    from torchtitan.experiments.rl.examples.tmax.rollouter import TMaxRollouter

    sample = TMaxSample(
        instance_id="t",
        image="img",
        workdir="/app",
        problem_statement="do it",
        agent_timeout_sec=declared,
        tmax={"test_sh": "x"},
    )
    rollouter = SimpleNamespace(
        _time_budget_sec=2400,
        _agent_budget_sec=TMaxRollouter._agent_budget_sec,
    )
    assert TMaxRollouter._agent_budget_sec(rollouter, sample) == expected


def test_guard_covers_the_agent_budget():
    """The whole-rollout guard must not cut a raised budget short."""
    from torchtitan.experiments.rl.examples.tmax.rollouter import TMaxRollouter

    rollouter = SimpleNamespace(_eval_timeout_sec=600)
    guard = TMaxRollouter._guard_for(rollouter, 7200)
    assert guard > 7200 and guard == 7200 + 600 + 300
