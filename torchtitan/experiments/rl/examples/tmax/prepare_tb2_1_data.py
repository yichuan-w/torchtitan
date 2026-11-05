# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Build a Terminal-Bench 2.1 EVAL JSONL in the tmax task schema.

Corrected sibling of ``prepare_tb2_data.py`` (TB-2.0), which is left in place so
past TB-2.0 numbers stay reproducible. Fixes three defects in it:

  1. TB-2.1 nests task dirs under ``tasks/``; the 2.0 script globs the snapshot
     root and emits zero rows. Either layout is accepted here.
  2. Non-UTF-8 grading fixtures were dropped, so the 7 tasks whose verifier reads
     a binary out of /tests could never score. The fixture channel is str->str, so
     binaries ride as base64 at ``tests/<name>.b64`` and a decode preamble is
     prepended to ``test_sh``. grading.py is untouched; the other 82 tasks keep a
     byte-identical ``test_sh``.
  3. Per-task ``[environment] cpus/memory_mb/storage_mb`` now become
     daytona_cpu/mem_gb/disk_gb instead of the flat TT_DAYTONA_* defaults.

Also: real TOML parsing, ``newline=""`` on text reads (universal-newline
translation silently ate 310 bytes of sparql-university's CRLF .ttl), and the run
aborts unless the tree yields 89 tasks.

Four tasks whose own reference solution now fails because an external resource
rotted (qemu-startup, qemu-alpine-ssh, build-cython-ext, mcmc-sampling-stan) get an
environment-only patch keyed by task id; see ``_ENV_PATCHES``. ``--no-env-patches``
emits the published tree unchanged.

Rows are what ``TMaxDataset`` (data.py) consumes -- same keys as the 2.0 script
plus agent/verifier timeouts, the daytona_* trio, and tb_version/task_source.

Each row carries its declared ``verifier_timeout_sec`` and the rollouter grades on
it, floored at ``TMAX_EVAL_TIMEOUT_SEC``; there is nothing to set for a full-suite
eval. Output is ~26 MB; the base64 fixtures dominate.

Pointed at the Terminal-Bench 4 release tree (``terminal-bench-prebuilt-v4.0.0``,
where every environment, verifier and compose service is a digest-pinned image),
it emits TB4 rows: those grade in a separate verifier (``tmax.separate_verifier``)
and, where the task declares them, carry what else its Harbor environment needs
(see ``_harbor_fields``): a GPU in ``--gpu-target``, its docker-compose project,
a healthcheck, the image's own non-root user, collect hooks, verifier env and
no-internet. Rows of tasks graded in their own container never get these.

    python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_1_data \
        --out tb2_1_eval.jsonl [--tasks-root /path/to/terminal-bench-2-1]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from torchtitan.experiments.rl.examples.tmax.layout import write_json_atomic
from torchtitan.experiments.rl.examples.tmax.prepare_rts_data import (
    _DAYTONA_CPU_FLOOR,
    _DAYTONA_DISK_GB_FLOOR,
    _DAYTONA_MEM_GB_FLOOR,
)
from torchtitan.experiments.rl.examples.tmax.prepare_tmax_data import (
    _DEFAULT_IMAGE_PREFIX,
    _REWARD_PATH,
)
from torchtitan.experiments.rl.examples.tmax.tb2_1_resources import RESOURCE_OVERRIDES

# HF mirror of github.com/harbor-framework/terminal-bench-2-1 (same tree, same
# commit -- its registry.json records the mirrored GitHub SHA).
_HF_REPO = "harborframework/terminal-bench-2.1"

# TB-2.1 nests the task dirs under this subdir; TB-2.0 had them at the root.
_TASKS_SUBDIR = "tasks"

# Grade-time inputs live under ``tests/`` (test.sh is uploaded separately by
# grading.py). The task environment is baked into the published image, so -- unlike
# the tmax corpus -- there is no ``environment/seeds`` tree to seed into the workdir.
_FIXTURE_ROOT = "tests"

# Fallback workdir when a task Dockerfile declares no WORKDIR. TB-2.1: 86 of 89
# tasks use /app (the others /workspace, /app/dclm, /app/personal-site).
_DEFAULT_WORKDIR = "/app"

# Daytona region with the GPU quota; a TB4 task that declares a GPU runs there.
_DEFAULT_GPU_TARGET = "earth"

# Harbor's convention artifacts dir, collected for every separate-verifier task
# whatever it declares (harbor/models/task/artifacts.py with_convention_entry).
_CONVENTION_ARTIFACTS_DIR = "/logs/artifacts"

# Harbor's defaults for a declared [environment].healthcheck and a
# [[verifier.collect]] hook (harbor/models/task/config.py).
_HEALTHCHECK_DEFAULTS = {
    "interval_sec": 5.0,
    "timeout_sec": 30.0,
    "start_period_sec": 0.0,
    "start_interval_sec": 5.0,
    "retries": 3,
}
_COLLECT_DEFAULTS = {"service": "main", "timeout_sec": 60.0}

# TB-2.1 ships exactly this many tasks. Asserted so a layout change surfaces as an
# error rather than as a short eval file.
_EXPECTED_TASKS = 89

# The harness runs as the image's default user (a Daytona exec without a user,
# docker exec without -u), and some TB4 images end on an unprivileged USER
# (risk-scorer-replay on `nobody`, rs-archive-clone and fp8-rmsnorm-gemm on
# `agent`). Every derived Dockerfile of such a row therefore switches back to root:
# the tmux layer below, and in a plain build (no --verify-runtime) this line alone
# (apply_root_layer). Without it every root step fails on those images: mkdir
# /logs, the /solution upload, the runuser of the agent-user login.
_AS_ROOT = "USER root\n"

# Run before evaluation, once per cached image. A failed install must fail the build.
# As root (apt-get exits 100 otherwise), and left at root so the harness keeps it.
_TMUX_INSTALL = _AS_ROOT + (
    "RUN if ! command -v tmux >/dev/null 2>&1; then "
    "apt-get update && DEBIAN_FRONTEND=noninteractive "
    "apt-get install -y --no-install-recommends tmux; fi\n"
    "RUN tmux -V\n"
)
_TMUX_PROBE = (
    "tmux -V && tmux -L tb-runtime-check new-session -d -s check "
    "'sleep 30' && tmux -L tb-runtime-check has-session -t check "
    "&& tmux -L tb-runtime-check kill-server"
)

# --------------------------------------------------------------------------- #
# Environment patches for tasks whose own reference solution fails
# --------------------------------------------------------------------------- #
# Something outside these tasks rotted after TB-2.1 was cut, so their reference
# solution scores 0 on the published image. Each patch repairs the environment only
# (package sources, a version pin); no instruction, solution or test assertion
# changes. Each is based on an open upstream fix in harbor-framework/terminal-bench-2;
# where it departs from that fix, the entry and its ``upstream`` field say how.
# Tasks not listed here are untouched. Two kinds of patch:
#   "test_sh"      inserted at the top of the verifier, after the shebang. The
#                  rollouter writes metadata.tmax.test_sh to /tests/test.sh verbatim
#                  (grading.grade_tmax), so the row is the only place it can go.
#   "image_layer"  Dockerfile lines on top of the task image. A plain build gives
#                  the row ``dockerfile = FROM <image> + layer``, which the sandbox
#                  builds in place of pulling ``image`` (harness/sandbox/daytona.py).
#                  --verify-runtime replaces that with FROM <digest pin> + layer +
#                  the tmux layer.
# Both kinds apply to plain builds and to rows read back with --input-jsonl.
# build-pov-ray is not listed. povray.org blocked the download for a period
# (upstream issue #76). Fetching it is part of what the agent is asked to do, and it
# is reachable from Daytona again as of 2026-10-16, so it is left unpatched.
_ENV_PATCH_BEGIN = (
    "# --- injected by prepare_tb2_1_data.py: TB-2.1 environment patch ------------\n"
)
_ENV_PATCH_END = (
    "# --- end injected block -----------------------------------------------------\n"
)

# Debian 11 left LTS on 2026-08-31. Around 09-07 bullseye-security's Release file
# expired and its pool started returning 404, so `apt-get update && apt-get install`
# fails on both qemu images. Dropping that suite and pointing the rest at
# archive.debian.org follows terminal-bench-2 PR #82, which does this in both
# tests/test.sh and environment/Dockerfile.
_BULLSEYE_TO_ARCHIVE = (
    "sed -i -e '/bullseye-security/d' "
    "-e 's|http://deb.debian.org/debian|http://archive.debian.org/debian|g' "
    "/etc/apt/sources.list"
)
_BULLSEYE_PATCH = {
    "upstream": "harbor-framework/terminal-bench-2#82",
    "test_sh": (
        "# Debian 11 is EOL: drop the expired bullseye-security suite and use\n"
        "# archive.debian.org so the verifier's apt-get works (terminal-bench-2 PR #82).\n"
        f"{_BULLSEYE_TO_ARCHIVE}\n"
    ),
    "image_layer": f"RUN {_BULLSEYE_TO_ARCHIVE}\n",
}

_ENV_PATCHES: dict[str, dict[str, str]] = {
    "qemu-startup": _BULLSEYE_PATCH,
    "qemu-alpine-ssh": _BULLSEYE_PATCH,
    # pyknotid 0.5.3 declares an unpinned `planarity`. PyPI's planarity 1.0.0
    # (2026-06-29) is a different project that took over the name and renamed the
    # node attributes pyknotid reads (data['pos'] -> 'vertex_position'), so the
    # upstream suite this verifier runs fails even for the reference solution.
    # terminal-bench-2 PR #73 pins planarity 0.6 in solve.sh. Here the same pin is
    # applied in the verifier instead, so it also covers agent builds. It applies
    # only when 1.x is installed and pyknotid's representation.py reads data['pos']
    # without handling 'vertex_position', so a build adapted to 1.x keeps it. The
    # sdist is fetched from its content-addressed PyPI URL and checked against its
    # sha256, then installed from the local file: the name now belongs to another
    # project, so neither a different upload nor a wheel can be picked up. A failed
    # install leaves grading unchanged but prints TB21_ENV_PATCH_FAILED and writes
    # /logs/verifier/tb21_env_patch_failed, so the 0 is not read as the agent's.
    "build-cython-ext": {
        "upstream": (
            "harbor-framework/terminal-bench-2#73 (its solve.sh planarity pin, "
            "applied in the verifier)"
        ),
        "test_sh": r"""# planarity 1.0.0 is a different project under the old PyPI name; restore the
# 0.6 that pyknotid 0.5.3 was written against (terminal-bench-2 PR #73's pin).
_tb_rep=/app/pyknotid/pyknotid/representations/representation.py
if python -c 'import importlib.metadata as m, sys; sys.exit(int(m.version("planarity").split(".")[0] == "0"))' 2>/dev/null \
    && grep -qs "data\['pos'\]" "$_tb_rep" && ! grep -qs vertex_position "$_tb_rep"; then
  python - <<'TB21_PIN' || { echo "TB21_ENV_PATCH_FAILED: build-cython-ext could not install planarity==0.6; a 0 below reflects the environment, not the agent" >&2; mkdir -p /logs/verifier && echo "planarity==0.6 install failed" > /logs/verifier/tb21_env_patch_failed; }
import hashlib, os, subprocess, sys, tempfile, urllib.request
url = ("https://files.pythonhosted.org/packages/5b/17/"
       "9a822cf44b9cad016def3085013edd2b2d7e7d6347778c00f4a835e33cc9/planarity-0.6.tar.gz")
sha = "9852691d9c0d05e26a2fcbf2fce5a2632e8847ec462e816fa02e72d35013da68"
path = os.path.join(tempfile.mkdtemp(), "planarity-0.6.tar.gz")
urllib.request.urlretrieve(url, path)
if hashlib.sha256(open(path, "rb").read()).hexdigest() != sha:
    sys.exit("planarity-0.6.tar.gz: sha256 mismatch")
sys.exit(subprocess.call([sys.executable, "-m", "pip", "install", "--no-deps", path]))
TB21_PIN
fi
""",
    },
    # The reference solution pins Rcpp, RcppEigen, BH, StanHeaders 2.32.10 and
    # rstan 2.32.7 with remotes::install_version, which in a non-interactive R
    # upgrades every dependency that is already installed. Two releases made after
    # the image was built break that:
    #   RcppParallel 6.x (from 2026-07-23) needs cmake, which the image lacks, so
    #     the rstan chain fails to build.
    #   StanHeaders 2.39.1 (2026-09-02) replaces the pinned 2.32.10 when rstan is
    #     installed. rstan compiles models with StanHeaders' stanc.js, and stanc
    #     2.39 rejects the old array syntax that stanc 2.32 accepted.
    # With cmake alone (terminal-bench-2 PR #72), rstan installs but the model
    # no longer parses. The fix restores the image-era resolution instead:
    #   - preinstall RcppParallel 5.1.11-1, the release terminal-bench-2 PR #73
    #     pins in solve.sh (newest on the image date, needs no cmake);
    #   - set R_REMOTES_UPGRADE=never in R's Renviron.site, so install_version
    #     installs missing dependencies but no longer upgrades installed ones.
    #     This part is this patch's own; no upstream fix has it.
    # rstan itself, the package step 1 of the task asks for, is not preinstalled.
    # This must be in the image because the verifier runs after the install.
    "mcmc-sampling-stan": {
        "upstream": (
            "harbor-framework/terminal-bench-2#73 (its RcppParallel pin, as an image "
            "layer) + R_REMOTES_UPGRADE=never (added here)"
        ),
        "image_layer": (
            "RUN cd /tmp && curl -fsSL -o RcppParallel_5.1.11-1.tar.gz "
            "https://cran.r-project.org/src/contrib/Archive/RcppParallel/"
            "RcppParallel_5.1.11-1.tar.gz "
            "&& echo 'e18c83548212cbd52f6629131564a392b870399343a5867d512dbca2a27a6362"
            "  RcppParallel_5.1.11-1.tar.gz' | sha256sum -c - "
            "&& R CMD INSTALL RcppParallel_5.1.11-1.tar.gz "
            "&& rm RcppParallel_5.1.11-1.tar.gz "
            "&& Rscript -e \"stopifnot(packageVersion('RcppParallel') == '5.1.11.1')\"\n"
            "RUN echo 'R_REMOTES_UPGRADE=never' >> \"$(R RHOME)/etc/Renviron.site\" "
            '&& Rscript -e \'stopifnot(Sys.getenv("R_REMOTES_UPGRADE") == "never")\'\n'
        ),
    },
    # torch-pipeline-parallelism's verifier installs torch==2.7.0 from PyPI, which
    # on Linux x86_64 is the CUDA build: ~3 GB with its nvidia-* wheels, on a task
    # that declares gpus = 0. On its 1-vCPU sandbox, downloading that and importing
    # it in every spawned rank runs the reference solution past the declared 900 s
    # verifier budget (2026-10-19 oracle run: 3 of 4 tests done, exit 124).
    # terminal-bench-2 #80 points at the pattern sam-cell-seg and pytorch-model-cli
    # already use in their own verifiers: torch from PyTorch's CPU index
    # (--index https://download.pytorch.org/whl/cpu --index-strategy
    # unsafe-best-match). The same two settings are given here through uv's
    # environment (UV_INDEX / UV_INDEX_STRATEGY), so the verifier's uvx line, its
    # pins and its tests are unchanged: torch==2.7.0 resolves to 2.7.0+cpu, the
    # same release on the same CPU kernels. uv checks every wheel against the
    # sha256 the index lists. If the index cannot be reached the patch says so
    # (TB21_ENV_PATCH_FAILED, /logs/verifier/tb21_env_patch_failed) and leaves the
    # published behaviour in place.
    "torch-pipeline-parallelism": {
        "upstream": (
            "harbor-framework/terminal-bench-2#80 (torch from the PyTorch CPU index, "
            "as sam-cell-seg and pytorch-model-cli do; set through uv's environment)"
        ),
        "test_sh": r"""# torch==2.7.0 from PyTorch's CPU index (terminal-bench-2 #80): on PyPI it is the ~3 GB
# CUDA build, on a gpus = 0 task, and it runs this verifier past its 900 s budget.
if timeout 20 bash -c 'exec 3<>/dev/tcp/download.pytorch.org/443' 2>/dev/null; then
  export UV_INDEX="https://download.pytorch.org/whl/cpu"
  export UV_INDEX_STRATEGY=unsafe-best-match
else
  echo "TB21_ENV_PATCH_FAILED: torch-pipeline-parallelism could not reach the PyTorch CPU index; the verifier keeps the CUDA build and may run past its budget" >&2
  mkdir -p /logs/verifier && echo "PyTorch CPU index unreachable" > /logs/verifier/tb21_env_patch_failed
fi
""",
    },
}


# Terminal-Bench 4: patches to a separate verifier's tests/Dockerfile, keyed by the
# task's [task].name. They apply only where that Dockerfile is built: a rewritten
# package (``separate_verifier(build=True)``, pack_to_dataset). A benchmark row
# pulls the published verifier image, which was built when its dependencies
# still resolved, and is never patched. Each entry is (old, new, upstream);
# ``old`` must occur exactly once.
_VERIFIER_DOCKERFILE_PATCHES: dict[str, tuple[str, str, str]] = {
    # gnucleus-freecad-validator[render] pulls pyvista unpinned. pyvista 0.49.0
    # (2026-09-08) requires vtk>=9.3.1, and pip cannot replace the conda-installed
    # vtk 9.2.6 that freecad brings ("Cannot uninstall vtk 9.2.6 ... distutils
    # installed project"), so the image no longer builds. 0.48.x accepts
    # vtk>=9.2.2 and is what the published images (2026-08-26) hold.
    **{
        f"terminal-bench/{name}": (
            "'gnucleus-freecad-validator[render]==0.1.3'",
            "'gnucleus-freecad-validator[render]==0.1.3' 'pyvista<0.49'",
            "harbor-framework/terminal-bench#2002 (pins pyvista below 0.49 "
            "instead of installing it from conda-forge)",
        )
        for name in (
            "freecad-impeller",
            "freecad-platform-drawing",
            "freecad-spring-clip",
        )
    },
}


def _patch_verifier_dockerfile(cfg: dict, dockerfile: str) -> tuple[str, str | None]:
    """``dockerfile`` with this task's verifier patch applied, and its upstream."""
    patch = _VERIFIER_DOCKERFILE_PATCHES.get(_get(cfg, "task", "name") or "")
    if patch is None:
        return dockerfile, None
    old, new, upstream = patch
    if new in dockerfile:
        return dockerfile, upstream
    if dockerfile.count(old) != 1:
        raise ValueError(f"verifier patch for {_get(cfg, 'task', 'name')}: {old!r} not found once")
    return dockerfile.replace(old, new), upstream


def _after_shebang(test_sh: str, block: str) -> str:
    """Insert ``block`` after ``test_sh``'s shebang line (or at the top if none)."""
    lines = test_sh.split("\n")
    if lines and lines[0].startswith("#!"):
        return lines[0] + "\n" + block + "\n".join(lines[1:])
    return block + test_sh


def apply_env_patches(row: dict) -> dict:
    """Apply this task's ``_ENV_PATCHES`` entry to ``row`` in place.

    ``tb2_1_resources.RESOURCE_OVERRIDES`` is applied here too and recorded in
    ``metadata.resource_patch``. This is a no-op for any task without an entry in
    either table. It is idempotent: a ``test_sh``
    that already carries the injected block is left alone. The patched row also
    records its upstream source in ``metadata.env_patch``.
    """
    md = row["metadata"]
    resources = RESOURCE_OVERRIDES.get(md.get("instance_id"))
    if resources is not None:
        md.update(resources)
        md["resource_patch"] = dict(resources)
    patch = _ENV_PATCHES.get(md.get("instance_id"))
    if patch is None:
        return row
    tmax = md["tmax"]
    if "image_layer" in patch and (
        "dockerfile" not in md or md["dockerfile"] == _plain_dockerfile(md)
    ):
        md["dockerfile"] = _patch_dockerfile(md)
    if "test_sh" in patch and _ENV_PATCH_BEGIN not in tmax["test_sh"]:
        tmax["test_sh"] = _after_shebang(
            tmax["test_sh"], _ENV_PATCH_BEGIN + patch["test_sh"] + _ENV_PATCH_END
        )
    md["env_patch"] = patch["upstream"]
    return row


def _image_layer(task_id: str) -> str:
    """Dockerfile lines this task's environment patch adds to the derived image."""
    return _ENV_PATCHES.get(task_id, {}).get("image_layer", "")


def _patch_dockerfile(md: dict, *, with_layer: bool = True) -> str:
    """The Dockerfile a plain build gives a row that needs one: ``USER root`` for
    an image that ends on the agent's user (``agent_user``), then the env patch's
    image layer (``with_layer``)."""
    root = _AS_ROOT if md.get("agent_user") else ""
    layer = _image_layer(md["instance_id"]) if with_layer else ""
    return f"FROM {md['image']}\n" + root + layer


def _plain_dockerfile(md: dict) -> str:
    """``_patch_dockerfile`` for the row as it stands: the layer only once its env
    patch is applied."""
    return _patch_dockerfile(md, with_layer=bool(md.get("env_patch")))


def _has_own_dockerfile(md: dict) -> bool:
    """True when the row carries a Dockerfile other than the one a plain build
    derives (the env-patch layer, ``USER root`` for an agent_user row)."""
    if "dockerfile" not in md:
        return False
    if not (_image_layer(md["instance_id"]) or md.get("agent_user")):
        return True
    return md["dockerfile"] not in (
        _patch_dockerfile(md),
        _patch_dockerfile(md, with_layer=False),
    )


def apply_root_layer(row: dict) -> dict:
    """Give an agent_user row a ``FROM <image>`` + ``USER root`` Dockerfile when it
    has none, so the harness keeps root with or without --verify-runtime (whose
    tmux layer starts with the same line). In place; a no-op for other rows."""
    md = row["metadata"]
    if md.get("agent_user") and "dockerfile" not in md:
        md["dockerfile"] = _plain_dockerfile(md)
    return row


async def verify_runtime(
    rows: list[dict], evidence_dir: str, *, keep_going: bool = False
) -> dict[str, str]:
    """Build and check two fresh sandboxes per task; resume from exact-row evidence.

    Raises on the first task that fails, unless ``keep_going``: then every task
    is checked and the failures come back as ``{task_id: error}``."""
    from torchtitan.experiments.rl.examples.tmax.evolution.resolve_base_images import (
        head_digest,
        MEDIA,
        split,
        token,
    )
    from torchtitan.experiments.rl.harness.agents.claude_code import boot_agent_sandbox

    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    checkout = Path(__file__).resolve().parents[5]
    provenance = {
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
        ).strip(),
        "dependency_lock_sha256": hashlib.sha256(
            (
                checkout / "torchtitan/experiments/rl/examples/tmax/runbook/uv.lock"
            ).read_bytes()
        ).hexdigest(),
        "probe_command": _TMUX_PROBE,
    }
    failed: dict[str, str] = {}
    with (root / "progress.jsonl").open("a") as log:

        def event(task: str, state: str, **extra) -> None:
            record = dict(
                time=datetime.now(timezone.utc).isoformat(),
                task=task,
                state=state,
                **extra,
            )
            line = json.dumps(record)
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        for row in rows:
            md = row["metadata"]
            tid = md["instance_id"]
            event(tid, "prepare", source_image=md["image"], **provenance)
            # Pin each source before building so a resumed run cannot switch bases.
            # A plain env-patch Dockerfile is rebuilt here on the pinned base.
            if not _has_own_dockerfile(md):
                source_image = md["image"]
                pin_file = root / (
                    hashlib.sha256(source_image.encode()).hexdigest() + ".pin"
                )
                if pin_file.exists():
                    pinned_image = pin_file.read_text()
                elif "@sha256:" in source_image:
                    pinned_image = source_image
                else:
                    registry, repo, tag = split(source_image.removeprefix("docker.io/"))
                    auth = await asyncio.to_thread(token, registry, repo)
                    headers = {"Accept": MEDIA, "Authorization": f"Bearer {auth}"}
                    digest = await asyncio.to_thread(
                        head_digest,
                        f"https://{registry}/v2/{repo}/manifests/{tag}",
                        headers,
                    )
                    if not digest:
                        raise RuntimeError(f"Cannot pin source image {source_image}")
                    pinned_image = f"{registry}/{repo}@{digest}"
                if not pin_file.exists():
                    pending_pin = pin_file.with_suffix(".incoming")
                    pending_pin.write_text(pinned_image)
                    pending_pin.replace(pin_file)
                # The layer goes only on rows that carry the patch, so
                # --no-env-patches yields the published environment.
                layer = _image_layer(tid) if md.get("env_patch") else ""
                # An agent_user image is root before the layer's own RUN lines too.
                as_root = _AS_ROOT if layer and md.get("agent_user") else ""
                md["dockerfile"] = (
                    f"FROM {pinned_image}\n" + as_root + layer + _TMUX_INSTALL
                )
            digest = hashlib.sha256(
                json.dumps(row, sort_keys=True).encode()
            ).hexdigest()
            evidence = root / f"{digest}.json"
            if evidence.exists():
                saved = json.loads(evidence.read_text())
                if saved.get("row") == row and saved.get("status") == "pass":
                    event(tid, "skip", evidence=str(evidence))
                    continue
                raise ValueError(f"Invalid runtime evidence: {evidence}")
            event(tid, "start", row_sha256=digest)
            probes = []
            try:
                for attempt in range(2):
                    async with boot_agent_sandbox(
                        md["image"],
                        dockerfile=md["dockerfile"],
                        install_claude=False,
                        cpu=md.get("daytona_cpu"),
                        memory=md.get("daytona_mem_gb"),
                        disk_gb=md.get("daytona_disk_gb"),
                    ) as sandbox:
                        code, stdout, stderr = await sandbox.exec(
                            _TMUX_PROBE, user="root", timeout=60, check=False
                        )
                        probes.append(
                            dict(
                                attempt=attempt,
                                sandbox_id=sandbox.sandbox_id,
                                code=code,
                                stdout=stdout,
                                stderr=stderr,
                            )
                        )
                        if code:
                            raise RuntimeError(
                                f"tmux probe failed: {code}: {stdout} {stderr}"
                            )
                write_json_atomic(
                    evidence,
                    dict(
                        status="pass",
                        row=row,
                        probes=probes,
                        **provenance,
                        time=datetime.now(timezone.utc).isoformat(),
                    ),
                )
                event(tid, "pass", evidence=str(evidence), probes=probes)
            except Exception as error:
                event(tid, "fail", error=str(error), row=row, probes=probes)
                if not keep_going:
                    raise
                failed[tid] = str(error)
    return failed


