# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
RL training loop using Monarch Actors.

This demonstrates:
1. Distributed actor architecture with VLLMGenerator (vLLM) and PolicyTrainer (TorchTitan)
   running on separate GPU meshes
2. Weight synchronization across meshes via TorchStore: the trainer publishes its
   model state dict and the generator pulls it into its own parallelism layout,
   with direct GPU-to-GPU RDMA transfer when available
3. Envs driven rollouts; reward and advantage computation live inline
   in the controller.

Command to run:
python3 -m torchtitan.experiments.rl.train \
    --module alphabet_sort --config rl_grpo_qwen3_0_6b_varlen \
    --hf_assets_path=<path_to_model_checkpoint>
"""

import asyncio
import logging
import os
import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

# must run before torch import. Set it as early as possible to avoid other
# imports transitively importing torch.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# Monarch reads this setting during import. Keep async actor endpoints concurrent;
# @concurrent_endpoint is not available in the stable Monarch release yet (#3832).
os.environ["MONARCH_ACTOR_QUEUE_DISPATCH"] = "0"

from monarch.actor import HostMesh, ProcMesh, this_host

from torchtitan.config import ConfigManager, ParallelismConfig
from torchtitan.experiments.rl.controller import Controller
from torchtitan.experiments.rl.models.vllm_registry import InferenceParallelismConfig
from torchtitan.observability import structured_logger as sl


logger = logging.getLogger(__name__)


class PerHostProvisioner:
    """Allocates non-overlapping GPU ranges within a single host.

    On the same host, the trainer and generator run on separate GPU
    meshes (e.g. GPUs 0-3 for training, GPUs 4-7 for generation). Each
    call to `allocate(n)` reserves the next *n* GPUs and returns a
    bootstrap callable that sets `CUDA_VISIBLE_DEVICES` before CUDA
    initializes in the spawned process, ensuring each mesh only sees its
    own devices.
    """

    def __init__(self, total_gpus: int = 8):
        self.total_gpus = total_gpus
        self.next_gpu = 0
        # RL_GPUS names the physical devices this run may use, in order. Each mesh
        # takes its slice of that list by position, so the set need not be
        # contiguous -- the GPUs are all-to-all NVLinked and nothing here requires
        # adjacency. Order is the placement: the trainer allocates first, then one
        # generator per entry. Unset -> RL_GPU_OFFSET applies instead.
        self.devices = [d for d in os.environ.get("RL_GPUS", "").split(",") if d]
        if self.devices and len(self.devices) < total_gpus:
            raise ValueError(
                f"RL_GPUS lists {len(self.devices)} device(s) but this run needs "
                f"{total_gpus}. List every GPU the run may use."
            )

    @property
    def available(self) -> int:
        return self.total_gpus - self.next_gpu

    def allocate(self, num_gpus: int) -> Callable[[], None]:
        if num_gpus > self.available:
            raise RuntimeError(
                f"Requested {num_gpus} GPUs but only {self.available} "
                f"available (total={self.total_gpus}, allocated={self.next_gpu})"
            )
        gpu_ids = list(range(self.next_gpu, self.next_gpu + num_gpus))
        self.next_gpu += num_gpus

        devices = self.devices

        def _bootstrap():
            # RL_GPUS wins when set and is used verbatim, which is what lets a run
            # take a non-contiguous subset. Otherwise RL_GPU_OFFSET lets a local run
            # use a GPU subset of a shared host that does not start at absolute
            # index 0 (e.g. GPUs 0-3 busy -> offset=4). Default 0 = unchanged.
            if devices:
                visible = [devices[g] for g in gpu_ids]
            else:
                offset = int(os.environ.get("RL_GPU_OFFSET", "0"))
                visible = [str(g + offset) for g in gpu_ids]
            os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(visible)
            # TODO: Remove once Monarch/PyTorch fixes concurrent import during unpickling.
            import torch  # noqa: F401

        return _bootstrap


@dataclass
class HostMeshes:
    trainer: HostMesh
    generators: list[HostMesh]
    gpus_per_node: int
    eval_generators: list[HostMesh] = field(default_factory=list)
    """Hosts for generators dedicated to validation. Held out of the training
    generator pool so a validation pass never competes with rollout collection."""


def _compute_trainer_world_size(p: ParallelismConfig) -> int:
    """Compute world size from all parallel dimensions."""
    dp_shard = max(p.data_parallel_shard_degree, 1)
    return (
        p.data_parallel_replicate_degree
        * dp_shard
        * p.tensor_parallel_degree
        * p.pipeline_parallel_degree
        * p.context_parallel_degree
    )


def _compute_generator_world_size(p: InferenceParallelismConfig) -> int:
    """Number of GPU processes for one generator (vLLM) instance."""
    return p.data_parallel_degree * p.tensor_parallel_degree


def _spawn_proc_mesh(
    host_mesh: HostMesh,
    role_world_size: int,
    gpus_per_node: int,
    *,
    role: str,
) -> ProcMesh:
    """Spawn one role's proc mesh on ``host_mesh``, splitting ``role_world_size``
    evenly across the mesh's hosts.
    """
    nodes = len(host_mesh)
    assert role_world_size % nodes == 0, (
        f"{role} world size ({role_world_size}) must be evenly divisible by its "
        f"host count ({nodes})"
    )
    role_gpus_per_node = role_world_size // nodes
    provisioner = PerHostProvisioner(total_gpus=gpus_per_node)
    return host_mesh.spawn_procs(
        per_host={"gpus": role_gpus_per_node},
        bootstrap=provisioner.allocate(role_gpus_per_node),
    )


def spawn_proc_mesh(
    trainer_world_size: int,
    per_generator_world_size: int,
    host_meshes: HostMeshes | None = None,
    *,
    num_generators: int = 1,
    num_eval_generators: int = 0,
    per_eval_generator_world_size: int | None = None,
) -> tuple[ProcMesh, list[ProcMesh], list[ProcMesh]]:
    """Spawn the trainer, generator, and eval-generator proc meshes.

    Args:
        trainer_world_size: Number of GPU procs to spawn for the trainer.
        per_generator_world_size: Number of GPU procs to spawn for each generator.
        host_meshes: Caller-provided trainer/generator host meshes. When
            provided, each role is spawned on its provided host mesh. None means
            both roles are spawned on ``this_host()`` by using non-overlapping
            GPU ranges.
        num_generators: Number of generator proc meshes to spawn.
        num_eval_generators: Number of generator proc meshes reserved for
            validation. Each stays out of the training router, so a validation
            pass runs on its own GPUs.
        per_eval_generator_world_size: GPU procs for each eval generator. None
            sizes them like a training generator. A dedicated eval generator is
            idle between passes, so a smaller one (1 GPU) buys the validation
            curve without taking a training generator's worth of the box.

    Returns:
        The ``(trainer_mesh, generator_meshes, eval_generator_meshes)`` proc meshes.
    """
    if per_eval_generator_world_size is None:
        per_eval_generator_world_size = per_generator_world_size
    total_generator_gpus = (
        num_generators * per_generator_world_size
        + num_eval_generators * per_eval_generator_world_size
    )
    total_gpus = trainer_world_size + total_generator_gpus
    logger.info(
        f"{num_generators} generator(s) * {per_generator_world_size} GPUs + "
        f"{num_eval_generators} eval generator(s) * "
        f"{per_eval_generator_world_size} GPUs + "
        f"{trainer_world_size} trainer GPUs = {total_gpus} total"
    )

    if host_meshes is not None:
        trainer_host_mesh = host_meshes.trainer
        generator_host_meshes = host_meshes.generators
        eval_generator_host_meshes = host_meshes.eval_generators
        gpus_per_node = host_meshes.gpus_per_node

        assert len(generator_host_meshes) == num_generators, (
            f"expected {num_generators} generator host mesh(es), "
            f"got {len(generator_host_meshes)}"
        )
        assert len(eval_generator_host_meshes) == num_eval_generators, (
            f"expected {num_eval_generators} eval generator host mesh(es), "
            f"got {len(eval_generator_host_meshes)}"
        )

        trainer_mesh = _spawn_proc_mesh(
            trainer_host_mesh, trainer_world_size, gpus_per_node, role="trainer"
        )
        generator_meshes = [
            _spawn_proc_mesh(
                gen_host_mesh,
                per_generator_world_size,
                gpus_per_node,
                role="generator",
            )
            for gen_host_mesh in generator_host_meshes
        ]
        eval_generator_meshes = [
            _spawn_proc_mesh(
                gen_host_mesh,
                per_eval_generator_world_size,
                gpus_per_node,
                role="eval_generator",
            )
            for gen_host_mesh in eval_generator_host_meshes
        ]
    else:
        # Single-node mode: partition GPUs on this_host() via
        # CUDA_VISIBLE_DEVICES
        host_mesh = this_host()
        provisioner = PerHostProvisioner(total_gpus=total_gpus)
        trainer_mesh = host_mesh.spawn_procs(
            per_host={"gpus": trainer_world_size},
            bootstrap=provisioner.allocate(trainer_world_size),
        )
        generator_meshes = [
            host_mesh.spawn_procs(
                per_host={"gpus": per_generator_world_size},
                bootstrap=provisioner.allocate(per_generator_world_size),
            )
            for _ in range(num_generators)
        ]
        eval_generator_meshes = [
            host_mesh.spawn_procs(
                per_host={"gpus": per_eval_generator_world_size},
                bootstrap=provisioner.allocate(per_eval_generator_world_size),
            )
            for _ in range(num_eval_generators)
        ]

    return trainer_mesh, generator_meshes, eval_generator_meshes


def _configure_monarch_runtime() -> None:
    """Raise Monarch's actor-runtime deadlines to survive a large multi-host RL run.

    Defaults, not just for our cluster: on a controller driving many generator meshes
    at high rollout concurrency, the controller<->generator message channels congest and
    Monarch's default deadlines fire, so HEALTHY generators get declared dead and the
    whole job takes a global-fatal cascade. Raising these tolerates the backlog. Each is
    env-overridable so a run can tune or disable it; humantime strings ("300s").

      - message_delivery_timeout: the deadline that fires on channel backlog ("timed out
        reaching controller ... for mesh"); the biggest lever for the false-death crash.
      - host_spawn_ready_timeout: startup deadline for a slow host to bootstrap its procs.
      - supervision_watchdog_timeout: liveness stream watchdog over slow-but-healthy ops
        (e.g. a cold sandbox build), so a blocked-not-dead worker is not reaped.
    """
    try:
        from monarch.config import configure
    except Exception:
        return
    kwargs = {
        "message_delivery_timeout": os.environ.get(
            "MONARCH_MESSAGE_DELIVERY_TIMEOUT", "300s"
        ),
        "host_spawn_ready_timeout": os.environ.get(
            "MONARCH_HOST_SPAWN_READY_TIMEOUT", "180s"
        ),
        "supervision_watchdog_timeout": os.environ.get(
            "MONARCH_SUPERVISION_WATCHDOG_TIMEOUT", "600s"
        ),
    }
    try:
        configure(**kwargs)
        logger.info("Monarch runtime timeouts configured: %s", kwargs)
    except Exception as e:
        # A Monarch API change here must never block training; the defaults only add
        # robustness. Log and continue with Monarch's own defaults.
        logger.warning("configure_monarch skipped (%s): %s", type(e).__name__, e)


# A run that is killed from outside currently looks exactly like a run that
# finished: the controller catches the interrupt, closes cleanly, and exits 0, so
# systemd's Restart=on-failure never fires and nothing records who sent the
# signal. The two helpers below fix both halves of that.
_FORENSIC_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def _describe_sender(pid: int) -> str:
    """Best-effort identity of whoever sent us a signal.

    The sender is usually already gone -- the shell behind a `pkill` exits
    immediately -- so every read here is allowed to fail; the bare pid is still
    worth having.
    """
    if pid <= 0:
        return "pid=0 (kernel, or a sender the kernel did not name)"
    bits = [f"pid={pid}"]
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        bits.append(f"cmd={cmd or '<exited>'}")
    except OSError:
        bits.append("cmd=<exited>")
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith(("Uid:", "PPid:", "Name:")):
                    k, v = line.split(":", 1)
                    bits.append(
                        f"{k.lower()}={v.split()[0] if k != 'Name' else v.strip()}"
                    )
    except OSError:
        pass
    return " ".join(bits)


def _install_signal_forensics(on_signal) -> None:
    """Record the sender of SIGINT/SIGTERM, then start a graceful shutdown.

    A Python signal handler is called with (signum, frame) and never learns who
    signalled us. ``sigwaitinfo`` does carry si_pid, but only delivers signals
    that are blocked, so the mask has to go up first -- and a blocked mask is
    inherited across both fork and exec. Call this only AFTER the actor procs
    have been spawned, or every child inherits a SIGTERM it cannot be stopped
    with.
    """
    signal.pthread_sigmask(signal.SIG_BLOCK, _FORENSIC_SIGNALS)
    # Anything we fork from here (vLLM engine procs, dataloader workers) gets the
    # default disposition back.
    os.register_at_fork(
        after_in_child=lambda: signal.pthread_sigmask(
            signal.SIG_UNBLOCK, _FORENSIC_SIGNALS
        )
    )

    def _wait() -> None:
        while True:
            try:
                info = signal.sigwaitinfo(_FORENSIC_SIGNALS)
            except InterruptedError:
                continue
            name = signal.Signals(info.si_signo).name
            who = _describe_sender(info.si_pid)
            logger.error("KILLED BY %s sent from %s", name, who)
            on_signal(info.si_signo, who)
            return

    threading.Thread(target=_wait, name="signal-forensics", daemon=True).start()


async def main():
    config = ConfigManager().parse_args()
    assert isinstance(config, Controller.Config)
    _configure_monarch_runtime()
    sl.init_structured_logger(
        source="rl_controller",
        output_dir=config.dump_folder,
        rank=0,
        enable=config.trainer.debug.enable_structured_logging,
    )
    sl.log_trace_instant("structured_logger_started")

    rl_trainer: Controller = config.build()
    killed: dict = {}
    try:
        trainer_world_size = _compute_trainer_world_size(config.trainer.parallelism)
        per_generator_world_size = _compute_generator_world_size(
            config.generator.parallelism
        )
        trainer_mesh, generator_meshes, eval_generator_meshes = spawn_proc_mesh(
            trainer_world_size,
            per_generator_world_size,
            host_meshes=None,
            num_generators=config.num_generators,
            num_eval_generators=config.num_eval_generators,
            per_eval_generator_world_size=_compute_generator_world_size(
                config.eval_generator_parallelism()
            ),
        )
        # The child procs exist now, so blocking these signals here no longer
        # leaks a mask into them.
        loop = asyncio.get_running_loop()
        this_task = asyncio.current_task()
        try:
            _install_signal_forensics(
                lambda signo, who: (
                    killed.update(signo=signo, who=who),
                    loop.call_soon_threadsafe(this_task.cancel),
                )
            )
        except Exception as e:  # never let forensics cost us a run
            logger.warning("signal forensics unavailable (%s): %s", type(e).__name__, e)
        await rl_trainer.setup_async(
            trainer_mesh=trainer_mesh,
            generator_meshes=generator_meshes,
            eval_generator_meshes=eval_generator_meshes,
        )
        await rl_trainer.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interrupted; attempting graceful shutdown...")
    finally:
        await rl_trainer.close()

    if killed:
        # Exit non-zero so being killed is distinguishable from finishing:
        # Restart=on-failure cannot bring back a run that exits 0. A deliberate
        # `systemctl stop` still will not be restarted -- systemd suppresses that
        # regardless of the exit status.
        logger.error(
            "Exiting %d after %s from %s",
            128 + killed["signo"],
            signal.Signals(killed["signo"]).name,
            killed["who"],
        )
        raise SystemExit(128 + killed["signo"])


if __name__ == "__main__":
    asyncio.run(main())
