# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""prepare_tb2_1_data's TB4 rows: what a Harbor task declares beyond an image.

Every field here is written only for a separate-verifier (TB4) task that
declares it; a task graded in its own container gets none of them.
"""

from __future__ import annotations

import tomllib

import pytest

from torchtitan.experiments.rl.examples.tmax import prepare_tb2_1_data as B

_PIN = "@sha256:" + "a" * 64

_BASE_TOML = f"""schema_version = "1.2"
artifacts = ["/app/out.json"]

[task]
name = "terminal-bench/demo-task"

[verifier]
timeout_sec = 600.0
environment_mode = "separate"

[verifier.environment]
cpus = 2
memory_mb = 4096
storage_mb = 10240
docker_image = "harborframework/terminal-bench:demo-verifier{_PIN}"

[agent]
timeout_sec = 3600.0

[environment]
build_timeout_sec = 900.0
docker_image = "harborframework/terminal-bench:demo-environment{_PIN}"
cpus = 2
memory_mb = 4096
storage_mb = 10240
"""


def _task(root, toml=_BASE_TOML, *, dockerfile="FROM ubuntu:24.04\nWORKDIR /app\n", compose=None, name="demo-task"):
    task = root / name
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "environment" / "Dockerfile").write_text(dockerfile)
    if compose is not None:
        (task / "environment" / "docker-compose.yaml").write_text(compose)
    (task / "tests" / "test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n")
    (task / "tests" / "Dockerfile").write_text(
        "FROM python:3.11\nRUN pip install --no-cache-dir "
        "'gnucleus-freecad-validator[render]==0.1.3'\nCOPY . /tests/\n"
    )
    (task / "instruction.md").write_text("do it")
    (task / "task.toml").write_text(toml)
    return task


def _row(tmp_path, toml=_BASE_TOML, **kw):
    root = tmp_path / "tasks"
    root.mkdir(parents=True, exist_ok=True)
    _task(root, toml, **kw)
    (row,), skipped = B.build_rows(tasks_root=str(root))
    assert skipped == {}
    return row["metadata"]


def test_a_plain_tb4_task_gains_only_the_convention_dir(tmp_path):
    md = _row(tmp_path)
    for key in ("daytona_gpu", "daytona_target", "compose", "healthcheck", "agent_user"):
        assert key not in md
    sv = md["tmax"]["separate_verifier"]
    assert sv["convention_dir"] == "/logs/artifacts"
    assert not {"collect", "env", "network_block_all", "daytona_gpu"} & set(sv)


def test_a_shared_environment_task_gains_nothing(tmp_path):
    toml = _BASE_TOML.replace('environment_mode = "separate"\n', "") + "gpus = 1\n"
    md = _row(tmp_path, toml, dockerfile="FROM ubuntu:24.04\nUSER nobody\n")
    assert "separate_verifier" not in md["tmax"]
    for key in ("daytona_gpu", "compose", "healthcheck", "agent_user"):
        assert key not in md


def test_a_gpu_task_and_its_verifier_run_in_the_gpu_region(tmp_path):
    toml = _BASE_TOML.replace(
        "[environment]\n", '[environment]\ngpus = 1\ngpu_types = ["H100"]\n'
    ).replace("[verifier.environment]\n", '[verifier.environment]\ngpus = 1\ngpu_types = ["H100"]\n')
    md = _row(tmp_path, toml)
    assert (md["daytona_gpu"], md["daytona_gpu_type"], md["daytona_target"]) == (1, ["H100"], "earth")
    sv = md["tmax"]["separate_verifier"]
    assert (sv["daytona_gpu"], sv["daytona_gpu_type"], sv["daytona_target"]) == (1, ["H100"], "earth")
    root = tmp_path / "tasks"
    (row,), _ = B.build_rows(tasks_root=str(root), gpu_target="mars")
    assert row["metadata"]["daytona_target"] == "mars"


def test_a_compose_task_carries_its_released_project(tmp_path):
    compose = f"services:\n  main:\n    image: x{_PIN}\n  api:\n    image: y{_PIN}\n"
    md = _row(tmp_path, compose=compose)
    assert md["compose"] == {"docker_compose": compose, "build_timeout_sec": 900.0}
    assert md["image"].endswith(_PIN)  # main is still the row's own image


def test_healthcheck_gets_harbors_defaults(tmp_path):
    toml = _BASE_TOML + '\n[environment.healthcheck]\ncommand = "true"\nretries = 30\n'
    md = _row(tmp_path, toml)
    assert md["healthcheck"] == {
        "command": "true",
        "interval_sec": 5.0,
        "timeout_sec": 30.0,
        "start_period_sec": 0.0,
        "start_interval_sec": 5.0,
        "retries": 30,
    }


@pytest.mark.parametrize(
    "dockerfile, user",
    [
        ("FROM ubuntu:24.04\nUSER agent\n", "agent"),
        ("FROM ubuntu:24.04\nUSER root\n", ""),
        ("FROM ubuntu:24.04\nUSER agent\nUSER 0\n", ""),
        ("FROM ubuntu:24.04\nUSER agent:agent\n", "agent"),
        # the final stage inherits from the stage it builds FROM
        ("FROM python:3.12 AS base\nUSER nobody\nFROM base\nRUN true\n", "nobody"),
        # a later stage built FROM an image, not the earlier stage
        ("FROM python:3.12 AS build\nUSER nobody\nFROM ubuntu:24.04\n", ""),
        # a heredoc body is not parsed as instructions
        ("FROM ubuntu:24.04\nRUN <<EOF\nfrom pathlib import Path\nuser nobody\nEOF\n", ""),
        ("FROM ubuntu:24.04\nRUN python3 - <<'PY'\nFROM x\nPY\nUSER agent\n", "agent"),
    ],
)
def test_agent_user_is_the_images_final_user(tmp_path, dockerfile, user):
    md = _row(tmp_path, dockerfile=dockerfile)
    assert md.get("agent_user", "") == user


def test_agent_user_declared_in_task_toml_wins(tmp_path):
    toml = _BASE_TOML.replace("[agent]\n", '[agent]\nuser = "agent"\n')
    assert _row(tmp_path, toml)["agent_user"] == "agent"
    toml = _BASE_TOML.replace("[agent]\n", '[agent]\nuser = "root"\n')
    assert "agent_user" not in _row(tmp_path / "b", toml, dockerfile="FROM x\nUSER nobody\n")


def test_a_numeric_agent_user_is_skipped_with_its_reason(tmp_path):
    root = tmp_path / "tasks"
    root.mkdir()
    _task(root, dockerfile="FROM ubuntu:24.04\nUSER 1000\n")
    rows, skipped = B.build_rows(tasks_root=str(root))
    assert rows == [] and "numeric uid" in skipped["demo-task"]


def test_verifier_collect_hooks_env_and_no_internet(tmp_path):
    toml = _BASE_TOML.replace(
        "[verifier.environment]\n", "[verifier.environment]\nallow_internet = false\n"
    ) + (
        '\n[verifier.env]\nPORT = 8000\nHOST = "api"\n'
        '\n[[verifier.collect]]\nservice = "api"\ncommand = "dump"\ntimeout_sec = 10.0\n'
        '\n[[verifier.collect]]\ncommand = "git diff > /tmp/agent.patch"\n'
    )
    sv = _row(tmp_path, toml)["tmax"]["separate_verifier"]
    assert sv["network_block_all"] is True
    assert sv["env"] == {"PORT": "8000", "HOST": "api"}
    assert sv["collect"] == [
        {"service": "api", "command": "dump", "timeout_sec": 10.0},
        {"service": "main", "command": "git diff > /tmp/agent.patch", "timeout_sec": 60.0},
    ]


def test_dict_artifacts_are_kept_as_harbor_declares_them(tmp_path):
    toml = _BASE_TOML.replace(
        'artifacts = ["/app/out.json"]',
        'artifacts = [{ source = "/audit/a.json", service = "portal" }, '
        '{ source = "/app/gen", exclude = ["node_modules"] }, "/app/x"]',
    )
    sv = _row(tmp_path, toml)["tmax"]["separate_verifier"]
    assert sv["artifacts"] == [
        {"source": "/audit/a.json", "service": "portal"},
        {"source": "/app/gen", "exclude": ["node_modules"]},
        "/app/x",
    ]


def test_freecad_verifier_build_is_patched_and_published_rows_are_not(tmp_path):
    toml = _BASE_TOML.replace("demo-task", "freecad-impeller")
    task = _task(tmp_path, toml, name="freecad-impeller")
    cfg = tomllib.loads(toml)
    built, why = B.separate_verifier(cfg, str(task), build=True)
    assert why == ""
    assert "'gnucleus-freecad-validator[render]==0.1.3' 'pyvista<0.49'" in built["dockerfile"]
    assert "terminal-bench#2002" in built["env_patch"]
    published, _ = B.separate_verifier(cfg, str(task), build=False)
    assert "dockerfile" not in published and "env_patch" not in published
    # Another task's identical Dockerfile is left alone.
    other, _ = B.separate_verifier(tomllib.loads(_BASE_TOML), str(task), build=True)
    assert "pyvista" not in other["dockerfile"] and "env_patch" not in other
    # Already patched: unchanged. Pin missing: refused rather than skipped.
    (task / "tests" / "Dockerfile").write_text(built["dockerfile"])
    again, _ = B.separate_verifier(cfg, str(task), build=True)
    assert again["dockerfile"] == built["dockerfile"]
    (task / "tests" / "Dockerfile").write_text("FROM python:3.11\n")
    with pytest.raises(ValueError, match="not found once"):
        B.separate_verifier(cfg, str(task), build=True)


_SOURCE_TOML = """schema_version = "1.2"
artifacts = ["/app/out.json"]