# Suffix carrying a base64-encoded binary grading fixture (see _BINARY_DECODE_PREAMBLE).
_B64_SUFFIX = ".b64"

# Prepended to test.sh ONLY for tasks that ship a non-UTF-8 grading fixture. Restores
# each ``/tests/<name>.b64`` to ``/tests/<name>`` before the upstream verifier body
# runs (train-fasttext, for one, untars /tests/private_test.tar.gz in its first lines).
# Decodes via base64(1), falling back to python3, and writes through a temp file so a
# partial decode never leaves a truncated fixture in place.
_BINARY_DECODE_PREAMBLE = """
# --- injected by prepare_tb2_1_data.py: restore binary grading fixtures ---------
# The tmax fixture channel is a str->str map, so non-UTF-8 verifier inputs (images,
# .pt tensors, tarballs) travel as base64 text at <path>.b64. Restore them here.
find /tests -type f -name '*.b64' -print0 2>/dev/null | while IFS= read -r -d '' _tb_enc; do
  _tb_out="${_tb_enc%.b64}"
  if base64 -d "$_tb_enc" > "$_tb_out.tmp" 2>/dev/null; then
    mv -f "$_tb_out.tmp" "$_tb_out" && rm -f "$_tb_enc"
  elif command -v python3 >/dev/null 2>&1 && python3 -c 'import base64,sys
open(sys.argv[2],"wb").write(base64.b64decode(open(sys.argv[1],"rb").read()))' "$_tb_enc" "$_tb_out.tmp"; then
    mv -f "$_tb_out.tmp" "$_tb_out" && rm -f "$_tb_enc"
  else
    rm -f "$_tb_out.tmp"
    echo "WARNING: could not decode $_tb_enc" >&2
  fi
done
# --- end injected block --------------------------------------------------------
"""


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def _download() -> str:
    """Download the full TB-2.1 tree from the HF mirror; return the snapshot dir."""
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=_HF_REPO, repo_type="dataset")


