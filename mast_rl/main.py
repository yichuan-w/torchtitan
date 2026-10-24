# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Controller entry point for multi-host Search-R1 RL training on MAST.

Ported from ``fbsource//fbcode/pytorch/torchtitan/fb/mast_rl/main.py``.

When ``--job-name`` is set, this runs as the single-host MAST *controller*: it
attaches to the already-running ``trainer`` and ``generator_<i>`` host meshes via
``MastLauncher.acquire_hosts`` and drives the training loop over them. Without
``--job-name`` it runs locally on this host's GPUs (the ``--local`` path), with
trainer + generators partitioning ``this_host()`` via CUDA_VISIBLE_DEVICES.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from torchtitan.config import ConfigManager
from torchtitan.experiments.rl.controller import Controller
from torchtitan.experiments.rl.train import (
    HostMeshes,
    _compute_generator_world_size,
    _compute_trainer_world_size,
    spawn_proc_mesh,
)
from torchtitan.observability import structured_logger as sl

logger: logging.Logger = logging.getLogger(__name__)


async def _run_training(
    *,
    config: Controller.Config,
    host_meshes: HostMeshes | None,
) -> None:
    assert isinstance(config, Controller.Config)
    sl.init_structured_logger(
        source="rl_controller",
        output_dir=config.dump_folder,
        rank=0,
        enable=config.trainer.debug.enable_structured_logging,
    )
    sl.log_trace_instant("structured_logger_started")

    rl_trainer: Controller = config.build()
    training_completed = False
    try:
        trainer_world_size = _compute_trainer_world_size(config.trainer.parallelism)
        generator_world_size = _compute_generator_world_size(
            config.generator.parallelism
        )
        eval_generator_world_size = _compute_generator_world_size(
            config.eval_generator_parallelism()
        )
        trainer_mesh, generator_meshes, eval_generator_meshes = spawn_proc_mesh(
            trainer_world_size,
            generator_world_size,
            host_meshes=host_meshes,
            num_generators=config.num_generators,
            num_eval_generators=config.num_eval_generators,
            per_eval_generator_world_size=eval_generator_world_size,
        )
        await rl_trainer.setup_async(
            trainer_mesh=trainer_mesh,
            generator_meshes=generator_meshes,
            eval_generator_meshes=eval_generator_meshes,
        )
        await rl_trainer.run()
        training_completed = True
    except (KeyboardInterrupt, asyncio.CancelledError):
        # A mesh that fails *during* training surfaces as a Monarch supervision
        # event, delivered as a KeyboardInterrupt to the main thread. Re-raise so
        # the MAST job ends with a non-zero exit code (failure), not a clean exit.
        logger.info("Interrupted; attempting graceful shutdown...")
        raise
    finally:
        # Worker process meshes can segfault inside mesh.stop() during teardown
        # (Monarch/CUDA/NCCL cleanup), surfacing as Cancelled/KeyboardInterrupt.
        # After a fully completed run all results are already persisted, so a
        # teardown crash must not turn success into a MAST failure.
        try:
            await rl_trainer.close()
        except BaseException as e:  # noqa: B036 -- intentionally broad on teardown
            if not training_completed:
                raise
            logger.warning("Ignoring teardown error after completed training: %r", e)


async def _run_from_args(
    *,
    script_args: list[str],
    host_meshes: HostMeshes | None,
) -> None:
    config = ConfigManager().parse_args(script_args)
    await _run_training(config=config, host_meshes=host_meshes)


