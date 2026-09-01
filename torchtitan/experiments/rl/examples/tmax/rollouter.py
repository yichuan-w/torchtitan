# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Terminal-agent rollouter for AI2 tmax tasks.

Forked from ``swe_r2e/rollouter.py`` (same shape: open an adapter session, boot a
fresh sandbox, run a host-side ReAct loop against the on-box adapter, then grade
and stamp the reward) but drives the FAITHFUL Vanillux agent loop
(``run_vanillux_loop``) rather than the swe_r2e host_loop. The tmax Qwen3.5-9B is
SFT'd under ``SWERLVanilluxSandboxEnv`` (single ``bash`` tool, vanillux prompts,
persistent shell, ``echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`` to submit); the
swe_r2e host_loop's Bash/Read/Write/Edit tool set + SWE system prompt would put the
policy off-distribution, so we reproduce the tmax scaffold exactly while keeping the
host_loop transport (agent brain on the controller, each bash action dispatched to
the Daytona sandbox via ``sb.exec``). tmax specifics:

  1. Run the agent as ROOT. tmax tasks write to system paths (``/logs``,
     ``/output``, ``/home/user``, ``/app``). ``_RootSandbox`` forces every sandbox
     call to ``user="root"``.
  2. Honor the per-task ``workdir`` (best-guessed at data-prep time; /home/user,
     /app, or /workspace).
  3. Grade in place on the SUBMIT MARKER only: the tmax verifier inspects the
     container's live filesystem, so ``grade_tmax`` uploads the verifier into the
     agent's own sandbox and runs ``bash /tests/test.sh``. A rollout that never
     submits scores 0 (matches the env: tests run only on submit).

The standard scoring + advantage path is unchanged: the grade is stamped on the
last turn's ``env_rewards`` (key ``tmax_reward``) and read back by ``RewardTMax``.

Knobs read from env (the launcher sets these; see ``submit_swe_tmax_9b.sh``):
  ``SHIM_BIND_HOST`` / ``SHIM_PORT``  adapter bind address (default 127.0.0.1:18001)
  ``SWE_TIME_BUDGET_SEC``             per-agent wallclock (default 2400)
  ``TMAX_EVAL_TIMEOUT_SEC``           verifier (test.sh) run timeout, applied to
                                      TRAINING and validation rollouts alike
                                      (default 600)
  ``SWE_MAX_CONTEXT_LEN``             model context budget for the adapter session
  ``SWE_ROLLOUT_CONCURRENCY``         concurrently-active rollouts (default 16)
  ``TMAX_CALL_LIMIT``                 Vanillux step cap (its per-turn token cap is a
                                      constant in vanillux_loop, not an env knob)
  ``TMAX_TURN_MAX_TOKENS``            per-turn generation cap, but only where a
                                      recipe reads it into the generator's
                                      SamplingConfig -- that config is what bounds
                                      generation, NOT the ``max_tokens`` a harness
                                      puts in the request body, which the adapter
                                      ignores
  ``TMAX_CTRF_DIAGNOSTICS``           read the verifier's per-test CTRF report for
                                      metrics ONLY (default off; costs one extra
                                      sandbox read per graded rollout and never
                                      moves reward)
  ``SWE_REWARD_DENSE``                NOT read here: config_registry maps =1 to
                                      ``Config.reward_mode="dense"``, which reads
                                      that same report AS the reward. It travels as
                                      a config field so a run records what its
                                      reward meant.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import logging
import math
import os
import shlex
import statistics
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from renderers import Renderer

from torchtitan.experiments.rl.environment import TokenEnv
from torchtitan.experiments.rl.examples.tmax.data import TMaxDataset, TMaxSample
from torchtitan.experiments.rl.examples.tmax.env import TMaxEnv
from torchtitan.experiments.rl.examples.tmax.grading import (
    ctrf_pass_fraction,
    grade_tmax,
    read_ctrf_report,
    seed_workspace,
)
from torchtitan.experiments.rl.examples.tmax.rubric import RewardTMax, TMAX_REWARD_KEY
from torchtitan.experiments.rl.examples.tmax.vanillux_loop import (  # noqa: F401 -- registers the default agent
    vanillux_agent,
)
from torchtitan.experiments.rl.harness import (
    AgentTask,
    AnthropicAdapter,
    boot_agent_sandbox,
    get_agent,
    Sandbox,
    SandboxIssue,
    SandboxIssueTracker,
    SandboxLogContext,
)
from torchtitan.experiments.rl.observability import metrics as m
from torchtitan.experiments.rl.rollout.advantage import AdvantageEstimator
from torchtitan.experiments.rl.rollout.rollouter import Rollouter
from torchtitan.experiments.rl.rollout.types import (
    GenerateFn,
    is_scored,
    Rollout,
    RolloutGroup,
    RolloutStatus,
    RolloutTurn,
)
from torchtitan.experiments.rl.rubrics import Rubric
from torchtitan.experiments.rl.types import RolloutTurnID

if TYPE_CHECKING:
    # Type-only: importing the generator module pulls in vLLM at import time.
    from torchtitan.experiments.rl.actors.generator import SamplingConfig

logger = logging.getLogger(__name__)


_FINISH_REASONS = (
    "submit",
    "hit_max_turns",
    "hit_time_budget",
    # The history filled the model's context before the agent was done. Distinct
    # from hit_max_turns (it had episodes left) and from error (nothing broke).
    "hit_context_limit",
    "stopped_early",
    "error",
)

# See TMaxRollouter.Config.reward_mode.
_REWARD_MODES = frozenset({"sparse", "dense"})

# Canonical RTS check names (``test_check_01_required_evidence`` and its shorter
# variants), matched as substrings of the CTRF test names. They separate "wrong
# answer" (final_semantics) from "right answer via a shortcut" (no_shortcut), which
# the binary reward cannot. Corpora that do not use this template (e.g. TB-2.0)
# simply match nothing and the per-check metrics drop out.
_CTRF_CHECK_KEYS = (
    "required_evidence",
    "intermediate_artifact",
    "final_semantics",
    "no_shortcut",
)

_DISK_ISSUE_KINDS = {
    "command_disk_exhausted",
    "session_disk_exhausted",
}
_TRANSPORT_ISSUE_KINDS = {
    "command_logs_failed",
    "command_logs_missing",
    "command_logs_retry",
    "command_output_failed",
    "command_output_missing",
    "command_output_retry",
    "command_recovery_query_failed",
    "command_status_fallback",
    "command_status_invalid",
    "command_status_query_failed",
    "command_status_timeout",
    "delete_failed",
    "delete_retry",
    "exec_failed",
    "execute_missing_command_id",
    "execute_response_recovered",
    "execute_response_unconfirmed",
    "file_upload_failed",
    "file_upload_retry",
    "heartbeat_retry",
    "poll_transient",
    "sandbox_lost",
    "session_cleanup_failed",
}
_PROVISION_ISSUE_KINDS = {
    "create_failed",
    "create_retry",
    "provision_failed",
    "provision_retry",
    "session_create_failed",
    "session_create_retry",
}
_TIMEOUT_ISSUE_KINDS = {
    "command_status_timeout",
    "command_timeout",
}


@dataclass(slots=True)
class _SandboxRolloutDiagnostics:
    sandbox_id: str
    disk_gb: int | None
    issue_counts: dict[str, int]
    issues: tuple[SandboxIssue, ...]
    num_dropped_details: int
    infra_failed: bool = False