def _is_task_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.exists(os.path.join(path, "task.toml"))


def _resolve_tasks_root(root: str) -> str:
    """Return the dir whose children are task dirs, accepting either published layout.

    TB-2.1 puts them under ``<root>/tasks``; TB-2.0 put them at ``<root>``. Passing a
    repo root, a snapshot dir, or the ``tasks/`` dir itself all work. Note that
    ``tasks/dataset.toml`` is a *file* named like a task, which is why the probe tests
    for a task.toml inside a directory rather than merely listing names.
    """
    for candidate in (os.path.join(root, _TASKS_SUBDIR), root):
        if not os.path.isdir(candidate):
            continue
        if any(_is_task_dir(os.path.join(candidate, e)) for e in os.listdir(candidate)):
            return candidate
    nested = os.path.join(root, _TASKS_SUBDIR)
    raise SystemExit(
        f"ERROR: no task dirs (a directory containing task.toml) under {root!r} "
        f"or {nested!r}; is this a Terminal-Bench task tree?"
    )


# --------------------------------------------------------------------------- #
# task.toml
# --------------------------------------------------------------------------- #
def _load_task_toml(task_dir: str) -> dict:
    """Parse a task.toml into a dict (empty when absent)."""
    path = os.path.join(task_dir, "task.toml")
    if not os.path.exists(path):
        return {}
    import tomllib

    with open(path, "rb") as f:
        return tomllib.load(f)


