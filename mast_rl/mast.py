# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""MAST launching tools for multi-host, multi-generator Search-R1 RL training.

Ported from ``fbsource//fbcode/pytorch/torchtitan/fb/mast_rl/mast.py`` (pzhang's
multi-generator stack, D108818896/D108823536) and adapted to this OSS checkout:
the image namespace is ``monarch_conda`` (writable here), wandb logs to the
user's own entity, and ``run.sh`` (controller role only) brings up the
dense-retrieval server before training.

The AppDef is one MAST role per worker group -- ``trainer`` plus one
``generator``/``generator_<i>`` per generator replica -- and a single-host
``controller`` role that drives the training loop, attaching to the worker host
meshes via ``MASTJob.from_torchx``.
"""

from __future__ import annotations

import copy
import getpass
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import torchx.specs as specs
from monarch.tools import commands
from monarch.tools.components.meta import hyperactor as meta_hyperactor
from monarch.tools.config import Config as MonarchToolsConfig
from monarch.tools.config import Workspace
from monarch.tools.config.workspace import ACTIVE_CONDA_ENV
from torchx.specs import Workspace as TorchxWorkspace
from torchx.specs.fb.component_helpers import Packages, run_as, ttls
from torchx.specs.fb.named_resources import MAST_WHOLE_HOST_FEATURE

from torchtitan.config import Configurable
from torchtitan.experiments.rl.train import (
    _compute_generator_world_size,
    _compute_trainer_world_size,
)

if TYPE_CHECKING:
    from monarch.actor import HostMesh

# The detached node-side controller parses the training config via
# ``ConfigManager``, which imports ``mast_rl.config_registry`` for ``--module
# mast_rl`` configs. Put the repo root (this dir's parent) on sys.path so
# ``mast_rl`` resolves as a top-level package here too -- the same name MAST sees
# for the shipped dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


logger: logging.Logger = logging.getLogger(__name__)

# Attach retry budget for the controller. MAST reports no status for a window after
# scheduling a job, and the window grows with task-group count.
_ATTACH_TIMEOUT_SEC = 900.0
_ATTACH_RETRY_SEC = 15.0

# The number of GPUs on each host type. The string is the torchx named-resource
# alias passed straight into the mesh spec (see torchx/specs/fb/named_resources.py).
# grandteton/H200 are x86 with 8 GPUs/host; the Grace-Blackwell parts (gb200/gb300)
# are aarch64 with fewer, much-larger GPUs (gb300: 4x ~278GB) -- they require an
# aarch64 conda env (see build_conda_aarch64.sh), not the x86 rlmast pack.
_GPUS_PER_HOST_TYPE: dict[str, int] = {
    "grandteton": 8,
    "grandteton_80g_roce": 8,
    "grandteton_h200": 8,
    "gb200": 2,
    "gb300": 4,
    # Nebius GB300 (region ofn): the msl_infra_pytorch_dct dedicated flexpool.
    "nebius_gb300": 4,
}


def gpus_per_node(host_type: str) -> int:
    """GPUs on one host of ``host_type`` (raises for unknown types)."""
    try:
        return _GPUS_PER_HOST_TYPE[host_type]
    except KeyError:
        raise ValueError(
            f"unknown host_type {host_type!r}; known: {sorted(_GPUS_PER_HOST_TYPE)}. "
            "Add it to _GPUS_PER_HOST_TYPE in mast_rl/mast.py."
        ) from None


def sanitize_job_name(raw: str) -> str:
    """MAST job names must match ``[a-zA-Z0-9_-]+``."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw)


def generate_job_name(raw_prefix: str) -> str:
    """``<prefix>-<6-char-uuid>``, sanitized and capped at MAST's 76-char limit.

    A fresh uuid suffix per call so resubmits of the same prefix don't collide.
    """
    suffix = uuid.uuid4().hex[:6]
    prefix = sanitize_job_name(raw_prefix) if raw_prefix else "torchtitan-rl"
    return f"{prefix[:69]}-{suffix}"


def _default_job_name_prefix(script_args: tuple[str, ...]) -> str:
    """``torchtitan-rl-<config>`` derived from the forwarded ``--config``."""
    args = list(script_args)
    config_name = ""
    for i, arg in enumerate(args):
        if arg == "--config" and i + 1 < len(args):
            config_name = args[i + 1]
            break
        if arg.startswith("--config="):
            config_name = arg.split("=", 1)[1]
            break
    return f"torchtitan-rl-{config_name}" if config_name else "torchtitan-rl"


def _strip_submit_only_args(args: list[str]) -> list[str]:
    """Remove launcher options that are only meaningful on the submit host."""
    result: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == "--launcher.package-workspace":
            skip_value = True
            continue
        if arg == "--launcher.no-package-workspace" or arg.startswith(
            "--launcher.package-workspace="
        ):
            continue
        result.append(arg)
    return result


class AllRolesWorkspace(Workspace):
    """``Workspace`` that attaches itself to every role.

    Default ``Workspace.set_env_vars`` populates env vars on each role but only
    sets ``role.workspace`` on the first one, so multi-role apps don't trigger
    the ``caching_build_workspace_and_update_role`` flow for the trailing roles.
    We override to set ``role.workspace`` on all of them.
    """

    def __init__(self, dirs=None, env=None) -> None:
        super().__init__(dirs=dirs, env=env)
        self._workspace_out: str | None = None

    def merge(self, outdir) -> None:
        self._workspace_out = str(outdir)
        super().merge(outdir)

    def set_env_vars(self, appdef) -> None:
        super().set_env_vars(appdef)
        if self._workspace_out and self.dirs:
            ws = TorchxWorkspace.from_str(self._workspace_out)
            for role in appdef.roles:
                role.workspace = ws


def infer_nodes(world_size: int, gpus_per_node: int) -> int:
    """Hosts needed for a role's ``world_size`` under the full-host rule.

    A role that fits on one host uses one host; a larger role tiles whole hosts,
    so its world size must be a multiple of ``gpus_per_node``.
    """
    if world_size <= gpus_per_node:
        return 1
    if world_size % gpus_per_node != 0:
        raise ValueError(
            f"role world size {world_size} exceeds one host ({gpus_per_node} "
            "GPUs) but is not a multiple of it; multi-host roles must use whole "
            "hosts. Adjust the config's parallelism degrees."
        )
    return world_size // gpus_per_node


def generator_role_names(num_generators: int) -> list[str]:
    """MAST role name for each generator.

    A single generator keeps the unsuffixed ``"generator"`` role; multiple
    generators get ``"generator_0"``, ``"generator_1"``, ... -- one role each.
    """
    if num_generators == 1:
        return ["generator"]
    return [f"generator_{i}" for i in range(num_generators)]


def generators_per_host(generator_ws: int, node_gpus: int) -> int:
    """Generators packed onto one host. SWE_PACK_GENERATORS=1 puts
    ``node_gpus // generator_ws`` sub-host generators on each host (e.g. 8 DP-1
    engines) instead of one host per generator."""
    if os.environ.get("SWE_PACK_GENERATORS", "0") != "1" or generator_ws >= node_gpus:
        return 1
    if node_gpus % generator_ws:
        raise ValueError(
            f"SWE_PACK_GENERATORS needs generator world size ({generator_ws}) to "
            f"divide the host's {node_gpus} GPUs"
        )
    return node_gpus // generator_ws