def _sandbox_issue_metrics(
    issue_counts_by_rollout: list[Mapping[str, int]],
) -> list[m.Metric]:
    """Summarize bounded sandbox issue categories for one sibling group."""
    assert issue_counts_by_rollout, "a TMax rollout group must contain siblings"
    num_rollouts = len(issue_counts_by_rollout)

    def _num_events(counts: Mapping[str, int], kinds: set[str] | None = None) -> int:
        if kinds is None:
            return sum(counts.values())
        return sum(counts.get(kind, 0) for kind in kinds)

    def _frac(kinds: set[str] | None = None) -> float:
        return (
            sum(
                1.0
                for counts in issue_counts_by_rollout
                if _num_events(counts, kinds) > 0
            )
            / num_rollouts
        )

    def _events_mean(kinds: set[str] | None = None) -> float:
        return (
            sum(_num_events(counts, kinds) for counts in issue_counts_by_rollout)
            / num_rollouts
        )

    return [
        m.Metric("rollout/sandbox_issue_frac", m.Mean(_frac())),
        m.Metric("rollout/sandbox_issue_events_mean", m.Mean(_events_mean())),
        m.Metric("rollout/sandbox_disk_full_frac", m.Mean(_frac(_DISK_ISSUE_KINDS))),
        m.Metric(
            "rollout/sandbox_disk_full_events_mean",
            m.Mean(_events_mean(_DISK_ISSUE_KINDS)),
        ),
        m.Metric(
            "rollout/sandbox_transport_issue_frac",
            m.Mean(_frac(_TRANSPORT_ISSUE_KINDS)),
        ),
        m.Metric(
            "rollout/sandbox_transport_issue_events_mean",
            m.Mean(_events_mean(_TRANSPORT_ISSUE_KINDS)),
        ),
        m.Metric(
            "rollout/sandbox_provision_issue_frac",
            m.Mean(_frac(_PROVISION_ISSUE_KINDS)),
        ),
        m.Metric("rollout/sandbox_timeout_frac", m.Mean(_frac(_TIMEOUT_ISSUE_KINDS))),
    ]


def _finish_reason_metrics(finish_reasons: list[str]) -> list[m.Metric]:
    """Return exhaustive per-reason fractions for one completed rollout group."""
    assert finish_reasons, "a TMax rollout group must contain at least one sibling"
    unexpected = set(finish_reasons).difference(_FINISH_REASONS)
    assert not unexpected, f"unexpected TMax finish reasons: {sorted(unexpected)}"

    num_rollouts = len(finish_reasons)
    return [
        m.Metric(
            f"rollout/finish_{reason}_frac",
            m.Mean(
                sum(1.0 for finish_reason in finish_reasons if finish_reason == reason)
                / num_rollouts
            ),
        )
        for reason in _FINISH_REASONS
    ]


def _ctrf_metrics(reports: list[dict | None]) -> list[m.Metric]:
    """Summarize the verifier's per-test CTRF reports for one sibling group.

    The reward is binary (all tests pass), so these say WHY a zero happened: the
    test-level pass fraction plus which canonical check failed. Fractions are over
    the siblings that produced a report; with none, the empty means are NaN and the
    metrics aggregator drops them.
    """
    assert reports, "a TMax rollout group must contain at least one sibling"
    present = [report for report in reports if report]
    metrics = [
        m.Metric("rollout/ctrf_report_frac", m.Mean(len(present) / len(reports))),
        m.Metric(
            "rollout/ctrf_test_pass_frac",
            m.Mean.from_list(
                [
                    fraction
                    for fraction in (ctrf_pass_fraction(report) for report in present)
                    if fraction is not None
                ]
            ),
        ),
    ]
    metrics += [
        m.Metric(
            f"rollout/ctrf_check_{key}_fail_frac",
            m.Mean.from_list(
                [
                    float(any(key in name for name in report["failed"]))
                    for report in present
                ]
            ),
        )
        for key in _CTRF_CHECK_KEYS
    ]
    return metrics


class _RootSandbox:
    """Sandbox wrapper that forces every operation to run as ``root``.

    tmax tasks need root to touch system paths (``/logs``, ``/output``, ``/app``).
    This delegates to the underlying sandbox with the requested ``user`` overridden
    to ``root``, so ``run_vanillux_loop`` (and ``grade_tmax``) run entirely as root.
    """

    def __init__(self, inner: Sandbox) -> None:
        self._inner = inner

    @property
    def sandbox_id(self) -> str:
        return self._inner.sandbox_id

    @property
    def allocated_disk_gb(self) -> int | None:
        return self._inner.allocated_disk_gb

    @property
    def issue_tracker(self) -> SandboxIssueTracker:
        return self._inner.issue_tracker

    async def exec(self, cmd: str, *, user: str = "root", **kwargs):
        return await self._inner.exec(cmd, user="root", **kwargs)

    async def write_file(self, sandbox_path: str, content, *, user: str = "root"):
        return await self._inner.write_file(sandbox_path, content, user="root")

    async def read_file(self, sandbox_path: str, *, user: str = "root") -> str:
        return await self._inner.read_file(sandbox_path, user="root")


# Floor applied ONLY to a task's own declared agent budget (Harbor
# ``[agent].timeout_sec``). Today that means the TB-2.0 eval set --
# ``prepare_tb2_data`` extracts the declaration; RTS/TerminalWorld training rows carry
# none and keep the configured ``time_budget_sec``, so training throughput stays under
# launcher control while eval gets the wall clock its tasks actually need.
#
# A declared budget is raised to the floor, never lowered: TB-2.0 declares 900s for 49
# of its 89 tasks, which is nowhere near what a 120-turn Terminus-2 episode needs in a
# 2 vCPU sandbox. Measured over a full step-0 pass, only ~2% of the wall clock is token
# generation (11,927 completion tokens at ~200 tok/s = 60s); ~98% is in-sandbox command
# execution -- apt, pip, pytest, tmux round trips.
_DECLARED_AGENT_BUDGET_FLOOR_SEC = int(
    os.environ.get("SWE_AGENT_TIMEOUT_FLOOR_SEC", "7200")
)

# Seconds to let an ENTRYPOINT reach a state the agent can use. It is a startup
# script, not a health-checked service: the ones in these corpora bind a local port
# or append to /etc/hosts and are ready well inside this, and there is no generic
# readiness signal to wait on instead.
_ENTRYPOINT_SETTLE_SEC = 2.0
_ENTRYPOINT_LOG = "/tmp/.titan_entrypoint.log"


def _note_image_without_tmux(sample) -> None:
    """Record an image that reached a running sandbox without tmux in it.

    The injected preinstall ends in `|| echo ... (non-fatal)`, so a build where it
    failed is indistinguishable from one where it worked, and the consequence shows
    up much later and somewhere else: Terminus self-installs at session bring-up,
    and on an image whose package lists were cleaned and which carries no compiler
    it cannot, so every rollout in the group ends having taken zero turns.

    Making the preinstall strict again is the real fix and is already a TODO in
    prepare_rts_data -- but strict turns these into build failures, which cost a
    Daytona create each and are retried, which is the problem that made it
    non-fatal in the first place. So neither blocks nor guesses here: one cheap
    exec turns "this image has no tmux" into a fact recorded against the task,
    available after one rollout instead of after a whole group has died.

    **This list is a warning, not a verdict, and must not be used to mask the
    pool.** Terminus usually installs tmux successfully at runtime; an image
    without it is only fatal when the runtime install ALSO fails, which needs
    both no package and no compiler. Measured on the first pass: 78 tasks landed
    here while `infra_quarantine/` -- the list of groups that actually died at
    zero turns -- held none of them. Filtering on this one would drop 78 healthy
    tasks. `infra_quarantine/` is the list with a verdict behind it; this one
    says where to look first when a group does die.
    """
    root = os.environ.get("SWE_TASK_EVOLUTION_DIR", "")
    if not root:
        return
    try:
        d = os.path.join(os.path.dirname(root), "no_tmux")
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, f"{sample.instance_id.replace('/', '_')}.json")
        seen = 0
        if os.path.exists(f):
            with open(f) as fh:
                seen = int(json.load(fh).get("occurrences", 0))
        with open(f, "w") as fh:
            json.dump(
                {
                    "instance_id": sample.instance_id,
                    "image": sample.image,
                    "occurrences": seen + 1,
                    "last_seen": time.time(),
                },
                fh,
            )
    except Exception as e:  # noqa: BLE001 -- bookkeeping must not kill a rollout
        logger.warning(f"[tmax] no-tmux record failed for {sample.instance_id}: {e}")