def _get(cfg: dict, *keys):
    """``cfg["a"]["b"]`` with a None for any missing/non-dict level."""
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _positive_number(val) -> float | None:
    """Coerce a declared timeout to a positive float, else None (use the default)."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val) if val > 0 else None


def _size_to_gb(mb_value, size_value) -> int | None:
    """GiB (rounded up) from either a megabyte number or a TB-2.0 size string.

    ``memory_mb = 8192`` -> 8. ``memory = "8G"`` -> 8, ``"512M"`` -> 1. Returns None
    when neither field states a usable positive size, so the caller can omit the key
    and let the TT_DAYTONA_* default apply.
    """
    if isinstance(mb_value, (int, float)) and not isinstance(mb_value, bool):
        if mb_value > 0:
            return math.ceil(float(mb_value) / 1024)
    if isinstance(size_value, (int, float)) and not isinstance(size_value, bool):
        # A bare number in the 2.0 field is already GiB.
        return math.ceil(float(size_value)) if size_value > 0 else None
    if isinstance(size_value, str):
        m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([KMGTkmgt])?[Bi]*\s*", size_value)
        if not m:
            return None
        val = float(m.group(1))
        mult = {"K": 1 / 1024**2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}.get(
            (m.group(2) or "G").upper(), 1.0
        )
        gb = val * mult
        return math.ceil(gb) if gb > 0 else None
    return None


def _daytona_resources(cfg: dict) -> dict[str, int]:
    """Map ``[environment] cpus/memory_mb/storage_mb`` -> daytona_* metadata fields.

    Clamped to the same floors ``prepare_rts_data`` applies, for one reason: an RL
    agent explores far more than the oracle solution the declaration was sized for.
    A field the task does not declare is omitted so the sandbox falls back to that
    field's ``TT_DAYTONA_*`` env default rather than to a guess.
    """
    env = cfg.get("environment") if isinstance(cfg.get("environment"), dict) else {}
    out: dict[str, int] = {}
    cpus = env.get("cpus")
    if isinstance(cpus, (int, float)) and not isinstance(cpus, bool) and cpus > 0:
        out["daytona_cpu"] = max(_DAYTONA_CPU_FLOOR, int(round(float(cpus))))
    # TB-2.1 (schema 1.1) states megabytes in `memory_mb`/`storage_mb`; TB-2.0
    # (schema 1.0) stated a size string in `memory`/`storage` (e.g. "2G", "512M").
    # Read both so a 2.0-style tree is sized from its own declaration rather than
    # silently falling back to the TT_DAYTONA_* defaults.
    for mb_key, size_key, floor, out_key in (
        ("memory_mb", "memory", _DAYTONA_MEM_GB_FLOOR, "daytona_mem_gb"),
        ("storage_mb", "storage", _DAYTONA_DISK_GB_FLOOR, "daytona_disk_gb"),
    ):
        gb = _size_to_gb(env.get(mb_key), env.get(size_key))
        if gb is not None:
            out[out_key] = max(floor, gb)
    return out


def _workdir_from_dockerfile(task_dir: str) -> str:
    """Read the last ``WORKDIR`` from the task's environment Dockerfile.

    The published image lands the agent in its final WORKDIR; our harness cd's there
    per bash command, so it must match. Falls back to /app when absent.
    """
    dockerfile = os.path.join(task_dir, "environment", "Dockerfile")
    if not os.path.exists(dockerfile):
        return _DEFAULT_WORKDIR
    workdir = _DEFAULT_WORKDIR
    with open(dockerfile, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"\s*WORKDIR\s+(\S+)", line)
            if m:
                workdir = m.group(1)
    return workdir


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _collect_fixtures(
    task_dir: str, *, include_binary: bool = True
) -> tuple[dict[str, str], list[str]]:
    """Gather ``{relpath: content}`` for every file under ``tests/`` except ``test.sh``.

    Relpaths are relative to the task dir (``tests/test_outputs.py``,
    ``tests/test/01-factorial.scm``); grading.py maps ``tests/*`` -> ``/tests/*``,
    preserving subdirectories.

    Files that are not valid UTF-8 are base64-encoded under ``<relpath>.b64`` and
    their original relpath is returned in the second element, so the caller knows to
    prepend the decode preamble. This is the fix for the seven TB-2.1 verifiers that
    read a binary out of /tests -- the 2.0 script dropped those files outright.
    """
    fixtures: dict[str, str] = {}
    encoded: list[str] = []
    base = os.path.join(task_dir, _FIXTURE_ROOT)
    if not os.path.isdir(base):
        return fixtures, encoded
    for dirpath, _dirs, files in sorted(os.walk(base)):
        for fn in sorted(files):
            abspath = os.path.join(dirpath, fn)
            rel = os.path.relpath(abspath, task_dir)
            if rel == os.path.join(_FIXTURE_ROOT, "test.sh"):
                continue  # uploaded explicitly by grading.py, never as a fixture
            try:
                # newline="" disables universal-newline translation. Without it a
                # CRLF fixture silently loses every \r on the way into the JSONL --
                # sparql-university's university_graph_test.ttl is 310 bytes shorter
                # that way. Graded inputs must reach /tests byte-identical.
                with open(abspath, encoding="utf-8", newline="") as f:
                    fixtures[rel] = f.read()
                continue
            except UnicodeDecodeError:
                pass
            except OSError as e:
                print(f"WARNING: unreadable fixture {rel}: {e}", file=sys.stderr)
                continue
            if not include_binary:
                continue
            with open(abspath, "rb") as fb:
                fixtures[rel + _B64_SUFFIX] = base64.b64encode(fb.read()).decode()
            encoded.append(rel)
    return fixtures, encoded


def _wrap_test_sh(test_sh: str) -> str:
    """Insert the base64-decode preamble into ``test_sh``, after any shebang.

    grading.py invokes ``bash /tests/test.sh`` explicitly, so the shebang is inert --
    it is preserved anyway so the emitted script still reads like the upstream one.
    """
    return _after_shebang(test_sh, _BINARY_DECODE_PREAMBLE)


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #
def _tb_version(cfg: dict) -> str:
    """Benchmark version implied by the task schema: 1.1 -> 2.1, 1.0 -> 2.0."""
    schema = str(cfg.get("schema_version") or cfg.get("version") or "").strip()
    return {"1.1": "2.1", "1.0": "2.0"}.get(schema, schema or "unknown")


def separate_verifier(
    cfg: dict, task_dir: str, *, build: bool, gpu_target: str = _DEFAULT_GPU_TARGET
) -> tuple[dict | None, str]:
    """The ``tmax.separate_verifier`` block of a Harbor schema-1.1 task whose
    ``[verifier] environment_mode = "separate"`` (every TB4 task): the verifier
    runs in its own environment, which holds /tests, and sees only the declared
    ``artifacts``, copied from the agent's container. ``(None, "")`` for a task
    graded in its own container; ``(None, reason)`` for an unusable declaration.

    ``build=False`` names the published verifier image. ``build=True`` builds it
    from the package's ``tests/Dockerfile`` with ``tests/`` as the context, as a
    rewritten package must: a changed test is not in the published image."""
    if _get(cfg, "verifier", "environment_mode") != "separate":
        return None, ""
    verifier_env = _get(cfg, "verifier", "environment")
    if not isinstance(verifier_env, dict):
        # Harbor (resolve_effective_verifier_env_config): with no
        # [verifier.environment] the verifier runs in a deep copy of
        # [environment] -- its resources, GPU and internet setting (60 of the
        # 66 source-tree task.tomls). Not its docker_image: that is the agent's
        # image, which does not hold /tests; every release task.toml names its
        # verifier image in [verifier.environment].
        verifier_env = {
            k: v
            for k, v in copy.deepcopy(cfg.get("environment") or {}).items()
            if k != "docker_image"
        }
    artifacts = cfg.get("artifacts")
    if not artifacts:
        return None, "separate verifier without artifacts"
    sv: dict = {
        "artifacts": list(artifacts),
        **_daytona_resources({"environment": verifier_env}),
    }
    sv.update(_verifier_fields(cfg, verifier_env, gpu_target))
    if build:
        tests = Path(task_dir) / _FIXTURE_ROOT
        if not (tests / "Dockerfile").is_file():
            return None, "separate verifier without tests/Dockerfile"
        sv["dockerfile"], patched = _patch_verifier_dockerfile(
            cfg, (tests / "Dockerfile").read_text(encoding="utf-8")
        )
        if patched:
            sv["env_patch"] = patched
        sv["build_context"] = {
            str(f.relative_to(tests)): base64.b64encode(f.read_bytes()).decode()
            for f in sorted(tests.rglob("*"))
            if f.is_file()
        }
    elif verifier_env.get("docker_image"):
        sv["image"] = verifier_env["docker_image"]
    else:
        return None, "separate verifier without [verifier.environment].docker_image"
    return sv, ""


def _gpu_fields(env: dict, gpu_target: str) -> dict:
    """``daytona_gpu`` / ``daytona_gpu_type`` / ``daytona_target`` for an
    environment that declares ``gpus``; {} for one that does not."""
    gpus = env.get("gpus")
    if isinstance(gpus, bool) or not isinstance(gpus, int) or gpus <= 0:
        return {}
    out: dict = {"daytona_gpu": gpus}
    if env.get("gpu_types"):
        out["daytona_gpu_type"] = [str(t) for t in env["gpu_types"]]
    out["daytona_target"] = gpu_target
    return out


def _verifier_fields(
    cfg: dict, verifier_env: dict, gpu_target: str = _DEFAULT_GPU_TARGET
) -> dict:
    """What a separate verifier needs beyond its image, resources and artifacts,
    each only when the task declares it: ``collect`` hooks, ``env``
    ([verifier.env]), ``network_block_all`` (allow_internet = false) and its GPU.
    ``convention_dir`` is Harbor's, collected for every task."""
    out: dict = {"convention_dir": _CONVENTION_ARTIFACTS_DIR}
    hooks = _get(cfg, "verifier", "collect") or []
    if hooks:
        out["collect"] = [{**_COLLECT_DEFAULTS, **dict(h)} for h in hooks]
    env = _get(cfg, "verifier", "env") or {}
    if env:
        out["env"] = {str(k): str(v) for k, v in env.items()}
    if verifier_env.get("allow_internet") is False:
        out["network_block_all"] = True
    out.update(_gpu_fields(verifier_env, gpu_target))
    return out


_DOCKER_HEREDOC = re.compile(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?")
_ROOT_USERS = ("", "root", "0", "0:0", "root:root")


def _image_user(task_dir: str) -> str:
    """The user ``environment/Dockerfile``'s final stage runs as: its last USER,
    else the stage it builds FROM; "" when none says (the bases TB4 uses are
    root). Heredoc bodies (``RUN <<EOF``) are skipped, not parsed."""
    path = os.path.join(task_dir, "environment", "Dockerfile")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = re.sub(r"\\\r?\n", " ", f.read()).splitlines()
    stages: list[dict] = []
    pending: list[str] = []
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        m = re.match(r"\s*([A-Za-z]+)\s+(.*)", line)
        if not m:
            continue
        key, args = m.group(1).upper(), m.group(2).strip()
        pending = _DOCKER_HEREDOC.findall(args)
        if key == "FROM":
            words = [w for w in args.split() if not w.startswith("--")]
            alias = words[2].lower() if len(words) > 2 and words[1].upper() == "AS" else ""
            stages.append({"base": words[0].lower(), "alias": alias, "user": ""})
        elif key == "USER" and stages:
            stages[-1]["user"] = args.split()[0]
    index = len(stages) - 1
    while index >= 0:
        if stages[index]["user"]:
            return stages[index]["user"]
        base = stages[index]["base"]
        index = next(
            (j for j in range(index - 1, -1, -1) if stages[j]["alias"] == base), -1
        )
    return ""


def _agent_user(cfg: dict, task_dir: str) -> str:
    """Who the agent runs as: ``[agent].user``, else the image's own user, as in
    Harbor. "" for root. The image itself is left at root, by the tmux layer
    (``_TMUX_INSTALL``) or, in a plain build, by ``apply_root_layer``, so the
    harness keeps root and only the agent drops."""
    declared = _get(cfg, "agent", "user")
    user = str(declared) if declared is not None else _image_user(task_dir)
    if user in _ROOT_USERS:
        return ""
    name = user.split(":", 1)[0]
    if not name or name.isdigit():
        raise ValueError(f"agent user {user!r}: a numeric uid is not supported")
    return name


def _check_compose_sidecars(text: str) -> None:
    """Refuse a compose project with a sidecar built from a context.

    The row carries the compose file alone, so every service other than
    ``main`` must be an image, as in the TB4 release tree (where each is
    digest-pinned). ``main`` is built from the row's own Dockerfile. This module
    stays stdlib-only (the evolution loop loads it under plain python), so
    without PyYAML the check is skipped and such a project fails at boot."""
    try:
        import yaml
    except ImportError:
        return
    services = (yaml.safe_load(text) or {}).get("services") or {}
    built = sorted(
        name
        for name, svc in services.items()
        if name != "main" and isinstance(svc, dict) and "build" in svc
    )
    if built:
        raise ValueError(
            f"compose services {built} build from a context; only image services "
            "(the release tree's pinned images) are supported besides main"
        )


def _harbor_fields(cfg: dict, task_dir: str, gpu_target: str) -> dict:
    """Row fields for what a separate-verifier (TB4) task's Harbor environment
    declares beyond an image and its resources. Each is present only when the
    task declares it, so a plain TB4 row gains nothing here."""
    env = cfg.get("environment") if isinstance(cfg.get("environment"), dict) else {}
    out: dict = dict(_gpu_fields(env, gpu_target))
    compose_path = os.path.join(task_dir, "environment", "docker-compose.yaml")
    if os.path.exists(compose_path):
        with open(compose_path, encoding="utf-8", newline="") as f:
            text = f.read()
        _check_compose_sidecars(text)
        out["compose"] = {
            "docker_compose": text,
            "build_timeout_sec": _positive_number(env.get("build_timeout_sec"))
            or 600.0,
        }
    if env.get("healthcheck"):
        out["healthcheck"] = {**_HEALTHCHECK_DEFAULTS, **dict(env["healthcheck"])}
    user = _agent_user(cfg, task_dir)
    if user:
        out["agent_user"] = user
    return out


def _to_row(
    task_id: str,
    task_dir: str,
    *,
    image_prefix: str,
    include_binary: bool = True,
    source: str = _HF_REPO,
    gpu_target: str = _DEFAULT_GPU_TARGET,
) -> tuple[dict | None, str]:
    """Build one row from a TB-2.1 task dir, or ``(None, reason)`` when unusable."""
    instr_path = os.path.join(task_dir, "instruction.md")
    test_path = os.path.join(task_dir, _FIXTURE_ROOT, "test.sh")
    cfg = _load_task_toml(task_dir)
    image = _get(cfg, "environment", "docker_image")

    if not os.path.exists(instr_path):
        return None, "no instruction.md"
    if not os.path.exists(test_path):
        return None, "no tests/test.sh"
    if not image:
        return None, "no [environment].docker_image"
    # newline="" here too: the prompt and the verifier script ship verbatim.
    with open(instr_path, encoding="utf-8", newline="") as f:
        instruction = f.read()
    with open(test_path, encoding="utf-8", newline="") as f:
        test_sh = f.read()
    if not instruction.strip():
        return None, "empty instruction.md"
    if not test_sh.strip():
        return None, "empty tests/test.sh"

    if image_prefix and "/" in image and not image.startswith(image_prefix):
        image = image_prefix + image

    fixtures, encoded = _collect_fixtures(task_dir, include_binary=include_binary)
    if encoded:
        test_sh = _wrap_test_sh(test_sh)

    metadata = {
        "instance_id": task_id,
        "image": image,
        "workdir": _workdir_from_dockerfile(task_dir),
        "problem_statement": instruction,
        # Harbor states the agent budget per task, not per benchmark; the rollouter
        # falls back to its configured default when this is None.
        "agent_timeout_sec": _positive_number(_get(cfg, "agent", "timeout_sec")),
        # Read per task by the rollouter (_verifier_budget_sec), with
        # TMAX_EVAL_TIMEOUT_SEC as the floor. TB-2.1 declares 360s to 12000s.
        "verifier_timeout_sec": _positive_number(_get(cfg, "verifier", "timeout_sec")),
        # Provenance must describe what was actually read, not what this script is
        # named for: pointed at a 2.0-style tree it must not stamp rows as 2.1.
        "tb_version": _tb_version(cfg),
        "task_source": source,
        "tmax": {
            "test_sh": test_sh,
            "fixtures": fixtures,
            "reward_path": _REWARD_PATH,
        },
    }
    metadata.update(_daytona_resources(cfg))
    sv, why = separate_verifier(cfg, task_dir, build=False, gpu_target=gpu_target)
    if why:
        return None, why
    if sv is not None:
        # The verifier image owns /tests, so the grading fixtures are never uploaded.
        metadata["tmax"]["fixtures"] = {}
        metadata["tmax"]["separate_verifier"] = sv
        try:
            metadata.update(_harbor_fields(cfg, task_dir, gpu_target))
        except ValueError as e:
            return None, str(e)
    return {"prompt": instruction, "label": task_id, "metadata": metadata}, "ok"


def build_rows(
    *,
    tasks_root: str | None = None,
    limit: int | None = None,
    image_prefix: str = _DEFAULT_IMAGE_PREFIX,
    include_binary: bool = True,
    env_patches: bool = True,
    gpu_target: str = _DEFAULT_GPU_TARGET,
) -> tuple[list[dict], dict[str, str]]:
    """Convert every TB-2.1 task dir to a row. Returns ``(rows, {task_id: reason})``.

    ``tasks_root`` is a local checkout (repo root, ``tasks/`` dir, or a 2.0-style flat
    root); when None the HF mirror is downloaded. ``env_patches`` applies
    ``_ENV_PATCHES`` (a no-op for every task without an entry).
    """
    root = _resolve_tasks_root(tasks_root or _download())
    rows: list[dict] = []
    skipped: dict[str, str] = {}
    for entry in sorted(os.listdir(root)):
        task_dir = os.path.join(root, entry)
        if not _is_task_dir(task_dir):
            continue
        row, reason = _to_row(
            entry,
            task_dir,
            image_prefix=image_prefix,
            include_binary=include_binary,
            source=tasks_root or _HF_REPO,
            gpu_target=gpu_target,
        )
        if row is None:
            skipped[entry] = reason
            continue
        row = apply_env_patches(row) if env_patches else row
        rows.append(apply_root_layer(row))
        if limit is not None and len(rows) >= limit:
            break
    return rows, skipped


def _write_jsonl(rows: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a Terminal-Bench 2.1 eval JSONL in the tmax schema."
    )
    ap.add_argument("--out", required=True, help="output tb2_1_eval.jsonl path")
    ap.add_argument(
        "--input-jsonl",
        help="preserve the existing eval rows (environment patches still apply unless "
        "--no-env-patches); requires --verify-runtime",
    )
    ap.add_argument(
        "--tasks-root",
        default=None,
        metavar="PATH",
        help="local Terminal-Bench 2.1 checkout (repo root or its tasks/ dir). "
        f"Default: download {_HF_REPO} from the Hub.",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="emit only the first N tasks (smoke); relaxes the task-count check",
    )
    ap.add_argument("--image-prefix", default=_DEFAULT_IMAGE_PREFIX)
    ap.add_argument(
        "--verify-runtime",
        metavar="EVIDENCE_DIR",
        help="preinstall tmux in cached derived images and check two fresh Daytona "
        "sandboxes per task before publishing the output; resumes from evidence",
    )
    ap.add_argument(
        "--keep-going",
        action="store_true",
        help="with --verify-runtime: check every task, leave the ones that fail out "
        "of the output and list them in <out>.failed.json",
    )
    ap.add_argument(
        "--no-binary-fixtures",
        action="store_true",
        help="drop non-UTF-8 grading fixtures instead of shipping them base64 "
        "(smaller JSONL, but the 7 tasks that read a binary out of /tests can then "
        "never score -- this is the TB-2.0 script's behaviour)",
    )
    ap.add_argument(
        "--no-env-patches",
        action="store_true",
        help="emit the published tree unchanged: skip the environment patches for the "
        "tasks whose reference solution fails on rotted external resources "
        "(see _ENV_PATCHES)",
    )
    ap.add_argument(
        "--gpu-target",
        default=_DEFAULT_GPU_TARGET,
        help="Daytona region for the sandboxes of a task that declares a GPU "
        "(TB4); written into those rows only",
    )
    ap.add_argument(
        "--expect-tasks",
        type=int,
        default=_EXPECTED_TASKS,
        help="fail unless exactly this many rows are produced (0 disables)",
    )
    args = ap.parse_args()

    if args.input_jsonl:
        if not args.verify_runtime or args.tasks_root or args.no_binary_fixtures:
            ap.error(
                "--input-jsonl requires --verify-runtime and cannot convert task fixtures"
            )
        with open(args.input_jsonl) as source:
            rows = [json.loads(line) for line in source if line.strip()]
        if any(_has_own_dockerfile(row["metadata"]) for row in rows):
            ap.error("--input-jsonl expects original image rows, without a Dockerfile")
        if args.limit:
            rows = rows[: args.limit]
        if not args.no_env_patches:
            rows = [apply_env_patches(row) for row in rows]
        rows = [apply_root_layer(row) for row in rows]
        skipped = {}
    else:
        rows, skipped = build_rows(
            tasks_root=args.tasks_root,
            limit=args.limit,
            image_prefix=args.image_prefix,
            include_binary=not args.no_binary_fixtures,
            env_patches=not args.no_env_patches,
            gpu_target=args.gpu_target,
        )
    if skipped:
        for tid, reason in sorted(skipped.items()):
            print(f"WARNING: skipped {tid}: {reason}", file=sys.stderr)
    if not rows:
        print("ERROR: produced 0 rows", file=sys.stderr)
        sys.exit(1)
    if args.limit is None and args.expect_tasks and len(rows) != args.expect_tasks:
        print(
            f"ERROR: produced {len(rows)} rows, expected {args.expect_tasks}. "
            "The published task tree changed shape -- check --tasks-root and the "
            "skip warnings above before using this file (pass --expect-tasks 0 to "
            "override).",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.verify_runtime:
        if Path(args.out).exists():
            raise SystemExit(f"Refusing to overwrite {args.out}; use a new output path")
        failed = asyncio.run(
            verify_runtime(rows, args.verify_runtime, keep_going=args.keep_going)
        )
        if failed:
            # Left out of the output, with their reasons beside it.
            rows = [r for r in rows if r["metadata"]["instance_id"] not in failed]
            write_json_atomic(Path(args.out + ".failed.json"), failed)
            for tid, why in sorted(failed.items()):
                print(f"WARNING: runtime check failed {tid}: {why[:300]}", file=sys.stderr)
    _write_jsonl(rows, args.out)
    print(f"wrote {len(rows)} tasks -> {args.out}")


if __name__ == "__main__":
    main()