def generator_host_role_names(num_generators: int, per_host: int) -> list[str]:
    """MAST role per generator host; generator ``i`` runs on role ``i // per_host``."""
    if per_host == 1:
        return generator_role_names(num_generators)
    return [f"generator_host_{j}" for j in range(-(-num_generators // per_host))]


def eval_generator_role_names(num_eval_generators: int) -> list[str]:
    """MAST role name for each validation-only generator.

    Same shape as :func:`generator_role_names`, on a separate ``eval_generator``
    prefix: these hosts serve the periodic validation pass and are never routed
    training rollouts. Empty when the run has no dedicated eval generator.
    """
    if num_eval_generators == 1:
        return ["eval_generator"]
    return [f"eval_generator_{i}" for i in range(num_eval_generators)]


def eval_generator_host_role_names(num_eval_generators: int, per_host: int) -> list[str]:
    """MAST role per eval-generator host; eval generator ``i`` runs on role ``i // per_host``."""
    if per_host == 1:
        return eval_generator_role_names(num_eval_generators)
    return [f"eval_generator_host_{j}" for j in range(-(-num_eval_generators // per_host))]


class MastLauncher(Configurable):
    """Submit + attach to a detached (multi-host) RL MAST job.

    ``MastLauncher`` is a :class:`~torchtitan.config.Configurable`; its nested
    ``Config`` carries only the launch/scheduler knobs. The *training* config
    (``RLTrainer.Config``) stays separate and is parsed by the controller from
    the forwarded ``--module``/``--config`` args.
    """

    @dataclass(kw_only=True, slots=True)
    class Config(Configurable.Config):
        # Job-name prefix; ``submit`` appends a fresh 6-char suffix per launch so
        # resubmits of the same prefix don't collide. Left empty, it defaults to
        # ``torchtitan-rl-<config>`` (the recipe name) at submit time.
        job_name_prefix: str = ""
        host_type: str = "grandteton_80g_roce"
        # Override the host's GPU count instead of deriving it from the host
        # type. Useful in multi-host testing (e.g. simulate a 4-GPU host).
        gpus_per_node: int = 0
        # Run the controller, trainer, and all generators on one host. The
        # launcher validates that their combined world size fits on that host;
        # main.py then uses the existing local GPU partitioning path.
        collocate_on_controller: bool = False
        # Run the trainer on the controller host while generators remain in
        # detached MAST roles. This avoids an otherwise idle controller GPU
        # host when the trainer itself fills one host.
        trainer_on_controller: bool = False
        # ``mast_conda`` replaces this with an ephemeral fbpkg of the conda-packed
        # env + workspace, published under this package's name. ``monarch_conda``
        # is a CAF namespace writable from this OSS checkout. The Manifold mount
        # fbpkgs are added separately via ``Packages`` in ``_build_appdef``.
        image: str = "monarch_conda:latest_conveyor_build"
        # Reuse ``image`` as an already packed environment instead of rebuilding
        # the active conda environment and workspace. This is useful for resuming
        # a checkpoint with the exact package that created it.
        package_workspace: bool = True
        # Monarch's host-mesh component turns on the TW TTLS sidecar. Nebius hosts
        # cannot start it ("Failed to start service to synchronize blocker
        # starts"), so the task dies before run.sh; Monarch's own TLS still works.
        enable_ttls: bool = True
        # Attach to worker hosts through addresses they publish on the mounted
        # bucket (publish_worker_addr.py) instead of the MAST API, which
        # off-network hosts (Nebius) cannot reach.
        attach_via_mount: bool = False
        hpc_identity: str = "pytorch_distributed"  # genai_llm_research-llama users grant expired 2026-10-19
        rm_attribution: str = "msl_infra_pytorch_dev"
        hpc_oncall: str = "meta_conda"
        # Job priority within the entitlement (critical/very_high/high/normal/low);
        # the entitlement clamps requests it can't grant. critical: long
        # VLLM_COMPILE startups kept getting preempted at lower priority.
        job_priority: str = "critical"
        # Retry a failed MAST task group. Training resumes from the latest
        # checkpoint in the shared dump folder when checkpointing is enabled.
        max_retries: int = 0
        region: str = "eag"
        hpc_cluster_uuid: str = "MastGenAICluster"
        manifold_bucket: str = "torchtrain_datasets"
        # wandb (OFFLINE on MAST) entity/project; the offline run is synced from
        # the devvm afterward. Defaults to the user's own entity.
        wandb_team: str = "yichuan"
        wandb_project: str = "torchtitan"

    def __init__(self, config: "MastLauncher.Config") -> None:
        self.config = config

    # ------------------------------------------------------------------ #
    # devserver: build + submit
    # ------------------------------------------------------------------ #
    def submit(self, cfg, script_args) -> str:
        """Infer host counts from ``cfg``, build the detached AppDef, submit.

        ``cfg`` is the already-parsed ``RLTrainer.Config`` (a ``MastRLConfig``) --
        its trainer/generator parallelism gives the per-role world sizes, while
        this launcher's ``self.config`` (== ``cfg.launcher``) gives the MAST knobs.
        ``script_args`` is the post-``--`` argv forwarded to the controller's
        ``main.py``. Returns the job handle (``mast_conda:///<job_name>``).
        """
        lc = self.config
        if lc.max_retries < 0:
            raise ValueError("launcher.max_retries must be non-negative")
        host_gpus = gpus_per_node(lc.host_type)
        node_gpus = lc.gpus_per_node or host_gpus
        if lc.gpus_per_node and lc.gpus_per_node != host_gpus:
            print(
                f"NOTE: launcher.gpus_per_node={node_gpus} overrides {lc.host_type}'s "
                f"{host_gpus} GPUs/host; each role uses {node_gpus} GPU(s)/host."
            )

        trainer_ws = _compute_trainer_world_size(cfg.trainer.parallelism)
        generator_ws = _compute_generator_world_size(cfg.generator.parallelism)
        eval_generator_ws = _compute_generator_world_size(
            cfg.eval_generator_parallelism()
        )
        num_generators = cfg.num_generators
        num_eval_generators = cfg.num_eval_generators
        trainer_nodes = infer_nodes(trainer_ws, node_gpus)
        gen_nodes_each = infer_nodes(generator_ws, node_gpus)
        gens_per_host = generators_per_host(generator_ws, node_gpus)
        eval_gens_per_host = generators_per_host(eval_generator_ws, node_gpus)
        if gens_per_host > 1:
            # trainer_on_controller still puts every generator on its own host
            # role, so packing composes with it; full collocation does not.
            if lc.collocate_on_controller:
                raise ValueError("SWE_PACK_GENERATORS needs detached generator hosts")
            print(
                f"packing {gens_per_host} generators x {generator_ws} GPUs per host: "
                f"{num_generators} generators -> "
                f"{len(generator_host_role_names(num_generators, gens_per_host))} host(s)"
            )
        eval_gen_nodes_each = infer_nodes(eval_generator_ws, node_gpus)
        if lc.collocate_on_controller and lc.trainer_on_controller:
            raise ValueError(
                "collocate_on_controller and trainer_on_controller are mutually exclusive"
            )
        if lc.collocate_on_controller:
            total_world_size = (
                trainer_ws
                + num_generators * generator_ws
                + num_eval_generators * eval_generator_ws
            )
            if (
                trainer_nodes != 1
                or gen_nodes_each != 1
                or eval_gen_nodes_each != 1
                or total_world_size > node_gpus
            ):
                raise ValueError(
                    "collocate_on_controller requires the trainer and every generator "
                    f"to fit on one {node_gpus}-GPU host, but trainer={trainer_ws}, "
                    f"{num_generators} generators x {generator_ws}, and "
                    f"{num_eval_generators} eval generators x "
                    f"{eval_generator_ws} require {total_world_size} GPUs"
                )
            print(
                f"collocating on one {node_gpus}-GPU host: trainer {trainer_ws} GPUs + "
                f"{num_generators} generators x {generator_ws} GPUs + "
                f"{num_eval_generators} eval generators x {eval_generator_ws} GPUs = "
                f"{total_world_size} GPUs"
            )
        elif lc.trainer_on_controller:
            if trainer_nodes != 1:
                raise ValueError(
                    "trainer_on_controller requires the trainer to fit on one "
                    f"{node_gpus}-GPU host, but trainer world size is {trainer_ws}"
                )
            print(
                f"placing trainer {trainer_ws} GPUs on the controller host; "
                f"{num_generators} generator role(s) x {generator_ws} GPUs use "
                f"{gen_nodes_each} host(s) each; {num_eval_generators} eval generator "
                f"role(s) x {eval_generator_ws} GPUs use "
                f"{eval_gen_nodes_each} host(s) each"
            )
        else:
            print(
                f"inferred from config ({node_gpus} GPUs/host): "
                f"trainer {trainer_ws} GPUs -> {trainer_nodes} host(s), "
                f"{num_generators} generator role(s) x {generator_ws} GPUs -> "
                f"{gen_nodes_each} host(s) each, {num_eval_generators} eval generator "
                f"role(s) x {eval_generator_ws} GPUs -> {eval_gen_nodes_each} host(s) "
                "+1 controller host"
            )

        prefix = lc.job_name_prefix or _default_job_name_prefix(script_args)
        job_name = generate_job_name(prefix)
        wandb_run_name = prefix
        appdef = self._build_appdef(
            job_name=job_name,
            trainer_nodes=trainer_nodes,
            num_generators=num_generators,
            num_eval_generators=num_eval_generators,
            gen_nodes_each=gen_nodes_each,
            eval_gen_nodes_each=eval_gen_nodes_each,
            wandb_run_name=wandb_run_name,
            gens_per_host=gens_per_host,
            eval_gens_per_host=eval_gens_per_host,
            collocate_on_controller=lc.collocate_on_controller,
            trainer_on_controller=lc.trainer_on_controller,
        )
        for role in appdef.roles:
            role.max_retries = lc.max_retries

        # Forward the user's controller args to the controller role's bash
        # ``"${@:2}"``. Drop the leading ``mast_rl/main.py`` (the controller's bash
        # already invokes ``python mast_rl/main.py``).
        controller_args = list(script_args)
        if controller_args and controller_args[0].endswith("main.py"):
            controller_args = controller_args[1:]
        controller_args = _strip_submit_only_args(controller_args)
        appdef.roles[0].args.extend(controller_args)

        # Ship only ``mast_rl/`` (run.sh + main.py + this module + configs +
        # retrieval_server.py); torchtitan rides in the conda-packed env.
        if lc.package_workspace:
            workspace = AllRolesWorkspace(
                dirs={Path(__file__).resolve().parent: "mast_rl"},
                env=ACTIVE_CONDA_ENV,
            )
        else:
            workspace = Workspace.null()
        tools_config = MonarchToolsConfig(
            scheduler="mast_conda",
            scheduler_args=self._scheduler_args(),
            appdef=appdef,
            workspace=workspace,
        )
        return commands.create(tools_config, name=job_name)

    def _scheduler_args(self) -> dict[str, object]:
        cfg = self.config
        scheduler_args = {
            "hpcIdentity": cfg.hpc_identity,
            "rmAttribution": cfg.rm_attribution,
            "hpcJobOncall": cfg.hpc_oncall,
            "hpcClusterUuid": cfg.hpc_cluster_uuid,
            "job_priority": cfg.job_priority,
        }
        # An empty region drops the constraint so MAST places the job in whichever
        # region actually stocks the requested machine type. Pinning the wrong region
        # fails at enqueue with NO_REGION_CAN_PLACE / MACHINE_TYPES_NOT_FOUND, and
        # the right region is not discoverable from the torchx named resource.
        if cfg.region:
            scheduler_args["localityConstraints"] = ["region", cfg.region]
        return scheduler_args

    def _build_appdef(
        self,
        *,
        job_name: str,
        trainer_nodes: int,
        num_generators: int,
        num_eval_generators: int,
        gen_nodes_each: int,
        eval_gen_nodes_each: int,
        wandb_run_name: str,
        gens_per_host: int = 1,
        eval_gens_per_host: int = 1,
        collocate_on_controller: bool = False,
        trainer_on_controller: bool = False,
    ) -> specs.AppDef:
        """Build a collocated or detached multi-role AppDef.

        In detached mode each role is its own MAST task group:
          - the trainer and each ``generator_<i>`` task group can span multiple
            hosts; the controller maps them to host meshes via
            ``MASTJob.from_torchx``.
          - the controller task group is a single host running ``main.py
            --job-name <job>`` to drive the training loop over those meshes. It
            also hosts the dense-retrieval server (see run.sh).
        """
        cfg = self.config
        env = {
            "PYTORCH_ALLOC_CONF": "expandable_segments:True",
            "WANDB_MODE": "offline",
            "WANDB_TEAM": cfg.wandb_team,
            "WANDB_PROJECT": cfg.wandb_project,
            "WANDB_RUN_NAME": wandb_run_name or job_name,
            "TORCH_CPP_LOG_LEVEL": "ERROR",
            "LOGLEVEL": "ERROR",
            "PYTHONFAULTHANDLER": "1",
            # MAST packs multiple trainer/generator actor processes per host. Pin
            # BLAS/OpenMP to one thread each so the per-rank CPU pools don't
            # oversubscribe the host during the CPU-staged weight sync (the slow
            # ~115s generator pull was BLAS oversubscription). fbcode env_defaults
            # parity for the SHM PUT + MonarchRDMA GET weight-sync path.
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            # Pin Monarch's actor message-dispatch to the current behavior (#3832):
            # Monarch is landing a breaking change to its dispatch default; the new
            # default would change/slow the controller<->actor RPCs. Keep "0" until
            # the @concurrent_endpoint API ships in a stable monarch release. Set in
            # the role env so it is in os.environ before monarch is imported.
            "MONARCH_ACTOR_QUEUE_DISPATCH": "0",
        }
        mesh_proc_spawn_max_idle = os.environ.get("MONARCH_MESH_PROC_SPAWN_MAX_IDLE")
        if mesh_proc_spawn_max_idle:
            env["MONARCH_MESH_PROC_SPAWN_MAX_IDLE"] = mesh_proc_spawn_max_idle
        mesh_orphan_timeout = os.environ.get("MONARCH_MESH_ORPHAN_TIMEOUT")
        if mesh_orphan_timeout:
            env["MONARCH_MESH_ORPHAN_TIMEOUT"] = mesh_orphan_timeout
        # Startup host-spawn-ready timeout (default ~30s): on a large multi-host job a
        # single slow host bootstrap trips "error spawning proc mesh: Timeout(30s)" and
        # kills the controller at startup. main.py reads this and raises it.
        host_spawn_ready_timeout = os.environ.get("MONARCH_HOST_SPAWN_READY_TIMEOUT")
        if host_spawn_ready_timeout:
            env["MONARCH_HOST_SPAWN_READY_TIMEOUT"] = host_spawn_ready_timeout
        # Message-delivery deadline: the timeout that actually fires on controller<->
        # generator channel congestion at high concurrency ("timed out reaching controller
        # for mesh ... Assuming proc is dead"). main.py reads this and raises it.
        message_delivery_timeout = os.environ.get("MONARCH_MESSAGE_DELIVERY_TIMEOUT")
        if message_delivery_timeout:
            env["MONARCH_MESSAGE_DELIVERY_TIMEOUT"] = message_delivery_timeout
        # Raise Monarch's supervision / state-query timeouts so a rollout_worker that
        # merely blocks on a slow-but-healthy Daytona op (cold sandbox build 20-40s,
        # image pull) is not mistaken for a crashed proc (-> global-fatal supervision
        # event). main.py reads these and calls configure_monarch(...).
        for _ll_var in ("LITELLM_LOG", "LITELLM_SUPPRESS_DEBUG_INFO"):
            _ll_val = os.environ.get(_ll_var)
            if _ll_val:
                env[_ll_var] = _ll_val
        for _mon_var in (
            "MONARCH_SUPERVISION_WATCHDOG_TIMEOUT",
            "MONARCH_GET_PROC_STATE_MAX_IDLE",
            "MONARCH_GET_ACTOR_STATE_MAX_IDLE",
        ):
            _mon_val = os.environ.get(_mon_var)
            if _mon_val:
                env[_mon_var] = _mon_val
        # Both the controller and the workers go through run.sh, which mounts the
        # Manifold bucket named by MANIFUSE_BUCKET.
        env["MANIFUSE_BUCKET"] = cfg.manifold_bucket
        # Without TTLS, fwdproxy:8080 is unreachable; run.sh relays to its mTLS
        # port instead (fwdproxy_tls_relay.py).
        if not cfg.enable_ttls:
            env["MAST_RL_FWDPROXY_RELAY"] = "1"
        if cfg.attach_via_mount:
            env["MAST_RL_HOSTS_DIR"] = (
                f"/mnt/{cfg.manifold_bucket}/tree/{getpass.getuser()}/mast_hosts/{job_name}"
            )

        # NCCL transport + torchcomms knobs, forwarded from the submit-side env so
        # a run can flip on RoCE/IB without an appdef edit:
        #   NCCL_TRANSPORT=IB bash mast_rl/submit_fsdp16.sh
        # run.sh reads NCCL_TRANSPORT to set NCCL_NET/NCCL_IB_DISABLE/GDR_LEVEL;
        # torchcomms reads USE_TORCHCOMMS[_RDMA]. Default Socket / off matches an
        # env built without the pinned rdma-core.
        env["NCCL_TRANSPORT"] = os.environ.get("NCCL_TRANSPORT", "Socket")
        env["USE_TORCHCOMMS"] = os.environ.get("USE_TORCHCOMMS", "0")
        env["USE_TORCHCOMMS_RDMA"] = os.environ.get("USE_TORCHCOMMS_RDMA", "0")
        # TorchStore resolves its transport lazily in every actor process. Forward
        # these before Python starts so a run can bypass Monarch RDMA and select
        # Gloo when the node-side RDMA manager is unavailable.
        env["TORCHSTORE_RDMA_ENABLED"] = os.environ.get("TORCHSTORE_RDMA_ENABLED", "1")
        env["TORCHSTORE_GLOO_ENABLED"] = os.environ.get("TORCHSTORE_GLOO_ENABLED", "1")

        # SWE-R2E coding-agent harness, forwarded from the submit env. SWE_RUN=1
        # selects run.sh's Daytona/Claude-Code controller branch (no retriever);
        # the rest configure it (Daytona key + egress proxy, sandbox quota, the
        # toolchain + R2E JSONL staged on the bucket, agent budget). Harmless on
        # worker roles -- they exit before the SWE branch and the proxy is applied
        # only inside that branch (controller), not here, so worker NCCL is unaffected.
        for key in (
            "SWE_RUN",
            # SWE_CURATE=1 makes run.sh run the pass-rate data-washing worker on
            # every host instead of the RL loop (see run.sh SWE_CURATE branch).
            "SWE_CURATE",
            "SWE_CURATE_K",
            "SWE_CURATE_MODEL",
            "SWE_CURATE_OUT",
            "SWE_CURATE_LIMIT",
            # Override the RL dump/checkpoint dir (default mast_runs/<run-name> is
            # shared across same-config jobs, so a new run would try to resume the
            # previous job's checkpoint). Set a fresh path to start from base.
            # SWE_DUMP_DIR is the SWE branch's knob; SEARCH_R1_DUMP_DIR is the
            # Search-R1 branch's (run.sh:437). Set the matching one for a fresh run.
            "SWE_DUMP_DIR",
            "SEARCH_R1_DUMP_DIR",
            # Evolution roots live on the shared bucket. The trainer writes
            # rollout records and signals under TRL_RUN_DIR while a separate
            # devvm-side loop updates TRL_BASE/data/mix/live.jsonl.
            "TRL_BASE",
            "TRL_RUN_DIR",
            # DAPO-Math runs use the generic RL controller without the Search-R1
            # retrieval server or the SWE/Daytona setup.
            "DAPO_MATH_RUN",
            "DAPO_DUMP_DIR",
            # One-host checkpoint staging mode. run.sh downloads the pinned HF
            # snapshot before training startup and copies it to the mounted bucket.
            "HF_STAGE_REPO",
            "HF_STAGE_REVISION",
            "HF_STAGE_DEST",
            "SWE_TP",
            "SWE_DP_SHARD",
            "SWE_GEN_DP",
            # SWE_DP_REPLICATE=2 composes with the trainer dp_shard into HSDP
            # (replicate x shard). Used for FSDP-16-as-HSDP (replicate 2 x shard 8, 2
            # hosts) to avoid the pure-dp_shard=16 cross-host all-gather hang.
            "SWE_DP_REPLICATE",
            # SWE_DP_WEIGHT_BROADCAST=1 -> one rank per generator DP group pulls its TP
            # shard from TorchStore and broadcasts to the DP replicas (D116462661),
            # cutting duplicated weight pulls. torchtitan_wrapper fill-in-place only.
            "SWE_DP_WEIGHT_BROADCAST",
            # SWE_AGGREGATE=1: shard 0 aggregates a finished wash on the mounted
            # bucket into the band JSONL (run.sh SWE_AGGREGATE branch).
            "SWE_AGGREGATE",
            "SWE_CURATE_RUN",
            "SWE_BAND_OUT",
            "SWE_BAND_MIN",
            "SWE_BAND_MAX",
            "SWE_BAND_MIN_SAMPLES",
            "DAYTONA_API_KEY",
            "SWE_PROXY",
            "SWE_PROMPT_DATA",
            "SWE_HOLDOUT_N",
            "SWE_ROLLOUT_RECORDS",
            # Overfit bench (overfit_bench/recipe.env).
            "SWE_BENCH_SCHEDULER",
            "SWE_BENCH_MAX_TURNS",
            "SWE_BENCH_SANDBOX_CPU",
            "SWE_BENCH_SANDBOX_MEM_GB",
            "SWE_BENCH_SANDBOX_DISK_GB",
            "SWE_BENCH_TURN_CAP_GUARD_SEC",
            "SWE_SKIP_ZERO_GRAD_STEP",
            # Train-only TMax curriculum whitelist. The path must be on the shared
            # mount because rollout workers construct their own dataset instances.
            "SWE_INCLUDE_PROMPTS",
            # Terminal-Bench 2.0 eval (rl_grpo_qwen3_5_9b_tmax_tb2_eval): the TB-2.0
            # JSONL and the trained DCP checkpoint dir to score. Read at config-module
            # import time, so they must reach the controller role env.
            "SWE_TB2_DATA",
            "SWE_TB2_CKPT",
            # Held-out validation sample count (tmax config). 0 skips pre/periodic
            # validation entirely (e.g. a pure step-time / speedup run).
            "SWE_VAL_SAMPLES",
            # Inline Terminal-Bench 2.0 validation for the TRAINING recipe: point the
            # periodic pass at the TB-2.0 JSONL instead of the tmax holdout slice, at
            # the Harbor Vanillux2Agent sampling defaults (k=5, temp 0.7, top_p 0.95)
            # so the curve is comparable to the published avg@5 / pass@5.
            "SWE_TB2_VAL_DATA",
            "SWE_TB2_VAL_K",
            "SWE_TB2_VAL_TEMPERATURE",
            "SWE_TB2_VAL_TOP_P",
            # Per-turn generation cap for the TB-2.0 pass only (0/unset = inherit the
            # training 16384). A probe for turns truncated inside <think> before they
            # emit a tool call.
            "SWE_TB2_VAL_MAX_TOKENS",
            # Eval-generator-only knobs and context lengths (config_registry /
            # controller read them at config build on the controller host).
            "SWE_EVAL_GPU_MEMORY_LIMIT",
            "SWE_EVAL_GEN_BACKEND",
            "SWE_EVAL_GEN_CUDAGRAPH_MODE",
            "SWE_EVAL_MAX_MODEL_LEN",
            "SWE_EVAL_MAX_CONTEXT_LEN",
            "SWE_EVAL_TIME_BUDGET_SEC",
            "SWE_EVAL_BUDGET_FLOOR_SEC",
            "SWE_TMAX_CONTEXT",
            "SWE_EVAL_MAX_TURNS",
            "SWE_EVAL_AGENT_BUDGET_CAP_SEC",
            "SWE_EVAL_VERIFIER_BUDGET_CAP_SEC",
            # Strict env rules (train: opening only by default; eval off).
            "SWE_STRICT_FORMAT",
            "SWE_STRICT_OPENING",
            "SWE_STRICT_TRUNCATION",
            "SWE_EVAL_STRICT_FORMAT",
            "SWE_EVAL_STRICT_OPENING",
            "SWE_EVAL_STRICT_TRUNCATION",
            "SWE_GEN_VLLM_CUSTOM_OPS",
            "SWE_VAL_INTERVAL",
            # SWE_CKPT_INTERVAL=N -> save a full training-state checkpoint every N
            # steps (recipe default 20). Lower it (e.g. 5) for crash tolerance when a
            # run keeps dying before the default interval and restarts from step 1.
            "SWE_CKPT_INTERVAL",
            "SWE_CKPT_CONTROLLER_DATA",
            "SWE_CKPT_FIRST_STEP",
            "SWE_CKPT_KEEP",
            "SWE_CKPT_KEEP_EVERY",
            "SWE_CKPT_ASYNC",
            "SWE_CKPT_FOLDER",
            # SWE_VAL_TRACES=0 turns off the per-pass browsable trace report
            # (<dump>/validation_traces/step-<N>/INDEX.md).
            "SWE_VAL_TRACES",
            # SWE_NUM_EVAL_GENERATORS=1 adds one generator host that serves ONLY the
            # periodic validation pass, which also switches the pass to async (training
            # continues through it). Read at config-module import time on BOTH the
            # submit side (it sizes the appdef) and the controller.
            "SWE_NUM_EVAL_GENERATORS",
            "SWE_EVAL_GEN_DP",
            "SWE_NUM_EVAL_ROLLOUT_WORKERS",
            "SWE_EVAL_ROLLOUT_CONCURRENCY",
            # Online task evolution (recursive task synthesis). When
            # SWE_TASK_EVOLUTION_DIR is set, the rollouter writes one signal per
            # zero-std group to that dir (on the shared mount) and run.sh brings up
            # the evolve_ondella loop on the controller, which re-tunes each signalled
            # task with the SYNTH_* model and folds the result back into SWE_PROMPT_DATA
            # for the dataset hot-reload. The dir must be on the shared mount so the
            # rollout workers (their own hosts) can write it. Unset = feature off.
            "SWE_TASK_EVOLUTION_DIR",
            # Read-only canonical seed copied once into the writable per-run
            # SWE_PROMPT_DATA at controller boot (a MAST auto-retry resumes onto the
            # already-evolved mix, so the copy is skipped when the mix exists). The
            # manifold path holding evolve_assets.tar.gz + tw_tasks.tar (scripts +
            # POOL_ROOTS), and the loop interval in seconds.
            "SWE_EVOLVE_SEED_DATA",
            "SWE_EVOLVE_ASSETS",
            "SWE_EVOLVE_INTERVAL",
            "SWE_EVOLVE_WORKERS",
            # =1 (default in run.sh): re-tune one hop from the task's current evolved
            # package instead of the original seed, so evolution compounds.
            "SWE_EVOLVE_COMPOUND",
            # SWE_DATA_HOT_RELOAD=1 turns on the dataset's mid-run reload;
            # SWE_MIX_VERSIONED=1 makes the evolve loop publish each mix update as a new
            # <stem>.v<N>.jsonl (cross-host visible) instead of rewriting the base file.
            "SWE_MIX_VERSIONED",
            # The evolve loop's model client (OpenAI-compatible). Reaches the endpoint
            # over fwdproxy from the controller. Unset when evolution is off.
            "OPENAI_API_KEY",
            "SYNTH_API_BASE",
            "SYNTH_MODEL",
            "SYNTH_EFFORT",
            # TRL_TT points pack_to_dataset at the torchtitan checkout so a folded
            # row matches a freshly prepared one (TRL_BASE is forwarded above).
            "TRL_TT",
            # Rollout-worker log level. Default INFO gives one line per rollout (its
            # stop reason); DEBUG adds the per-turn prompt/max_tokens/out/finish
            # records, which is how to tell whether turns hit the generation cap.
            "TT_ROLLOUT_LOG_LEVEL",
            # SWE_DROP_ZERO_STD=0 keeps all-fail/all-solve groups in the finalized
            # batch composition. The TMax-9B curriculum run uses this to preserve
            # the historical BS32/SPP8 no-drop schedule.
            "SWE_DROP_ZERO_STD",
            "SWE_HOST_NODE_TARBALL",
            "SWE_HOST_CC_TARBALL",
            "SWE_CLAUDE_EXTRA_ARGS",
            "SWE_TIME_BUDGET_SEC",
            "SWE_AGENT_TIMEOUT_FLOOR_SEC",
            "SWE_EVAL_TIMEOUT_SEC",
            "SWE_MAX_CONTEXT_LEN",
            "SWE_MAX_MODEL_LEN",
            "SWE_MAX_GEN_LEN",
            "SWE_BOOT_CONCURRENCY",
            "SWE_BOOT_RETRIES",
            "SWE_ROLLOUT_CONCURRENCY",
            "SWE_NUM_ROLLOUT_WORKERS",
            "SWE_LOSS",
            "SWE_DPPO_DIVERGENCE_THRESHOLD",
            # Truncated-IS ratio cap for DPPO (0=disabled); tolerates gen/train mismatch.
            "SWE_DPPO_RATIO_CAP",
            "SWE_OFFPOLICY_STEPS",
            # Overlap the generator weight-pull FETCH with generation (only APPLY pauses
            # the engine), so generators keep producing rollouts during the transfer.
            "SWE_OVERLAP_WEIGHT_FETCH",
            # Rollout buffer size (run-ahead groups), decoupled from the staleness cap
            # SWE_OFFPOLICY_STEPS. Enlarges concurrency without loosening staleness.
            "SWE_MAX_ACTIVE_GROUPS",
            # Cold-start active-group admission limit. The TMax-9B default is 32;
            # retained trainable groups open the remaining headroom up to the full
            # active-group capacity.
            "SWE_INITIAL_ACTIVE_GROUPS",
            # Overfit bench: one active group, slots idle through group tails.
            "SWE_ALLOW_IDLE_ROLLOUT_SLOTS",
            # Sync overfit runs: the next group waits for pending rewrites.
            "SWE_SYNC_WAIT_FOR_REWRITES",
            "SWE_SYNC_REWRITE_TIMEOUT_SEC",
            # Validation on the training generators when there is no eval host.
            "SWE_VAL_ASYNC",
            "SWE_VAL_SKIP_UNCHANGED",
            "SWE_VAL_DEFER",
            "SWE_VAL_TRACE_BACKGROUND",
            "SWE_EVAL_SHADOW_GRADE_TIMEOUTS",
            "SWE_EVAL_GRADE_UNSUBMITTED",
            "SWE_EVAL_CONTEXT_NOTICE",
            "TMAX_GRADE_UNSUBMITTED_TRAIN",
            # Override the generator's vLLM max_num_seqs (decode batch cap per engine).
            "SWE_MAX_NUM_SEQS",
            "SWE_GPU_MEM_LIMIT",
            # SWE_GEN_CUDAGRAPH=0 -> eager vLLM decode (halves gen/train logprob
            # mismatch vs cudagraph decode, at the cost of decode throughput).
            "SWE_GEN_CUDAGRAPH",
            # SWE_GEN_PREFIX_CACHE=0 -> disable vLLM prefix caching on the GDN
            # generator (tests prefix-cache + GDN paged state as a format-error cause).
            "SWE_GEN_PREFIX_CACHE",
            "SWE_SALT_KV",
            "SWE_GEN_VLLM_DEFAULT_COMPILE",
            # SWE_PACK_GENERATORS=1 -> several sub-host generators per host.
            "SWE_PACK_GENERATORS",
            "SWE_WEIGHT_PULL_CONCURRENCY",
            "SWE_OVERLAP_WEIGHT_SYNC",
            "SWE_CPU_STAGED_WEIGHT_PULL",
            # SWE_PACKED_GPU_ORDINAL_FIX=1 -> packed single-GPU generators keep CUDA ordinal == physical GPU.
            "SWE_PACKED_GPU_ORDINAL_FIX",
            "SWE_PACKING",
            "SWE_EVAL_PULL_ONLY_BEFORE_VALIDATION",
            "SWE_TRAINER_COLUMN_SEND",
            "SWE_TRAINER_REGIONAL_COMPILE",
            "SWE_TS_REPLICA_DEDUP",
            "SWE_NATIVE_EP_PARTIAL_PULL",
            "SWE_GEN_REGIONAL_COMPILE",
            "SWE_TRAINER_REGIONAL_COMPILE_WIDE",
            "SWE_CONTROLLER_ASYNC_BOOKKEEPING",
            "SWE_TRAINER_MEM_LOG",
            "SWE_LEAN_NORMS",
            "SWE_MOE_EXPERT_RECOMPUTE",
            "SWE_PIN_THREAD_DEVICE",
            "SWE_EVAL_GEN_TP",
            "SWE_EVAL_GEN_EP",
            "SWE_NATIVE_TP_PARTIAL_PULL",
            "SWE_NATIVE_FAST_APPLY",
            "SWE_GEN_STEPS_PER_DECISION",
            "SWE_GEN_VLLM_MAX_CUDAGRAPH_CAPTURE_SIZE",
            "SWE_NATIVE_PINNED_EXPERT_BUFFERS",
            "SWE_SKIP_FROZEN_WEIGHT_SYNC",
            # TORCHSTORE_RDMA_ENABLED=0 -> weight sync over gloo/TCP instead of MonarchRDMA.
            "TORCHSTORE_RDMA_ENABLED",
            # SWE_GEN_COMPILE=1 -> torch.compile the wrapper generator only.
            "SWE_GEN_COMPILE",
            "SWE_GEN_COMPILE_BACKEND",
            # SWE_GEN_BACKEND=vllm_native -> vLLM's own Qwen3.5 model (weights via
            # state_dict_adapter); default torchtitan_wrapper (whole TT model in vLLM).
            "SWE_GEN_BACKEND",
            # SWE_LR=2e-7 -> override the trainer AdamW learning rate (default 1e-6);
            # for the effective-step-size A/B vs open-instruct (grad_norm*lr match).
            "SWE_LR",
            "SWE_AC",
            "SWE_LOSS_CHUNKS",
            "SWE_LMHEAD_TF32",
            # SWE_LMHEAD_TF32X3=1 -> fp32-accurate lm_head on tensor cores (3xTF32).
            "SWE_LMHEAD_TF32X3",
            "SWE_DP_FALLBACK_ROUTER",
            "SWE_DP_STICKY_REBALANCE",
            "SWE_DP_STICKY_MAX_SESSIONS",
            "SWE_EVOLUTION_HARDER_RATIO",
            "SWE_EVOLUTION_EASIER_RATIO",
            "SWE_EVOLUTION_MIN_SCORED",
            "SWE_EVOLUTION_SIGNALS",
            "SWE_DATA_HOT_RELOAD",
            # SWE_GDN_BI=1 -> batch-invariant GDN: one switch that flips trainer+generator
            # debug.batch_invariant, generator backend=torchtitan_wrapper, and
            # gdn_trainer_parity (recurrent-everywhere fla) so decode==prefill==trainer
            # logprobs are bitwise; also forces prefix cache on + FULL_DECODE_ONLY
            # cudagraph + reset (not salt) weight-sync KV. Default 0.
            "SWE_GDN_BI",
            # SWE_GEN_MAMBA_SSM_CACHE_DTYPE=float32 -> keep the vLLM GDN recurrent
            # (ssm) decode state in fp32 (matches the titan trainer's fla fp32 state);
            # default auto=bf16 for native. Reduces gen/train logprob mismatch.
            "SWE_GEN_MAMBA_SSM_CACHE_DTYPE",
            # SWE_GEN_NATIVE_FP32_LMHEAD=0 -> disable the fp32 lm_head patch on the
            # native generator (default on; matches the trainer's fp32 CastLinear).
            "SWE_GEN_NATIVE_FP32_LMHEAD",
            # SWE_DISABLE_SHUFFLE=1 -> deterministic train-prompt order.
            "SWE_DISABLE_SHUFFLE",
            "SWE_RESHUFFLE_EACH_EPOCH",
            "SWE_TRAIN_STEPS",
            "SWE_NUM_GROUPS_PER_TRAIN_STEP",
            "SWE_GROUP_SIZE",
            "SWE_POLICY_CANARY_K",
            "SWE_POLICY_CANARY_MAX_CONCURRENCY",
            "SWE_POLICY_CANARY_MAX_VERSION_SPAN",
            "SWE_POLICY_CANARY_TIMEOUT_SEC",
            "SWE_POLICY_CANARY_POLL_SEC",
            # Single debug switch: SWE_DEBUG_MAX_LOGDIFF=1 makes the dppo loss log the
            # worst gen/train logprob-diff token (argmax id + both logprobs + a window)
            # and dump the full per-token arrays under SWE_DUMP_DIR/max_logdiff for a
            # heat map + repro. Off by default.
            "SWE_DEBUG_MAX_LOGDIFF",
            # MSL-style sliding-prefix selection. Keep the optional max-bypass
            # stall off until complete groups have a reliable hard timeout.
            "SWE_SELECTION_WINDOW_GROUPS",
            "SWE_MAX_BYPASS_GROUPS",
            "SWE_STRICT_FIFO",
            # Zero-std prompt annotation (write) + skip list (read): the rollouter
            # writes one <instance_id>.json per all-pass/all-fail prompt under
            # SWE_ZERO_STD_DIR, and a later run passes that dir as SWE_SKIP_PROMPTS to
            # drop them at load.
            "SWE_ZERO_STD_DIR",
            # SWE_ROLLOUT_DUMP_DIR: write one human-readable per-rollout trace JSON
            # (task, grade, every captured turn's decoded completion) under this dir
            # for offline inspection of rollout normality.
            "SWE_ROLLOUT_DUMP_DIR",
            "SWE_SKIP_PROMPTS",
            "SWE_BRIDGE_POLL_INTERVAL",
            # Agent execution mode: "in_sandbox_claude" (default, file-relay bridge)
            # or "host_loop" (host-side ReAct agent, LLM direct to the on-box adapter,
            # sandbox=remote Bash). Without forwarding these the controller silently
            # defaults to in_sandbox_claude regardless of the submit-side export.
            "SWE_AGENT_MODE",
            # GDN (Qwen3.5/3.6): run.sh reads this to switch CUDA_HOME to the pip
            # nvidia/cu13 toolkit (ptxas 13.2) for the FLA/triton kernels' cubin.
            "SWE_GDN",
            # Run the GDN generation layer on TorchTitan's own fla kernels
            # (gdn_vllm_titan.register_titan_gdn in the generator) instead of the
            # vLLM-native GDN, to cut generator/trainer logprob drift. Default off.
            "TT_GDN_UNIFIED_KERNEL",
            # GDN generation via the vLLM-native path (also used by the curate
            # worker): renderer picks the qwen3.6 chat template, additional_config
            # sets gdn_prefill_backend=triton, and the NCCL all-reduce fallback
            # avoids the TP>1 custom-all-reduce hang. curate_passrate reads these
            # as argparse defaults, so they must reach the role env.
            "SWE_RENDERER",
            "SWE_VLLM_ADDITIONAL_CONFIG",
            # Blackwell: FlashInfer JIT-builds its trtllm FMHA kernel for sm_100a
            # and the build fails ("CUDA compiler and CUDA toolkit headers are
            # incompatible"), so the backend has to be selectable per job.
            "VLLM_ATTENTION_BACKEND",
            "SWE_GEN_VLLM_ATTENTION",
            "NVCC_PREPEND_FLAGS",
            "VLLM_BATCH_INVARIANT",
            # =0 on Blackwell: generator.py's flashinfer import stub otherwise
            # aborts vllm_native engine init (B300 bring-up handoff, section 4).
            "VLLM_ALLREDUCE_USE_FLASHINFER",
            "MAST_PRECHECK_PRERUN_STEP_KILLSWITCH",
            "MAST_RL_NET_PROBE",
            "VLLM_TUNED_CONFIG_FOLDER",
            "SWE_DISABLE_CUSTOM_ALL_REDUCE",
            "SWE_MAX_TURNS",
            # TMAX_* knobs the tmax example reads directly (vanillux_loop /
            # grading / the agent registry). Without them in this list the
            # submit script's exports never reach the rollout workers, so the
            # per-command and verifier timeouts silently stayed at their
            # in-code defaults.
            "SWE_PREFLIGHT_TIMEOUT",
            "SWE_PREFLIGHT_FANOUT",
            "TMAX_AGENT",
            "TMAX_EXEC_TIMEOUT_SEC",
            "TMAX_EVAL_TIMEOUT_SEC",
            "TMAX_CALL_LIMIT",
            "TMAX_CTRF_DIAGNOSTICS",
            "TMAX_MAX_FORMAT_ERRORS",
            "TMAX_FORMAT_ERROR_FEEDBACK",
            "TMAX_TERMINUS_MODEL",
            "TMAX_TERMINUS_PARSER",
            "TMAX_TERMINUS_MAX_TURNS",
            "TMAX_TURN_MAX_TOKENS",
            # Terminus context-limit notice (overfit bench recipe: v2 / 0.7).
            "TMAX_CONTEXT_NOTICE_STYLE",
            "TMAX_CONTEXT_WARN_FRAC",
            "TMAX_CONTEXT_WARN_TOKENS_LEFT",
            "TMAX_CONTEXT_REPEAT_TOKENS_LEFT",
            "TMAX_CONTEXT_STATUS_LINES",
            # Batcher loss aggregation (prompt_mean default in the tmax recipe).
            "SWE_LOSS_AGG",
            # Grade unsubmitted rollouts for the record only (reward unchanged).
            "TMAX_SHADOW_GRADE_UNSUBMITTED",
            "SWE_TOOL_OUTPUT_LIMIT",
            "SWE_HOST_SYSTEM_PROMPT",
            "SWE_HOST_TASK_PROMPT",
            "SWE_REWARD_DENSE",
            # Per-format-error reward penalty (training advantage only) + its cap,
            # and the per-turn advantage offset for format-error turns.
            "SHIM_PORT",
            "TT_SANDBOX_BACKEND",
            "TT_DAYTONA_CPU",
            "TT_DAYTONA_MEM_GB",
            "TT_DAYTONA_MAX_MEM_GB",
            "TT_DAYTONA_DISK_GB",
            "TT_DAYTONA_EPHEMERAL",
            # Per-worker cap on concurrent sandbox CREATES (throttle-avoidance); total
            # create rate = num_rollout_workers x this. Rollouts still RUN up to
            # SWE_ROLLOUT_CONCURRENCY once booted.
            "TT_DAYTONA_CREATE_CONCURRENCY",
            "TT_DAYTONA_CREATE_RETRIES",
            "TT_DAYTONA_AUTO_STOP_MIN",
            "TT_DAYTONA_AUTO_DELETE_MIN",
            "TT_DAYTONA_EXEC_TIMEOUT_MIN",
            "TT_DAYTONA_HEARTBEAT_SEC",
            "TT_DAYTONA_RPC_RETRIES",
        ):
            val = os.environ.get(key)
            if key == "NVCC_PREPEND_FLAGS" and val:
                # `conda activate` on the devvm adds -ccbin=<devvm conda>/...-c++;
                # that path does not exist on MAST and every JIT nvcc call fails
                # (tilelang GDN backward). run.sh sets CC/CXX for the job itself.
                val = " ".join(t for t in val.split() if not t.startswith("-ccbin"))
            if val:
                env[key] = val

        packages = Packages()
        packages.add_package("oil.oilfs:stable")
        packages.add_package("manifold.manifoldfs:prod")

        generator_roles = generator_host_role_names(num_generators, gens_per_host)
        eval_generator_roles = eval_generator_host_role_names(
            num_eval_generators, eval_gens_per_host
        )
        if collocate_on_controller:
            meshes = [f"controller:1:{cfg.host_type}"]
        elif trainer_on_controller:
            meshes = [f"controller:1:{cfg.host_type}"]
            meshes += [
                f"{name}:{gen_nodes_each}:{cfg.host_type}" for name in generator_roles
            ]
            meshes += [
                f"{name}:{eval_gen_nodes_each}:{cfg.host_type}"
                for name in eval_generator_roles
            ]
        else:
            # One role for the trainer plus a dedicated role per generator,
            # each sized for one generator's hosts.
            meshes = [f"trainer:{trainer_nodes}:{cfg.host_type}"]
            meshes += [
                f"{name}:{gen_nodes_each}:{cfg.host_type}" for name in generator_roles
            ]
            meshes += [
                f"{name}:{eval_gen_nodes_each}:{cfg.host_type}"
                for name in eval_generator_roles
            ]
        appdef = meta_hyperactor.host_mesh_conda(
            image=cfg.image,
            meshes=meshes,
            additional_packages=packages,
            env=env,
            use_simple_bootstrap=True,
            use_ipv6_transport=True,
        )
        # Workers run Monarch's SIMPLE_BOOTSTRAP, which (unlike the controller)
        # bypasses run.sh -- so they would miss the Manifold mount, CUDA_HOME /
        # LD_LIBRARY_PATH, libcuda symlinks, the c++ toolchain, and NCCL settings
        # run.sh provides. Wrap each worker's command with run.sh so it does the
        # same host setup before exec'ing the bootstrap. run.sh skips the
        # retriever for worker roles (it only starts on the controller).
        for role in appdef.roles:
            role.resource.capabilities[MAST_WHOLE_HOST_FEATURE] = True
            run_as(role, root_user=True)
            if not cfg.enable_ttls:
                ttls(role, enable=False)
            role.args = [
                role.args[0],  # ${img_root}/conda/bin/entrypoint.sh (conda activate)
                "${img_root}/workspace/mast_rl/run.sh",
                *role.args[1:],  # python -X faulthandler -c '<monarch bootstrap>'
            ]

        if collocate_on_controller:
            # With no --job-name, main.py uses spawn_proc_mesh(host_meshes=None)
            # to partition this host's GPUs between trainer and generators.
            controller = appdef.roles[0]
            controller.entrypoint = "/bin/bash"
            bash_cmd = (
                'exec "${WORKSPACE_DIR}/mast_rl/run.sh" python mast_rl/main.py "$@"'
            )
            controller.args = ["-c", bash_cmd, "--"]
            run_as(controller, root_user=True)
        elif trainer_on_controller:
            # main.py attaches only to the detached generator roles and uses
            # this controller host as the trainer HostMesh.
            controller = appdef.roles[0]
            controller.entrypoint = "/bin/bash"
            bash_cmd = (
                'exec "${WORKSPACE_DIR}/mast_rl/run.sh" python mast_rl/main.py '
                '--job-name "$1" "${@:2}"'
            )
            controller.args = ["-c", bash_cmd, "--", job_name]
            run_as(controller, root_user=True)
        else:
            # Copy the first worker role so the detached controller inherits the
            # same image, packages, and environment.
            controller = copy.deepcopy(appdef.roles[0])
            controller.name = "controller"
            controller.num_replicas = 1
            controller.entrypoint = "/bin/bash"
            bash_cmd = (
                'exec "${WORKSPACE_DIR}/mast_rl/run.sh" python mast_rl/main.py '
                '--job-name "$1" "${@:2}"'
            )
            controller.args = ["-c", bash_cmd, "--", job_name]
            run_as(controller, root_user=True)
            appdef.roles.insert(0, controller)

        # SWE-R2E data-washing: stamp a distinct shard id on each role so run.sh's
        # SWE_CURATE branch washes a disjoint slice of the task pool per host. This
        # assumes one host per role (single-host trainer + gen_nodes_each=1), so the
        # role list IS the host list; SWE_NUM_SHARDS = number of roles.
        if (
            os.environ.get("SWE_CURATE") == "1"
            or os.environ.get("SWE_AGGREGATE") == "1"
        ):
            num_shards = len(appdef.roles)
            for shard_id, role in enumerate(appdef.roles):
                role.env = dict(role.env or {})
                role.env["SWE_SHARD_ID"] = str(shard_id)
                role.env["SWE_NUM_SHARDS"] = str(num_shards)

        appdef.name = job_name
        appdef.metadata["tags"] = ",".join(
            ["torchtitan-rl", "monarch:torchtitan-rl", "monarch"]
        )
        # Region is pinned by ``localityConstraints``; this prefers placing all
        # roles in one datacenter (one RDMA domain) within that region, falling
        # back to anywhere in the region. NetworkAffinityScope: 2=DC, 1=REGION.
        # NetworkAffinityScope: 2=DC, 1=REGION. The RL loop wants all roles in one
        # DC (one RDMA domain for FSDP/weight-sync). The data-washing job has NO
        # cross-host communication (each shard is independent), so prefer REGION to
        # place its many hosts without waiting for a co-located DC slot.
        preferred_scope = 1 if os.environ.get("SWE_CURATE") == "1" else 2
        appdef.metadata["mast"] = {
            "HpcJobDefinition": {
                "networkAffinity": {
                    "preferredScope": preferred_scope,
                    "fallbackScope": 1,
                },
            },
        }
        return appdef

    # ------------------------------------------------------------------ #
    # controller (inside MAST): attach
    # ------------------------------------------------------------------ #
    def acquire_hosts(
        self, job_name: str, role_names: tuple[str, ...]
    ) -> dict[str, "HostMesh"]:
        """Attach to the running job; return the ``HostMesh`` per worker role."""
        from monarch._rust_bindings.monarch_hyperactor.channel import ChannelTransport
        from monarch.actor import enable_transport
        from monarch.job.meta import MASTJob

        transport = ChannelTransport.MetaTlsWithIpV6
        enable_transport(transport)
        if self.config.attach_via_mount:
            return self._acquire_hosts_from_mount(role_names, transport)
        app_handle = f"mast_conda:///{job_name}"
        logger.info("attaching to %s for roles=%s", app_handle, list(role_names))
        job = MASTJob.from_torchx(
            app_handle=app_handle,
            role_names=list(role_names),
            default_transport=transport,
        )
        # monarch's own wait loop polls until every role is RUNNING, but it treats a
        # torchx status() of None as fatal ("Job not found") instead of as "not
        # registered yet". MAST returns None for a window after scheduling, and the
        # window scales with task-group count: the 8-generator 27B job (11 groups)
        # hit it on all 3 launcher retries and died with every host already
        # allocated, while 8-group 9B jobs never did. Retry only that message --
        # "Job failed with state" is also a ValueError and must stay fatal.
        deadline = time.monotonic() + _ATTACH_TIMEOUT_SEC
        while True:
            try:
                state = job.state(cached_path=None)
                break
            except ValueError as e:
                if "not found" not in str(e).lower() or time.monotonic() > deadline:
                    raise
                logger.warning(
                    f"job not registered yet ({e}); retrying attach in "
                    f"{_ATTACH_RETRY_SEC}s"
                )
                time.sleep(_ATTACH_RETRY_SEC)
        return {name: getattr(state, name) for name in role_names}


    def _acquire_hosts_from_mount(
        self, role_names: tuple[str, ...], transport
    ) -> dict[str, "HostMesh"]:
        """``acquire_hosts`` without the MAST API: wait for every worker of each
        role to publish its address (publish_worker_addr.py), then attach the way
        ``MASTJob`` does."""
        from monarch._rust_bindings.monarch_hyperactor.config import configure
        from monarch._src.actor.bootstrap import attach_to_workers

        configure(default_transport=transport)
        hosts_dir = (
            Path(os.environ["MAST_RL_HOSTS_DIR"])
            / f"attempt{os.environ.get('MAST_HPC_JOB_ATTEMPT_INDEX', '0')}"
        )
        deadline = time.monotonic() + _ATTACH_TIMEOUT_SEC
        meshes = {}
        for name in role_names:
            while True:
                records = [
                    json.loads(p.read_text()) for p in sorted(hosts_dir.glob(f"{name}.*"))
                ]
                if records and len(records) == records[0]["group_size"]:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"{len(records)} worker address(es) for role {name} in "
                        f"{hosts_dir} after {_ATTACH_TIMEOUT_SEC}s"
                    )
                time.sleep(_ATTACH_RETRY_SEC)
            # The file suffix is the worker's rank in its role.
            records = [
                json.loads((hosts_dir / f"{name}.{rank}").read_text())
                for rank in range(len(records))
            ]
            logger.info("attaching %s to %d published worker(s)", name, len(records))
            meshes[name] = attach_to_workers(
                ca="trust_all_connections",
                workers=[r["addr"] for r in records],
                name=name,
            )
        return meshes


def launcher_config(cfg) -> MastLauncher.Config:
    """Return the ``MastLauncher.Config`` carried by a parsed training config.

    A ``mast_rl`` recipe returns a ``MastRLConfig`` whose ``launcher`` field holds
    the MAST launch knobs. Raise if it is missing or the wrong type.
    """
    lc = getattr(cfg, "launcher", None)
    if not isinstance(lc, MastLauncher.Config):
        raise ValueError(
            f"--config must name a mast_rl recipe carrying a MastLauncher.Config "
            f"`launcher` field; got {type(cfg).__name__}."
        )
    return lc