async def _run_on_mast(job_name: str, script_args: list[str]) -> None:
    from mast import (
        eval_generator_host_role_names,
        generator_host_role_names,
        generators_per_host,
        gpus_per_node,
        launcher_config,
    )
    from monarch.actor import this_host
    from monarch.config import configure as configure_monarch

    # Parse the same config the devserver launcher used; the per-host GPU count
    # comes from cfg.launcher (recipe default / --launcher.gpus-per-node), so it
    # matches what sized the appdef.
    config = ConfigManager().parse_args(script_args)
    mesh_proc_spawn_max_idle = os.environ.get("MONARCH_MESH_PROC_SPAWN_MAX_IDLE")
    if mesh_proc_spawn_max_idle:
        configure_monarch(mesh_proc_spawn_max_idle=mesh_proc_spawn_max_idle)
        logger.info(
            "Monarch mesh proc spawn max idle set to %s",
            mesh_proc_spawn_max_idle,
        )
    mesh_orphan_timeout = os.environ.get("MONARCH_MESH_ORPHAN_TIMEOUT")
    if mesh_orphan_timeout:
        configure_monarch(mesh_orphan_timeout=mesh_orphan_timeout)
        logger.info(
            "Monarch mesh orphan timeout set to %s",
            mesh_orphan_timeout,
        )
    # Startup robustness: the default host-spawn-ready timeout is ~30s, and on a large
    # multi-host job (1 controller + trainer hosts + N generator hosts) a single slow
    # host bootstrap trips "error spawning proc mesh: Timeout(30s)" -> the controller
    # dies at startup and MAST retries. This is pure infra flakiness (host allocation),
    # unrelated to the RL/buffer knobs. Raise it so a slow-but-healthy host is tolerated.
    host_spawn_ready_timeout = os.environ.get("MONARCH_HOST_SPAWN_READY_TIMEOUT")
    if host_spawn_ready_timeout:
        configure_monarch(host_spawn_ready_timeout=host_spawn_ready_timeout)
        logger.info(
            "Monarch host spawn ready timeout set to %s",
            host_spawn_ready_timeout,
        )
    # A replacement host on a MAST retry can attach late; the 10s default config-push
    # barrier then fails the retry at startup (1018 att1: MESH_ATTACH_CONFIG_TIMEOUT).
    mesh_attach_config_timeout = os.environ.get(
        "MONARCH_MESH_ATTACH_CONFIG_TIMEOUT", "180s"
    )
    if mesh_attach_config_timeout:
        configure_monarch(mesh_attach_config_timeout=mesh_attach_config_timeout)
        logger.info(
            "Monarch mesh attach config timeout set to %s",
            mesh_attach_config_timeout,
        )
    # Root fix for the recurring "timed out reaching controller ... for mesh generator_N.
    # Assuming controller's proc is dead" crash: under high rollout concurrency the
    # controller<->generator mesh message channels congest and Monarch's message-delivery
    # deadline fires, so HEALTHY generators are declared dead -> global-fatal cascade ->
    # controller exit 1 (no OOM-kill in stderr; generators are fine). This is a DIFFERENT
    # timeout than supervision_watchdog (already raised) -- message_delivery_timeout is the
    # one that fires on channel backlog. Raise it so a congested-but-alive channel is not
    # mistaken for a dead proc. Pair with a lower SWE_ROLLOUT_CONCURRENCY.
    message_delivery_timeout = os.environ.get("MONARCH_MESSAGE_DELIVERY_TIMEOUT")
    if message_delivery_timeout:
        configure_monarch(message_delivery_timeout=message_delivery_timeout)
        logger.info(
            "Monarch message delivery timeout set to %s",
            message_delivery_timeout,
        )
    # Root fix for the recurring "timeout waiting for message from proc mesh agent
    # while querying for rollout_worker_N; the process likely crashed" -> global-fatal
    # supervision event. The worker did NOT crash (no OOM/segfault in its logs): it
    # blocked longer than the default supervision/state-query timeout on a slow-but-
    # healthy Daytona op (a cold sandbox build is 20-40s, an SWE-Smith Docker Hub image
    # pull is longer), so Monarch inferred "crashed" from a timeout. Raise the
    # supervision-stream liveness AND the proc/actor state-query idle timeouts well above
    # the worst-case Daytona latency so a slow worker is not mistaken for a dead one.
    # humantime strings, e.g. "180s".
    # Default ON at 180s (override via MONARCH_SUPERVISION_WATCHDOG_TIMEOUT): every RL
    # run needs headroom over Daytona's slow-but-healthy ops, so make the root fix the
    # default rather than opt-in.
    supervision_watchdog_timeout = os.environ.get(
        "MONARCH_SUPERVISION_WATCHDOG_TIMEOUT", "1000s"
    )
    if supervision_watchdog_timeout:
        supervision_kwargs = {
            "supervision_watchdog_timeout": supervision_watchdog_timeout,
            "get_proc_state_max_idle": os.environ.get(
                "MONARCH_GET_PROC_STATE_MAX_IDLE", supervision_watchdog_timeout
            ),
            "get_actor_state_max_idle": os.environ.get(
                "MONARCH_GET_ACTOR_STATE_MAX_IDLE", supervision_watchdog_timeout
            ),
        }
        configure_monarch(**supervision_kwargs)
        logger.info(
            "Monarch supervision timeouts raised: watchdog=%s proc_state_idle=%s "
            "actor_state_idle=%s",
            supervision_kwargs["supervision_watchdog_timeout"],
            supervision_kwargs["get_proc_state_max_idle"],
            supervision_kwargs["get_actor_state_max_idle"],
        )
    lc = launcher_config(config)
    node_gpus = lc.gpus_per_node or gpus_per_node(lc.host_type)

    num_generators = config.num_generators
    gens_per_host = generators_per_host(
        _compute_generator_world_size(config.generator.parallelism), node_gpus
    )
    generator_roles = generator_host_role_names(num_generators, gens_per_host)
    eval_gens_per_host = generators_per_host(
        _compute_generator_world_size(config.eval_generator_parallelism()), node_gpus
    )
    eval_generator_roles = eval_generator_host_role_names(
        config.num_eval_generators, eval_gens_per_host
    )
    worker_roles = (*generator_roles, *eval_generator_roles)
    role_names = (
        worker_roles if lc.trainer_on_controller else ("trainer", *worker_roles)
    )
    meshes = lc.build().acquire_hosts(job_name, role_names=role_names)
    # Packed generators share one HostMesh object, so train.py's per-host GPU
    # allocator hands each a disjoint slice of that host.
    generator_host_meshes = [
        meshes[generator_roles[i // gens_per_host]] for i in range(num_generators)
    ]
    eval_generator_host_meshes = [
        meshes[eval_generator_roles[i // eval_gens_per_host]]
        for i in range(config.num_eval_generators)
    ]
    trainer_host_mesh = this_host() if lc.trainer_on_controller else meshes["trainer"]
    logger.info(
        "attached: trainer=%d host(s); generator host counts=%s; "
        "eval generator host counts=%s",
        len(trainer_host_mesh),
        [len(m) for m in generator_host_meshes],
        [len(m) for m in eval_generator_host_meshes],
    )
    # The bench scheduler's socket is host-local; the rewriter runs on the devvm,
    # so carry it through the job dir on the mount (ofb_socket_relay.py).
    if os.environ.get("SWE_BENCH_SCHEDULER") == "1" and os.environ.get("TRL_BASE"):
        import subprocess

        relay = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ofb_socket_relay.py")
        subprocess.Popen([sys.executable, relay, "back", os.environ["TRL_BASE"]])
        logger.info("bench socket relay (back) started for %s", os.environ["TRL_BASE"])
    try:
        await _run_training(
            config=config,
            host_meshes=HostMeshes(
                trainer=trainer_host_mesh,
                generators=generator_host_meshes,
                gpus_per_node=node_gpus,
                eval_generators=eval_generator_host_meshes,
            ),
        )
    finally:
        # Shut down the host meshes so the worker roles exit instead of idling
        # in their bootstrap loop. A dead worker can deliver a teardown crash as
        # Cancelled/KeyboardInterrupt, so catch BaseException -- the run is over
        # either way and the workers get reaped when the controller exits.
        for name, mesh in meshes.items():
            try:
                await mesh.shutdown()
            except BaseException:  # noqa: B036 -- intentionally broad on teardown
                logger.exception("%s host_mesh.shutdown failed", name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--job-name",
        default=None,
        help="MAST job the RL training is running on. None means to run locally"
        " on this host's GPUs (the --local path).",
    )
    args, remaining = parser.parse_known_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    if args.job_name:
        asyncio.run(_run_on_mast(job_name=args.job_name, script_args=remaining))
    else:
        asyncio.run(_run_from_args(script_args=remaining, host_meshes=None))


if __name__ == "__main__":
    main()