async def _start_entrypoint(sb: Sandbox, command: str, *, workdir: str) -> None:
    """Start the image ENTRYPOINT in the background, as PID 1 would.

    Detached (``setsid``, every fd redirected) so it outlives this exec instead of
    holding it open: the command is typically a script whose tail is ``exec "$@"``,
    which never returns. Best-effort -- a task whose ENTRYPOINT fails scores what it
    scores, and the oracle-validation pass over the corpus already measures that.
    """
    rc, _out, err = await sb.exec(
        f"cd {shlex.quote(workdir)} 2>/dev/null || cd /; "
        f"setsid nohup {command} > {_ENTRYPOINT_LOG} 2>&1 < /dev/null &",
        user="root",
        check=False,
        timeout=120,
    )
    if rc != 0:
        logger.warning(
            f"[tmax] entrypoint start returned {rc}: {command[:200]!r} "
            f"stderr={(err or '')[-200:]!r}"
        )
    await asyncio.sleep(_ENTRYPOINT_SETTLE_SEC)


class _RolloutIssueGate:
    """Work-conserving async gate for sibling rollouts in one worker process.

    Every sibling reserves one slot. Waiters are admitted by ascending
    ``(group_id, rollout_idx)`` priority, so lower-id prompt groups get capacity first,
    but any completed sibling immediately hands its slot to the next waiter. A slow
    sibling therefore occupies only its own slot and cannot leave the worker's other
    slots idle while the gate waits to reassemble a full group.

    This matches open-instruct's environment pool at the relevant boundary: an
    environment is acquired and released per rollout, while prompt-group aggregation
    separately waits for all siblings. The gate is per worker process, not global
    across the RolloutWorker pool.

    Single-event-loop only (no threads): asyncio runs coroutine steps serially, so the
    counter/heap need no lock.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._available = capacity
        # Min-heap of (priority, tiebreak, future). tiebreak keeps the heap
        # totally ordered on equal priority (and never compares futures).
        self._waiters: list[tuple[tuple[int, int], int, asyncio.Future]] = []
        self._tiebreak = itertools.count()

    async def acquire_sibling(self, priority: tuple[int, int]) -> None:
        """Reserve one rollout slot, waiting behind lower-priority siblings."""
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (priority, next(self._tiebreak), fut))
        self._try_admit()  # may grant synchronously if this is now the head and fits
        try:
            await fut
        except asyncio.CancelledError:
            # Granted-then-cancelled: return the slot we were handed.
            # Pending-then-cancelled: the dead future is dropped by _try_admit's
            # done() check, so there is nothing to return.
            if fut.done() and not fut.cancelled():
                self.release()
            else:
                self._try_admit()
            raise

    def release(self) -> None:
        """Return one sibling slot and immediately admit the next waiter."""
        self._available += 1
        assert self._available <= self._capacity, "rollout gate released too many slots"
        self._try_admit()

    def _try_admit(self) -> None:
        while self._waiters and self._available:
            _, _, fut = heapq.heappop(self._waiters)
            if fut.done():  # cancelled/settled: drop and continue
                continue
            self._available -= 1
            fut.set_result(None)


class TMaxRollouter(Rollouter):
    """Drives a host-side ReAct agent (as root) in a sandbox per sibling, then runs
    the tmax verifier in that same sandbox."""

    @dataclass(kw_only=True, slots=True)
    class Config(Rollouter.Config):
        train_dataset: TMaxDataset.Config = field(
            default_factory=lambda: TMaxDataset.Config(seed=42)
        )
        validation_dataset: TMaxDataset.Config = field(
            default_factory=lambda: TMaxDataset.Config(seed=99, shuffle=False)
        )
        rubric: Rubric.Config = field(
            default_factory=lambda: Rubric.Config(
                reward_fns=[RewardTMax.Config(weight=1.0)],
                # RewardTMax returns zero when no verifier reward is present. Run it
                # for every status so failed rollouts remain in RewardTMax metrics.
            )
        )
        # Placeholder env (the agent loop runs in-sandbox; see env.py).
        message_env: TMaxEnv.Config = field(default_factory=TMaxEnv.Config)
        token_env: TokenEnv.Config = field(default_factory=TokenEnv.Config)
        # Centered (mean-baseline only), NOT std-normalized: matches the tmax
        # recipe's ``--advantage_normalization_type centered`` (qwen35_9b.sh).
        # Dividing by the group std amplifies rare-outcome advantages for
        # imbalanced binary-reward groups (e.g. a 30/32-pass group's 2 failures
        # get advantage ~ -3.9), which distorts the gradient and suppresses reward
        # growth; the recipe centers only to keep the advantage in [-1, 1].
        advantage: AdvantageEstimator.Config = field(
            default_factory=lambda: AdvantageEstimator.Config(
                should_std_normalize=False
            )
        )

        # Run knobs as CONFIG (not env), so they are serialized into the W&B run
        # config and each run's differences are visible. config_registry resolves
        # them from the launcher env once; the RolloutWorker pool overrides
        # rollout_concurrency to its per-worker share.
        rollout_concurrency: int = 16
        """Max concurrently-ACTIVE rollouts (per worker process). The pool total is
        num_rollout_workers x this. Each completed sibling immediately releases its
        slot to the next waiting rollout."""

        time_budget_sec: int = 2400
        """Per-rollout agent wall-clock budget (the vanillux loop stops after this)."""

        eval_timeout_sec: int = 600
        """Verifier (test.sh) run timeout."""

        reward_mode: str = "sparse"
        """Where a submitted rollout's reward comes from.

        ``sparse`` is the tmax/RTS verifier contract: the binary reward.txt, which is
        1 only when EVERY test passed. ``dense`` uses the same verifier run's CTRF
        per-test pass fraction instead, so a 3-of-4 rollout scores 0.75; tasks whose
        verifier writes no CTRF report keep their sparse value (counted in
        ``rollout/dense_fallback_frac``). Either way an unsubmitted rollout scores 0.

        Dense is not a free relabel. RTS partial credit is reachable without solving
        the task (``check_01`` only asserts an artifact exists), and it makes far
        fewer groups zero-std, so ``drop_zero_std_reward_groups`` keeps a different
        (larger) set of prompts -- a training-dynamics change, not just a rescale.
        """

        max_context_tokens: int = 32768
        """Model context budget for the adapter session."""

    def __init__(self, config: Config) -> None:
        # Before super(), which builds the datasets: a typo in a knob that changes
        # what reward means should fail on the spot, not behind a data error.
        if config.reward_mode not in _REWARD_MODES:
            raise ValueError(
                f"reward_mode must be one of {sorted(_REWARD_MODES)}, got "
                f"{config.reward_mode!r}"
            )
        super().__init__(config)
        # Which agent scaffold drives the rollout. Defaults to the vanillux loop the
        # tmax models are SFT'd under; TMAX_AGENT=terminus swaps in Terminus-2 (a
        # different output format -- see harness/agents/terminus.py).
        self._agent_name = os.environ.get("TMAX_AGENT", "vanillux")
        if self._agent_name != "vanillux":
            # Import for the side effect of registering; only the default is wired
            # in by the tmax module itself.
            import torchtitan.experiments.rl.harness.agents.terminus  # noqa: F401
        self._time_budget_sec = config.time_budget_sec
        self._eval_timeout_sec = config.eval_timeout_sec
        self._max_context_tokens = config.max_context_tokens
        self._reward_mode = config.reward_mode
        # The CTRF read is one extra sandbox exec per graded rollout, and the Daytona
        # API rate limit is the throughput ceiling at high rollout concurrency -- so
        # it is opt-in for metrics, and mandatory when it feeds the reward.
        self._read_ctrf = (
            self._reward_mode == "dense"
            or os.environ.get("TMAX_CTRF_DIAGNOSTICS", "0") == "1"
        )
        # Whole-rollout wall-clock guard: agent budget + eval + boot buffer.
        # Fallback guard; a rollout whose task declares its own budget derives its
        # own (see _agent_budget_sec / _guard_for).
        self._guard_sec = self._guard_for(self._time_budget_sec)
        # Per-worker rollout-issue gate (one rollouter per worker proc). Each sibling
        # holds one slot, matching open-instruct's per-rollout environment acquire and
        # release. Ordering is per worker. The controller pins group-loop lanes to
        # workers, and each gate prioritizes the groups claimed by its lanes.
        self._rollout_gate = _RolloutIssueGate(config.rollout_concurrency)
        self._adapter: AnthropicAdapter | None = None
        self._adapter_lock = asyncio.Lock()

    async def _ensure_adapter(self, renderer: Renderer) -> AnthropicAdapter:
        if self._adapter is None:
            async with self._adapter_lock:
                if self._adapter is None:
                    # Direct in-process use: build the adapter for its Anthropic
                    # translation + TITO turn capture, but do NOT start() an HTTP
                    # server -- the vanillux loop calls adapter.complete() directly
                    # (no loopback HTTP, no per-worker port).
                    self._adapter = AnthropicAdapter(renderer=renderer)
        return self._adapter

    async def run_group_rollouts(
        self,
        *,
        generate_fn: GenerateFn,
        sample: TMaxSample,
        group_id: int,
        group_size: int,
        sampling: "SamplingConfig",
        renderer: Renderer,
    ) -> RolloutGroup:
        """Run + grade one prompt group of terminal-agent rollouts."""
        adapter = await self._ensure_adapter(renderer)

        rollout_tasks = [
            asyncio.create_task(
                self._run_agent_rollout(
                    adapter=adapter,
                    generate_fn=generate_fn,
                    sample=sample,
                    group_id=group_id,
                    rollout_idx=i,
                    sampling=sampling,
                    renderer=renderer,
                ),
                name=f"tmax_rollout_{group_id}_{i}",
            )
            for i in range(group_size)
        ]
        try:
            results = await asyncio.gather(*rollout_tasks)
        except BaseException:
            # Parent cancellation already propagates through gather. A child exception
            # does not, so let the other rollouts finish. In both cases drain without a
            # second cancel, which could interrupt an in-progress sandbox teardown.
            await asyncio.gather(*rollout_tasks, return_exceptions=True)
            raise
        rollouts = [rollout for rollout, _, _, _, _ in results]
        submitted_flags = [submitted for _, submitted, _, _, _ in results]
        fmt_errors_list = [fmt for _, _, fmt, _, _ in results]
        finish_reasons = [fr for _, _, _, fr, _ in results]
        sandbox_diagnostics = [diagnostics for _, _, _, _, diagnostics in results]

        # Fraction of siblings that never emitted the submit marker (errored or ran to
        # the budget without submitting) -> the verifier never runs -> auto reward 0.
        # Mirrors open-instruct's val/non_submitting_completion_fraction; a high value
        # means the scaffold (not the task difficulty) is capping reward.
        nonsubmit_frac = 1.0 - sum(submitted_flags) / len(submitted_flags)
        # Format errors (malformed tool-calls) per rollout + fraction of rollouts that
        # hit any -- surfaces the vanillux tool-call parse-failure rate on wandb.
        fmt_errors_mean = sum(fmt_errors_list) / len(fmt_errors_list)
        fmt_error_frac = sum(1.0 for f in fmt_errors_list if f > 0) / len(
            fmt_errors_list
        )
        # How each sibling's loop ended (run_vanillux_loop finish_reason), as a
        # per-reason fraction of the group. Surfaces the stop-reason split on wandb:
        # submit vs turn-cap vs 40min time-budget wall vs early-stop vs rollout error
        # (the "error" bucket covers timeout/exception paths where the loop never
        # returned). The five fracs sum to 1.0 per group; cudagraph should push
        # time_budget down and submit up.
        finish_metrics = _finish_reason_metrics(finish_reasons)
        sandbox_metrics = _sandbox_issue_metrics(
            [diagnostics.issue_counts for diagnostics in sandbox_diagnostics]
        )
        infra_failed_flags = [
            diagnostics.infra_failed for diagnostics in sandbox_diagnostics
        ]
        infra_failed_frac = sum(infra_failed_flags) / len(infra_failed_flags)
        group_metrics = [
            m.Metric("rollout/nonsubmit_frac", m.Mean(nonsubmit_frac)),
            m.Metric("rollout/format_errors_mean", m.Mean(fmt_errors_mean)),
            m.Metric("rollout/format_error_frac", m.Mean(fmt_error_frac)),
            m.Metric("rollout/infra_failed_frac", m.Mean(infra_failed_frac)),
            m.Metric(
                "rollout/infra_failed_group_frac",
                m.Mean(1.0 if any(infra_failed_flags) else 0.0),
            ),
            *finish_metrics,
            *sandbox_metrics,
        ]
        if self._read_ctrf:
            group_metrics += _ctrf_metrics(
                [rollout.diagnostics.get("ctrf") for rollout in rollouts]
            )
        if self._reward_mode == "dense":
            # Keep the binary reward visible while dense drives training, so the two
            # curves are comparable within one run, and surface how often the CTRF
            # report was missing (those rollouts silently keep the sparse value).
            group_metrics += [
                m.Metric(
                    "rollout/sparse_reward_mean",
                    m.Mean.from_list(
                        [rollout.diagnostics["sparse_reward"] for rollout in rollouts]
                    ),
                ),
                m.Metric(
                    "rollout/dense_fallback_frac",
                    m.Mean.from_list(
                        [
                            float(rollout.diagnostics["dense_fallback"])
                            for rollout in rollouts
                        ]
                    ),
                ),
            ]

        # Standard scoring + advantage path (mirrors Rollouter.run_group_rollouts).
        outputs = await self.score_group(rollouts, sample)
        for rollout, output in zip(rollouts, outputs, strict=True):
            rollout.reward = output.reward
            rollout.reward_breakdown = output.reward_breakdown

        # An infrastructure failure is not a verdict on the policy, so it must not
        # become one. This used to hold the sibling at reward 0 to match
        # Open-Instruct, reasoning that a failed rollout has no completion and so no
        # training tokens. That holds for a sandbox that never booted and not for the
        # timeouts that dominate: across a 445-trial pass every one of the 30 infra
        # failures carried tokens, a median of 62 turns and 45k completion tokens.
        # Centered advantage then gives those turns 0 minus the group mean -- a
        # negative advantage training the policy away from behavior nothing
        # established was wrong -- and drags the baseline down for its siblings.
        #
        # NaN is "no verdict", distinct from 0.0 = "verdict: failed"; the advantage
        # estimator and the sample builder both drop it before computing any group
        # statistic, so a group of 8 with one infra failure baselines over the
        # surviving 7. Validation deliberately keeps 0.0: avg@k is defined over
        # attempts, so a NaN there would move the denominator and stop the number
        # being comparable to the published one (and index.json cannot encode it).
        if group_id >= 0 and any(infra_failed_flags):
            for rollout, infra_failed in zip(rollouts, infra_failed_flags, strict=True):
                if infra_failed:
                    rollout.reward = math.nan
            logger.warning(
                f"[tmax] group={group_id}: "
                f"{sum(infra_failed_flags)}/{len(infra_failed_flags)} "
                f"infrastructure failures excluded from the advantage baseline"
            )

        # Group reward-shape metrics -- also exactly what online evolution acts on: a
        # zero-variance group produces no gradient and is the one re-tuned, 0/k ("too
        # hard") made easier and k/k ("too easy") made harder. Logging the split here
        # puts the evolve loop's input rate on the training wandb (frac of groups per
        # step, so frac * groups-per-step = count). Filter to scored rollouts
        # (is_scored) so an infra-failed sibling's NaN reward is excluded -- statistics
        # .pstdev raises on NaN under Python 3.12. Training groups only (group_id >= 0).
        if group_id >= 0:
            ev_rewards = [r.reward for r in rollouts if is_scored(r)]
            ev_zero_std = len(ev_rewards) >= 2 and statistics.pstdev(ev_rewards) == 0.0
            ev_all_pass = ev_zero_std and ev_rewards[0] > 0
            ev_all_fail = ev_zero_std and not ev_all_pass
            group_metrics += [
                m.Metric("rollout/zero_std_group_frac", m.Mean(float(ev_zero_std))),
                m.Metric("rollout/all_fail_group_frac", m.Mean(float(ev_all_fail))),
                m.Metric("rollout/all_pass_group_frac", m.Mean(float(ev_all_pass))),
            ]

        group = RolloutGroup(
            group_id=group_id,
            rollouts=rollouts,
            metrics=group_metrics,
        )
        advantages = self.advantage_estimator(group)
        for rollout, advantage in zip(group.rollouts, advantages, strict=True):
            rollout.advantage = advantage
        self._maybe_annotate_zero_std(sample, rollouts)
        self._maybe_emit_evolution_signal(sample, rollouts, renderer)
        return group

    def _maybe_annotate_zero_std(
        self, sample: TMaxSample, rollouts: list[Rollout]
    ) -> None:
        """Record this prompt's ``instance_id`` under ``SWE_ZERO_STD_DIR`` when its
        group has zero reward variance (all-pass or all-fail = no learning signal, so
        it is dropped by ``drop_zero_std_reward_groups``). A later run points
        ``TMaxDataset.skip_ids_path`` at the same dir to stop sampling these prompts.

        Writes ONE small file per prompt (``<instance_id>.json``), mirroring the
        rollout-trace dump: a write-once-per-file pattern is the one that persists on
        the manifold (oilfs) FUSE mount and is safe across the pooled RolloutWorker
        processes -- unlike appending to a single shared file, which object-store FUSE
        does not handle reliably. Re-encountering a prompt just overwrites its file
        (natural dedup). Best-effort; never raises into the rollout.
        """
        dump_dir = os.environ.get("SWE_ZERO_STD_DIR", "")
        if not dump_dir:
            return
        # Validation groups carry negative ids (see Controller). Their prompts are a
        # held-out or benchmark set, never sampled for training, so annotating them
        # would only pollute the skip list a later training run reads.
        if rollouts and rollouts[0].group_id < 0:
            return
        # An infrastructure failure carries NaN to mean "no verdict". Match the
        # advantage estimator and sample builder by taking variance only over the
        # scored survivors; statistics.pstdev raises on NaN under Python 3.12.
        rewards = [r.reward for r in rollouts if is_scored(r)]
        if len(rewards) < 2 or statistics.pstdev(rewards) != 0.0:
            return
        try:
            os.makedirs(dump_dir, exist_ok=True)
            safe = sample.instance_id.replace("/", "_")
            with open(os.path.join(dump_dir, f"{safe}.json"), "w") as f:
                json.dump({"instance_id": sample.instance_id, "reward": rewards[0]}, f)
        except OSError as e:
            logger.warning(f"[tmax] zero-std annotate failed for {dump_dir}: {e}")

    def _guard_for(self, budget_sec: int) -> int:
        """Whole-rollout wall-clock guard for an agent budget: + verifier + boot."""
        return budget_sec + self._eval_timeout_sec + 300

    # Extra initial guard headroom for the sandbox-creation queue. After a restart
    # every rollout boots a sandbox at once and the creation queue runs tens of
    # minutes deep; measured on the 04:05 08-29 boot, 931 of 1008 rollouts burned
    # their entire guard in that queue and died with turns=0 at secs=2701. The
    # guard is rescheduled back down to _guard_for(budget) the moment the sandbox
    # is up, so this allowance never extends a rollout that actually started.
    _SANDBOX_BOOT_ALLOWANCE_SEC: int = int(
        os.environ.get("SWE_SANDBOX_BOOT_ALLOWANCE_SEC", "2700")
    )

    def _agent_budget_sec(self, sample: TMaxSample) -> int:
        """Wall-clock budget for this task's agent, in seconds.

        A task that declares its own budget (TB-2.0) gets
        ``max(declared, _DECLARED_AGENT_BUDGET_FLOOR_SEC)`` -- the floor only ever
        raises, so a task asking for more keeps its own value. The declared numbers are
        sized for a human or a fast local runner, not for a 120-turn Terminus-2 episode
        whose wall clock is ~98% in-sandbox command execution.

        A task that declares nothing (RTS, TerminalWorld) gets the configured
        ``time_budget_sec`` unchanged: training throughput is a launcher decision, and
        raising it there would triple the wall clock of every rollout that currently
        stops at the budget (47% of them, measured on a TB-2.0 pass).
        """
        declared = sample.agent_timeout_sec
        if declared is None:
            return self._time_budget_sec
        return max(int(declared), _DECLARED_AGENT_BUDGET_FLOOR_SEC)
    def _maybe_emit_evolution_signal(
        self, sample: TMaxSample, rollouts: list[Rollout], renderer=None
    ) -> None:
        """Hand a no-signal group to a data-side task factory to be re-tuned to the
        policy, instead of shed. This is the online half of recursive task synthesis,
        and it is a first-class step, not a rider on the drop annotation: every group
        the policy has moved past -- all-fail (0/k) or all-pass (k/k) -- is evolved,
        0/k made easier and k/k made harder, and returned to the pool. Both directions
        always; a group with any reward variance is already producing signal and is
        left untouched.

        Independent of ``_maybe_annotate_zero_std``: dropping sheds the prompt, this
        re-tunes it, and a run enables either or both via its own env var. Same
        write-once-per-file pattern (safe on the oilfs FUSE mount and across pooled
        RolloutWorker processes). Best-effort; never raises into the rollout.
        """
        dump_dir = os.environ.get("SWE_TASK_EVOLUTION_DIR", "")
        if not dump_dir:
            return
        if rollouts and rollouts[0].group_id < 0:  # validation prompts, never trained
            return
        # is_scored (not "is not None"): an infra-failed sibling carries a NaN
        # reward, and statistics.pstdev raises on NaN under Python 3.12. Matches
        # _maybe_annotate_zero_std, so the same groups are annotated and evolved.
        try:
            rewards = [r.reward for r in rollouts if is_scored(r)]
            if len(rewards) < 2 or statistics.pstdev(rewards) != 0.0:
                return
            # An all-fail group in which no attempt ever took a turn is an
            # infrastructure failure (agent import error, sandbox never up),
            # not a verdict on the task -- emitting it would drive an unearned
            # simplify. One real turn anywhere is enough to call it measured.
            if rewards[0] == 0 and not any(len(r.turns) for r in rollouts):
                logger.warning(
                    f"[tmax] evolution signal suppressed for "
                    f"{sample.instance_id}: all-fail group with zero turns"
                )
                # Suppressing the signal is right -- this measured the
                # infrastructure, not the task -- but returning here used to throw
                # the finding away with it. The task stays in the pool and destroys
                # a whole group every time it is drawn, and the only trace is one
                # warning in a log nobody greps. Record it where a filter can read
                # it: one file per task, rewritten each time, so the count says how
                # often it has happened and the pool can be masked against the
                # directory listing.
                try:
                    qdir = os.path.join(os.path.dirname(dump_dir), "infra_quarantine")
                    os.makedirs(qdir, exist_ok=True)
                    qf = os.path.join(qdir, f"{sample.instance_id.replace('/', '_')}.json")
                    seen = 0
                    if os.path.exists(qf):
                        with open(qf) as fh:
                            seen = int(json.load(fh).get("occurrences", 0))
                    with open(qf, "w") as fh:
                        json.dump(
                            {
                                "instance_id": sample.instance_id,
                                "reason": "all_fail_zero_turns",
                                "occurrences": seen + 1,
                                "rollouts_lost": seen * len(rollouts) + len(rollouts),
                                "image": sample.image,
                                "last_seen": time.time(),
                            },
                            fh,
                        )
                except Exception as e:  # noqa: BLE001 -- never kill a rollout over bookkeeping
                    logger.warning(f"[tmax] infra quarantine write failed: {e}")
                return
            os.makedirs(dump_dir, exist_ok=True)
            safe = sample.instance_id.replace("/", "_")
            occurrence = (
                sample.lineage.occurrence_id
                if sample.lineage is not None
                else f"group-{rollouts[0].group_id if rollouts else 'unknown'}"
            )
            safe_occurrence = occurrence.replace("/", "_").replace(":", "_")
            # One immutable file per occurrence. A per-task filename silently
            # overwrote an earlier pending signal when the same task repeated.
            with open(
                os.path.join(dump_dir, f"{safe}--{safe_occurrence}.json"), "x"
            ) as f:
                json.dump(
                    self._evolution_signal(sample, rollouts, rewards, renderer), f
                )
        except Exception as e:  # noqa: BLE001 -- token decode etc. must not kill rollout
            logger.warning(
                f"[tmax] evolution signal failed for {sample.instance_id}: {e}"
            )

    @staticmethod
    def _message_text(message: object) -> str:
        """Flatten a chat message to the text a human reading the trajectory sees."""
        if isinstance(message, str):
            return message
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                return "\n".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return str(content or "")
        return "" if message is None else str(message)

    def _rollout_transcript(self, rollout, tokenizer) -> list[dict]:
        """Per-turn {cmd, out} decoded from TOKENS, not from parsed Messages.

        The terminus agent carries its command in ``tool_calls`` and leaves
        ``completion_message.content`` empty, so ``_message_text`` recovered
        nothing (every cmd/out was ""). The reliable text is the token stream:
        a turn's completion is the model's action (``<tool_call>`` and all), and
        the tool_response is the next turn's prompt delta -- the same TITO
        reconstruction the SWE_ROLLOUT_DUMP_DIR trace uses. Bounded per turn so
        one pathological turn cannot bloat the signal. Best-effort: on any
        failure the caller falls back to the message-parse path.
        """
        def dec(ids):
            if tokenizer is None or not ids:
                return ""
            return tokenizer.decode(list(ids), skip_special_tokens=False)[:4000]

        turns = rollout.turns
        out = []
        for i, tn in enumerate(turns):
            cmd = dec(getattr(tn, "completion_token_ids", None))
            resp = ""
            if i + 1 < len(turns):
                cur = list(getattr(tn, "prompt_token_ids", []) or []) + \
                    list(getattr(tn, "completion_token_ids", []) or [])
                nxt = list(getattr(turns[i + 1], "prompt_token_ids", []) or [])
                if nxt[: len(cur)] == cur:
                    resp = dec(nxt[len(cur):])
            out.append({"cmd": cmd, "out": resp})
        return out

    def _evolution_signal(
        self, sample: TMaxSample, rollouts: list[Rollout],
        rewards: list[float], renderer=None
    ) -> dict:
        """What a zero-std group hands the data side: which way to move the task and
        the per-attempt command transcript a hint is read from. Same shape a solver
        rollout carries, so training rollouts and a standalone solve pass feed one
        pipeline. all-pass -> harder, all-fail -> easier (the group is zero-std, so
        every reward is the same and one of them decides the direction).

        Transcript is decoded from tokens (see ``_rollout_transcript``); a parsed-
        message fallback keeps the old shape if decoding is unavailable."""
        passed = rewards[0] > 0
        tokenizer = None
        if renderer is not None:
            tokenizer = getattr(renderer, "tokenizer", None) or getattr(
                renderer, "_tokenizer", None)

        def transcript(r):
            try:
                if tokenizer is not None:
                    return self._rollout_transcript(r, tokenizer)
            except Exception:  # noqa: BLE001 -- never break signal emission
                pass
            return [
                {"cmd": self._message_text(turn.completion_message),
                 "out": "\n".join(self._message_text(m) for m in turn.env_messages)}
                for turn in r.turns
            ]

        return {
            "event": "signal_created",
            "created_time_unix_ns": time.time_ns(),
            "task_id": sample.instance_id,
            "source_group_id": rollouts[0].group_id if rollouts else None,
            "source_lineage": asdict(sample.lineage) if sample.lineage else None,
            "solved": len(rewards) if passed else 0,
            "total": len(rewards),
            "direction": "harder" if passed else "easier",
            "attempts": [
                {"reward": r.reward, "turns": len(r.turns),
                 "transcript": transcript(r)}
                for r in rollouts
                if r.reward is not None
            ],
        }

    async def _run_agent_rollout(
        self,
        *,
        adapter: AnthropicAdapter,
        generate_fn: GenerateFn,
        sample: TMaxSample,
        group_id: int,
        rollout_idx: int,
        sampling: "SamplingConfig",
        renderer: Renderer,
    ) -> tuple[Rollout, bool, int, str, _SandboxRolloutDiagnostics]:
        """Boot a sandbox, run the agent as root, grade the task in place.

        Always returns ``(Rollout, submitted, fmt_errors, finish_reason,
        sandbox_diagnostics)`` (errors caught + marked terminal) so one bad sibling
        never fails the whole group.
        ``submitted`` is whether the agent emitted the submit marker (False on any
        error / no-submit); ``fmt_errors`` is the tool-call parse-failure count;
        ``finish_reason`` is how the loop ended (submit / hit_max_turns /
        hit_time_budget / stopped_early, or "error" on the timeout/exception paths
        where the loop never returned). The caller aggregates these into the group's
        ``rollout/nonsubmit_frac``, ``rollout/format_*`` and ``rollout/finish_*``
        metrics.
        """
        rollout_id = RolloutTurnID(
            group_id=group_id, rollout_id=rollout_idx, turn_id=0
        ).to_string(include_turn=False)

        status = RolloutStatus.ERROR
        reward = 0.0
        error_msg = ""
        submitted = False
        fmt_errors = 0  # total format errors this rollout (from run_vanillux_loop)
        # Default for the timeout/exception paths where run_vanillux_loop never
        # returned (it sets its own reason on the normal path).
        finish_reason = "error"
        agent_turns = 0
        infra_failed = False
        # Per-test verifier breakdown; stays None unless the rollout was graded with
        # the CTRF read enabled and the task wrote a parsable report.
        ctrf: dict | None = None
        # reward.txt's value, kept alongside a dense reward so both curves are
        # comparable on one run, and whether dense had to fall back to it.
        sparse_reward = 0.0
        dense_fallback = False
        issue_tracker = SandboxIssueTracker(
            SandboxLogContext(
                instance_id=sample.instance_id,
                group_id=group_id,
                rollout_id=rollout_idx,
            )
        )
        sandbox: Sandbox | None = None
        rollout_timeout = None
        # Hoisted out of the try so the completion line can report them whatever
        # path the rollout takes out of it. Both are pure reads of `sample`.
        budget_sec = self._agent_budget_sec(sample)
        started_at = time.monotonic()
        await self._rollout_gate.acquire_sibling((group_id, rollout_idx))
        try:
            # open_session is inside the try so a failure still releases the slot.
            adapter.open_session(
                rollout_id,
                generate_fn=generate_fn,
                sampling=sampling,
                routing_session_id=rollout_id,
                max_context_tokens=self._max_context_tokens,
            )
            async with asyncio.timeout(
                self._guard_for(budget_sec) + self._SANDBOX_BOOT_ALLOWANCE_SEC
            ) as rollout_timeout:
                # host_loop drives the sandbox with bash directly; it never runs the
                # Claude Code CLI, so skip the curl-based install (the tmax task
                # images have no curl, which would otherwise fail every boot).
                async with boot_agent_sandbox(
                    sample.image,
                    dockerfile=sample.dockerfile,
                    build_context=sample.build_context,
                    install_claude=False,
                    cpu=sample.daytona_cpu,
                    memory=sample.daytona_mem_gb,
                    disk_gb=sample.daytona_disk_gb,
                    issue_tracker=issue_tracker,
                ) as sandbox:
                    # Sandbox is up: shrink the guard back to the agent's own
                    # envelope. Boot-queue time must not eat the agent's budget --
                    # the deadline restarts here, exactly like the agent's own
                    # deadline (set at agent start in the harness).
                    rollout_timeout.reschedule(
                        asyncio.get_running_loop().time()
                        + self._guard_for(budget_sec)
                    )
                    # Force every tool command to run as root (tmax tasks touch
                    # system paths); the faithful Vanillux loop dispatches bash here.
                    root_sb = _RootSandbox(sandbox)
                    # Docker would have run this as PID 1 before anything else; our
                    # backends exec commands directly, so start it here or every
                    # task that depends on it is unsolvable.
                    if sample.entrypoint:
                        await _start_entrypoint(
                            root_sb, sample.entrypoint, workdir=sample.workdir
                        )
                    # Seed the agent-facing inputs (environment/seeds/* -> /workspace)
                    # BEFORE the agent runs -- upstream seeds at reset. Without this,
                    # seed-bearing tasks are unsolvable (inputs absent during rollout).
                    # Grading fixtures (tests/*) are uploaded later by grade_tmax.
                    await seed_workspace(root_sb, sample.tmax)
                    try:
                        _rc, _o, _e = await root_sb.exec(
                            "command -v tmux >/dev/null 2>&1", check=False
                        )
                        if _rc != 0:
                            _note_image_without_tmux(sample)
                    except Exception:  # noqa: BLE001 -- never fail a rollout on this
                        pass
                    agent_run = await get_agent(self._agent_name)(
                        AgentTask(
                            sandbox=root_sb,
                            instruction=sample.problem_statement,
                            session_id=rollout_id,
                            adapter=adapter,
                            time_budget_sec=budget_sec,
                            workdir=sample.workdir,
                        )
                    )
                    # None = the harness has no submit signal at all; grade anyway
                    # (see AgentRun.submitted) and report it as submitted for the
                    # trace, since a graded rollout is what the reward reflects.
                    submitted = agent_run.submitted is not False
                    fmt_errors = agent_run.format_errors
                    finish_reason = agent_run.finish_reason
                    # The harness's own turn count, which is what decides
                    # finish_reason (a harness reports hit_max_turns off ITS counter).
                    # Reports otherwise show len(rollout.turns) -- the trainable turns
                    # left after empty completions are dropped -- so a trajectory that
                    # spun out its turn budget on empty replies reads as a short one.
                    agent_turns = agent_run.turns
                    # tmax runs the verifier only on the submit marker; a rollout that
                    # never submits scores 0 (matches SWERLVanilluxSandboxEnv). No
                    # git_diff: grade the agent's OWN sandbox in place. ``None`` means
                    # the harness has no submit signal -- grade anyway rather than
                    # scoring every rollout 0 (see AgentRun.submitted).
                    if submitted:
                        reward = await grade_tmax(
                            sandbox,
                            sample.tmax,
                            workdir=sample.workdir,
                            timeout_sec=self._eval_timeout_sec,
                        )
                        sparse_reward = reward
                        # Read the verifier's second output (the per-test CTRF
                        # report) while the sandbox is still up. Swallow its
                        # failures: `sandbox.exec` raises when the sandbox is gone
                        # or the API errors, and the enclosing handler would
                        # otherwise turn that into reward=0 + infra_failed for an
                        # already-graded rollout. In dense mode a failed read is a
                        # fallback to the sparse reward, never a lost rollout.
                        if self._read_ctrf:
                            try:
                                ctrf = await read_ctrf_report(sandbox)
                            except Exception as e:
                                logger.warning(
                                    f"[tmax] {rollout_id}: ctrf read failed "
                                    f"({type(e).__name__}: {e})"
                                )
                        # Training only. Validation groups carry negative ids (see
                        # Controller) and MUST stay on the binary verdict: TB-2.0
                        # avg@k / pass@k are defined on all-tests-pass, so a dense
                        # validation reward would silently stop being a solve rate
                        # and stop being comparable to the published numbers.
                        if self._reward_mode == "dense" and group_id >= 0:
                            dense = ctrf_pass_fraction(ctrf)
                            dense_fallback = dense is None
                            reward = sparse_reward if dense is None else dense
                    else:
                        reward = 0.0
                status = RolloutStatus.COMPLETED
        except (TimeoutError, asyncio.TimeoutError):
            infra_failed = True
            reward = 0.0
            status = RolloutStatus.ERROR_TIMEOUT
            if rollout_timeout is not None and rollout_timeout.expired():
                logger.warning("[tmax] %s: wall-clock guard fired", rollout_id)
                error_msg = "wall_clock_timeout"
            else:
                logger.exception("[tmax] %s: sandbox timeout", rollout_id)
                error_msg = "sandbox_timeout"
        except Exception as e:
            infra_failed = True
            reward = 0.0
            logger.exception("[tmax] %s: rollout failed", rollout_id)
            status = RolloutStatus.ERROR
            error_msg = f"{type(e).__name__}: {e}"
        finally:
            self._rollout_gate.release()
            captured = await adapter.finish_session(rollout_id)

        disk_gb = (
            sandbox.allocated_disk_gb
            if sandbox is not None
            else sample.daytona_disk_gb
            or int(os.environ.get("TT_DAYTONA_DISK_GB", "6"))
        )
        diagnostics = _SandboxRolloutDiagnostics(
            sandbox_id=sandbox.sandbox_id if sandbox is not None else "",
            disk_gb=disk_gb,
            issue_counts=issue_tracker.counts,
            issues=issue_tracker.issues,
            num_dropped_details=issue_tracker.num_dropped_details,
            infra_failed=infra_failed,
        )

        # Drop empty-completion turns so rollout_to_training_samples only sees
        # trainable turns (a non-final empty completion would otherwise raise).
        turns: list[RolloutTurn] = [
            RolloutTurn(
                rollout_id=RolloutTurnID(
                    group_id=group_id, rollout_id=rollout_idx, turn_id=turn_idx
                ),
                prompt_token_ids=ct.prompt_token_ids,
                completion_token_ids=ct.completion_token_ids,
                completion_logprobs=ct.completion_logprobs,
                min_policy_version=ct.min_policy_version,
                max_policy_version=ct.max_policy_version,
            )
            for turn_idx, ct in enumerate(
                ct for ct in captured if ct.completion_token_ids
            )
        ]

        # Load-bearing invariant: the off-policy age filter (controller) takes
        # min()/max() over each sample's turn policy versions, which TypeErrors
        # on None. Over-budget/empty turns carry version None and are dropped
        # above; assert none leaked so a violation fails here (clear) instead of
        # deep in the batcher.
        assert all(
            t.min_policy_version is not None and t.max_policy_version is not None
            for t in turns
        ), (
            f"{rollout_id}: a trainable RolloutTurn has a None policy version "
            "(empty-turn drop invariant broke)"
        )

        if not turns:
            status = RolloutStatus.ERROR
            if not error_msg:
                n_cap = len(captured)
                n_empty = sum(1 for ct in captured if not ct.completion_token_ids)
                error_msg = (
                    f"no_trainable_turns: captured={n_cap} empty_completions={n_empty}"
                )
        else:
            # Wrong-submit penalty (SWE_WRONG_SUBMIT_PENALTY, default 0 = off).
            # The unshaped reward gives +1 for a correct submit and 0 for BOTH a
            # wrong submit and a timeout, so submitting costs nothing and the
            # policy drifts toward eager submission: measured on a 64-task
            # held-out set, 45 steps moved submit-rate +31% while submit
            # precision fell 82%->56% and net reward DROPPED below the base
            # model. A negative reward for a graded-wrong submit makes the
            # within-group ordering correct-submit > keep-working > wrong-submit,
            # which is the missing gradient toward "explore until sure".
            shaped = float(reward)
            _pen = float(os.environ.get("SWE_WRONG_SUBMIT_PENALTY", "0") or "0")
            if _pen > 0 and submitted and shaped <= 0.0:
                shaped = -_pen
            turns[-1].env_rewards = {TMAX_REWARD_KEY: shaped}

        # `or not turns`: a rollout that never took a turn is the one case where
        # the task is named nowhere else. The sandbox created fine, so there are no
        # issue_counts to trip this, and the per-rollout `[tmax] group=N/rollout=M`
        # line carries no instance_id -- so a task that kills every rollout in its
        # group leaves no record of which task it was, and an all-error group is
        # dropped before it can even emit an evolution signal (NaN rewards are not
        # scored). Observed: 16 rollouts lost to an image that could neither install
        # tmux nor build it, with nothing in any log naming the task.
        if diagnostics.issue_counts or not turns:
            logger.warning(
                "[tmax_sandbox_summary] %s",
                json.dumps(
                    {
                        "event": "tmax_sandbox_summary",
                        "instance_id": sample.instance_id,
                        "group_id": group_id,
                        "rollout_id": rollout_idx,
                        "sandbox_id": diagnostics.sandbox_id,
                        "image": sample.image,
                        "disk_gb": diagnostics.disk_gb,
                        "status": str(status),
                        "submitted": submitted,
                        "reward": reward,
                        "finish_reason": finish_reason,
                        "infra_failed": diagnostics.infra_failed,
                        "issue_counts": diagnostics.issue_counts,
                        "num_dropped_details": diagnostics.num_dropped_details,
                    },
                    sort_keys=True,
                ),
            )

        # Suspected out-of-memory kills, surfaced as a greppable per-rollout
        # flag: exit 137 / oom phrasing in any env message. The default 4GiB
        # sandbox is smaller than 26 tasks own declared req_memory_mb, so this
        # is the readout that says when memory, not the model, failed the task.
        _oom_marks = ("Killed", "Out of memory", "Cannot allocate memory",
                      "MemoryError", "exit=137", "exit code 137",
                      "No space left on device", "Disk quota exceeded")
        oom_suspect = any(
            any(m in self._message_text(msg) for m in _oom_marks)
            for turn in turns
            for msg in turn.env_messages
        )
        # secs/budget are what tells a budget change apart from a no-op. Nothing
        # else records how long a rollout ran: the trace dump carries turns but no
        # clock, and only the rollout that overruns its guard leaves a mark
        # (error_timeout -- 1 of 5,687 in the run this was added from). Without
        # these two numbers, "how many rollouts run long enough for the budget to
        # matter" is unanswerable, and the budget gets picked by feel.
        logger.info(
            "[tmax] %s: status=%s reward=%.2f turns=%d oom_suspect=%d "
            "secs=%.0f budget=%d",
            rollout_id,
            status,
            reward,
            len(turns),
            int(oom_suspect),
            time.monotonic() - started_at,
            budget_sec,
        )
        self._maybe_dump_trace(
            rollout_id=rollout_id,
            group_id=group_id,
            sample=sample,
            captured=captured,
            renderer=renderer,
            status=str(status),
            reward=reward,
            submitted=submitted,
            fmt_errors=fmt_errors,
            error_msg=error_msg,
            finish_reason=finish_reason,
            sandbox_diagnostics=diagnostics,
        )
        return (
            Rollout(
                group_id=group_id,
                rollout_id=rollout_idx,
                status=status,
                turns=turns,
                # Keep the per-rollout loop outcome: the group metrics average these
                # away, but a trace report needs to say why THIS rollout stopped
                # (e.g. a turn truncated inside <think> that never emitted a
                # tool_call is a format_errors=1 / stopped_early rollout).
                diagnostics={
                    "finish_reason": finish_reason,
                    "agent_turns": agent_turns,
                    "format_errors": fmt_errors,
                    "submitted": submitted,
                    "infra_failed": diagnostics.infra_failed,
                    "ctrf": ctrf,
                    "sparse_reward": sparse_reward,
                    "dense_fallback": dense_fallback,
                },
            ),
            submitted,
            fmt_errors,
            finish_reason,
            diagnostics,
        )

    def _maybe_dump_trace(
        self,
        *,
        rollout_id: str,
        group_id: int,
        sample: TMaxSample,
        captured: list,
        renderer: Renderer,
        status: str,
        reward: float,
        submitted: bool = False,
        fmt_errors: int = 0,
        error_msg: str = "",
        finish_reason: str = "",
        sandbox_diagnostics: _SandboxRolloutDiagnostics,
    ) -> None:
        """Write a human-readable per-rollout training trace when
        ``SWE_ROLLOUT_DUMP_DIR`` is set. Format mirrors the open-instruct diagnostic
        trace: a summary header (reward / finish / submitted / num tool calls /
        response length) followed by the FULL decoded trajectory with the model's
        actions (``<tool_call>``) and the sandbox outputs (``<tool_response>``)
        interleaved -- reconstructed from the TITO-bridged prompts (turn N+1's prompt
        is turn N's prompt+completion + the new tool_response, so the growing prefix
        recovers each turn's sandbox output). Best-effort; never raises."""
        dump_dir = os.environ.get("SWE_ROLLOUT_DUMP_DIR", "")
        if not dump_dir:
            return
        # Validation groups (negative ids) get the controller's per-pass trace
        # report instead; dumping them here too would duplicate every transcript
        # into the training dump dir.
        if group_id < 0:
            return
        try:
            tokenizer = getattr(renderer, "tokenizer", None) or getattr(
                renderer, "_tokenizer", None
            )

            def _decode(ids: list[int]) -> str:
                if tokenizer is None or not ids:
                    return ""
                return tokenizer.decode(ids, skip_special_tokens=False)

            # Reconstruct the full interleaved token stream. TITO invariant: each turn's
            # prompt extends the previous prompt+completion, so appending each
            # completion and then the next prompt's delta recovers the whole
            # system+task+<tool_call>+<tool_response>+... conversation. A branch
            # (extends_previous False, e.g. compaction) resets to that turn's fresh
            # prompt (rare for tmax vanillux; noted inline).
            full_ids: list[int] = list(captured[0].prompt_token_ids) if captured else []
            branch_turns: list[int] = []
            for i, ct in enumerate(captured):
                full_ids += list(ct.completion_token_ids)
                if i + 1 < len(captured):
                    nxt = list(captured[i + 1].prompt_token_ids)
                    if nxt[: len(full_ids)] == full_ids:
                        full_ids += nxt[len(full_ids) :]  # tool_response delta
                    else:
                        branch_turns.append(i + 1)
                        full_ids = nxt  # history rewrite -> fresh render
            full_text = _decode(full_ids)

            response_len = sum(len(ct.completion_token_ids) for ct in captured)
            num_tool_calls = full_text.count("<tool_call>")
            last_model_finish = captured[-1].finish_reason if captured else None
            any_length_finish = any(ct.finish_reason == "length" for ct in captured)
            outcome = "SUCCESS" if reward and reward > 0 else "FAIL"

            header = (
                "=" * 90
                + f"\nTMAX-9B rollout trace  ({outcome}, reward={reward})\n"
                + "=" * 90
                + f"\ninstance_id    : {sample.instance_id}"
                + f"\nimage          : {sample.image}"
                + f"\nrollout_id     : {rollout_id}"
                + f"\nstatus         : {status}   submitted: {submitted}"
                + f"\nreward         : {reward}"
                + f"\nfinish_reason  : {finish_reason}"
                + f"\nmodel finish   : {last_model_finish}   any length-cap turn: {any_length_finish}"
                + f"\nsandbox_id     : {sandbox_diagnostics.sandbox_id}"
                + f"\nsandbox disk   : {sandbox_diagnostics.disk_gb} GiB"
                + f"\ninfra failed   : {sandbox_diagnostics.infra_failed}"
                + "\nsandbox issues : "
                + json.dumps(sandbox_diagnostics.issue_counts, sort_keys=True)
                + f" (details dropped={sandbox_diagnostics.num_dropped_details})"
                + f"\nnum turns      : {len(captured)}   num tool calls: {num_tool_calls}"
                + f"\nresponse length: {response_len} tokens (model-generated, all turns)"
                + f"\nformat_errors  : {fmt_errors}"
                + (f"\nerror          : {error_msg}" if error_msg else "")
                + (
                    f"\nNOTE branch/re-render at turns {branch_turns} (TITO not continued)"
                    if branch_turns
                    else ""
                )
                + "\n"
                + "=" * 90
                + "\nAGENT TRAJECTORY (decoded; <tool_call>=model action, "
                + "<tool_response>=sandbox output)\n"
                + "=" * 90
                + "\n"
            )

            os.makedirs(dump_dir, exist_ok=True)
            safe = rollout_id.replace("/", "_")
            path = os.path.join(dump_dir, f"{safe}.txt")
            with open(path, "w") as f:
                f.write(header)
                f.write(full_text)
                f.write("\n")
            logger.info("[tmax] rollout trace dumped: %s", path)
            if sandbox_diagnostics.issue_counts:
                issue_path = os.path.join(dump_dir, f"{safe}.sandbox.json")
                with open(issue_path, "w") as f:
                    json.dump(
                        {
                            "instance_id": sample.instance_id,
                            "image": sample.image,
                            "rollout_id": rollout_id,
                            "sandbox_id": sandbox_diagnostics.sandbox_id,
                            "disk_gb": sandbox_diagnostics.disk_gb,
                            "status": status,
                            "submitted": submitted,
                            "reward": reward,
                            "finish_reason": finish_reason,
                            "infra_failed": sandbox_diagnostics.infra_failed,
                            "error": error_msg,
                            "issue_counts": sandbox_diagnostics.issue_counts,
                            "num_dropped_details": (
                                sandbox_diagnostics.num_dropped_details
                            ),
                            "issues": [
                                asdict(issue) for issue in sandbox_diagnostics.issues
                            ],
                        },
                        f,
                        sort_keys=True,
                    )
                logger.info("[tmax] sandbox issue trace dumped: %s", issue_path)
        except Exception as e:
            logger.warning("[tmax] rollout trace dump failed: %s", e)