[task]
name = "terminal-bench/demo-task"

[verifier]
timeout_sec = 600.0
environment_mode = "separate"

[agent]
timeout_sec = 3600.0

[environment]
docker_image = "harborframework/terminal-bench:demo-environment@sha256:abc"
cpus = 4
memory_mb = 16384
storage_mb = 30720
gpus = 1
gpu_types = ["H100"]
allow_internet = false
"""


def test_a_verifier_without_its_own_environment_inherits_the_tasks(tmp_path):
    """Harbor: no [verifier.environment] means a deep copy of [environment]
    (60 of the 66 source-tree task.tomls). Not its image: the agent's image
    does not hold /tests."""
    task = _task(tmp_path, _SOURCE_TOML)
    cfg = tomllib.loads(_SOURCE_TOML)
    built, why = B.separate_verifier(cfg, str(task), build=True)
    assert why == ""
    assert (built["daytona_cpu"], built["daytona_mem_gb"], built["daytona_disk_gb"]) == (4, 16, 30)
    assert (built["daytona_gpu"], built["daytona_gpu_type"]) == (1, ["H100"])
    assert built["network_block_all"] is True
    assert "image" not in built
    published, why = B.separate_verifier(cfg, str(task), build=False)
    assert published is None and "docker_image" in why
    assert cfg["environment"]["docker_image"].endswith("sha256:abc")  # not mutated


def test_a_compose_sidecar_built_from_a_context_is_refused(tmp_path):
    compose = f"services:\n  main:\n    build: .\n  api:\n    build:\n      context: ./api\n  db:\n    image: x{_PIN}\n"
    root = tmp_path / "tasks"
    root.mkdir()
    _task(root, compose=compose)
    rows, skipped = B.build_rows(tasks_root=str(root))
    assert rows == [] and "['api'] build from a context" in skipped["demo-task"]


# --------------------------------------------------------------------------- #
# The harness stays root on an image that ends on the agent's user
# --------------------------------------------------------------------------- #
_AGENT_IMAGE = "FROM ubuntu:24.04\nRUN useradd -m agent\nUSER agent\nWORKDIR /app\n"


@pytest.mark.parametrize("env_patches", [True, False])
def test_a_plain_build_gives_an_agent_user_row_a_root_layer(tmp_path, env_patches):
    """Without --verify-runtime nothing else switches the image back to root, and
    the harness runs as the image's default user (mkdir /logs, the /solution
    upload and the agent-user login would fail as `agent`)."""
    root = tmp_path / "tasks"
    root.mkdir()
    _task(root, dockerfile=_AGENT_IMAGE)
    (row,), _ = B.build_rows(tasks_root=str(root), env_patches=env_patches)
    md = row["metadata"]
    assert md["agent_user"] == "agent"
    assert md["dockerfile"] == f"FROM {md['image']}\nUSER root\n"
    assert md["image"].endswith(_PIN)  # the release's digest-pinned image
    assert not B._has_own_dockerfile(md)


def test_a_root_image_still_gets_no_dockerfile(tmp_path):
    md = _row(tmp_path)
    assert "agent_user" not in md and "dockerfile" not in md


def test_verify_runtime_keeps_the_agent_user_row_on_its_tmux_layer(tmp_path, monkeypatch):
    """The derived root Dockerfile is rebuilt by --verify-runtime exactly as before:
    FROM the pinned image + the tmux layer (which starts with USER root)."""
    import asyncio
    import sys
    import types
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def boot(image, **kwargs):
        async def execute(command, **kwargs):
            return 0, "tmux 3.4", ""

        yield types.SimpleNamespace(sandbox_id="box", exec=execute)

    monkeypatch.setitem(
        sys.modules,
        "torchtitan.experiments.rl.harness.agents.claude_code",
        types.SimpleNamespace(boot_agent_sandbox=boot),
    )
    root = tmp_path / "tasks"
    root.mkdir()
    _task(root, dockerfile=_AGENT_IMAGE)
    rows, _ = B.build_rows(tasks_root=str(root))
    image = rows[0]["metadata"]["image"]
    asyncio.run(B.verify_runtime(rows, str(tmp_path / "evidence")))
    assert rows[0]["metadata"]["dockerfile"] == f"FROM {image}\n" + B._TMUX_INSTALL


def test_an_agent_user_row_is_root_before_its_env_patch_layer(tmp_path, monkeypatch):
    monkeypatch.setitem(
        B._ENV_PATCHES,
        "demo-task",
        {"upstream": "test", "image_layer": "RUN apt-get install -y demo\n"},
    )
    root = tmp_path / "tasks"
    root.mkdir()
    _task(root, dockerfile=_AGENT_IMAGE)
    (row,), _ = B.build_rows(tasks_root=str(root))
    md = row["metadata"]
    assert md["dockerfile"] == (
        f"FROM {md['image']}\nUSER root\nRUN apt-get install -y demo\n"
    )
    assert not B._has_own_dockerfile(md)
    (unpatched,), _ = B.build_rows(tasks_root=str(root), env_patches=False)
    assert unpatched["metadata"]["dockerfile"] == f"FROM {md['image']}\nUSER root\n"
