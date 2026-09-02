# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Terminal-Bench's Terminus-2 scaffold as a swappable harness.

Terminus-2 is a materially different agent from our other harnesses, which is the
point of having it: it drives a live tmux pane with *batches* of raw keystrokes
and observes the screen, rather than issuing one bash command and reading its
stdout. That buys interactive programs (answering a prompt, ``C-c`` on a hung
process, typing into gdb/vim, or waiting without acting) and packs several
commands into one model turn -- which is why published Terminus-2 turn counts run
far below ours on the same tasks.

Two seams make it work without vendoring the agent:

  - ``_SandboxEnvironment`` presents our ``Sandbox`` as the five members
    Terminus-2 touches (measured, not guessed: ``exec`` / ``upload_file`` /
    ``download_file`` / ``is_dir`` / ``default_user`` plus
    ``trial_paths.agent_dir``).
  - ``_AdapterLLM`` replaces Terminus-2's LiteLLM backend with a direct
    ``adapter.complete`` call. The adapter deliberately does NOT run an HTTP
    server on the tmax path (no loopback hop, no per-worker port), and rollout
    workers are separate processes, so ``adapter.url`` resolves to nothing there --
    pointing LiteLLM at it fails every rollout with "Cannot connect to host".
    Calling in-process also keeps turn capture on the same path the other
    harnesses use.

CAVEAT: the model has to emit Terminus-2's XML (``<response><analysis><plan>
<commands><keystrokes>``). A policy trained under a tool-calling scaffold is off
distribution here, so check ``format_errors`` before reading anything into a
reward: sparse binary reward cannot teach a new output format.

Terminus-2's context summarization is OFF here by default. Upstream it defaults on
and fires whenever free context drops below 8k, and it is not one call but a chain
of three subagent prompts -- summarize, then "ask at least five questions", then
answer them. Those all run through ``_AdapterLLM``, so they land in the training
trajectory as ordinary turns while asking the policy for prose instead of an
action, and the post-handoff agent has lost the state it built. Measured on a
9B Terminal-Bench 2.0 pass, within the tasks that saw both: 61 trials that
summarized scored zero reward and submitted 8% of the time, against 0.07 and 21%
for the 94 that did not, and 79% of the run's wall-clock timeouts were trials
that had summarized. Set ``TMAX_TERMINUS_SUMMARIZE=1`` to restore the upstream
behavior (e.g. to A/B fidelity against published numbers).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from torchtitan.experiments.rl.harness.agents.spec import (
    AgentRun,
    AgentTask,
    register_agent,
)

logger = logging.getLogger(__name__)

_PARSER = os.environ.get("TMAX_TERMINUS_PARSER", "xml")
# Terminus-2 batches commands per turn, so it needs far fewer than a one-command-
# per-turn scaffold; this only bounds a runaway loop.
_DEFAULT_MAX_TURNS = int(os.environ.get("TMAX_TERMINUS_MAX_TURNS", "64"))
# Terminus-2 asks the backend for a context limit to decide when to summarize.
_MAX_CONTEXT = int(os.environ.get("SWE_MAX_CONTEXT_LEN", "63488"))
# Fallback for the per-turn cap quoted to the model. The real cap is the session's
# SamplingConfig (see _AdapterLLM.get_model_output_limit); this only applies when the
# adapter cannot be asked, e.g. before the session is open.
_TURN_MAX_TOKENS = int(os.environ.get("TMAX_TURN_MAX_TOKENS", "16384"))
# See the module docstring: upstream defaults this on, and on a 9B it is lethal.
_SUMMARIZE = os.environ.get("TMAX_TERMINUS_SUMMARIZE", "0") == "1"
# Free-context tokens below which Terminus-2 summarizes preemptively; 0 disables
# that (upstream default 8192). Only consulted when _SUMMARIZE is on.
_PROACTIVE_SUMMARIZE_THRESHOLD = int(
    os.environ.get("TMAX_TERMINUS_PROACTIVE_SUMMARIZE_TOKENS", "8192")
)
# Slack when deciding whether a turn stopped at the per-turn cap or at the context
# wall: the adapter clamps to exactly the remaining budget, so the two sum to
# max_context on the wall, give or take a rendering token.
_CONTEXT_TAIL_MARGIN = 64

# Placeholder model name handed to Terminus-2. The real policy is our swapped-in
# _AdapterLLM, but Terminus-2 still calls litellm's token_counter(model=_MODEL_NAME)
# once per turn to decide when to summarize. litellm does not know this name, so it
# raised a BadRequest with the "Provider List" hint every turn -- caught and harmless,
# but it flooded controller stdout and drowned the trainer step lines.
_MODEL_NAME = "titan-actor"

_litellm_registered = False


def _register_titan_actor_with_litellm() -> None:
    """Register the placeholder model name so litellm's token_counter stays quiet.

    token_counter falls back to a tiktoken estimate for any registered model it has
    no exact tokenizer for; registering the name just suppresses the per-turn
    "Provider List" BadRequest, it does not change the (approximate) token count that
    only drives Terminus-2's summarize threshold. Idempotent and best-effort: a
    litellm API change here must never fail a rollout, so any error is swallowed.
    """
    global _litellm_registered
    if _litellm_registered:
        return
    try:
        import litellm

        litellm.register_model(
            {
                _MODEL_NAME: {
                    "litellm_provider": "openai",
                    "mode": "chat",
                    "max_input_tokens": _MAX_CONTEXT,
                    "max_tokens": _TURN_MAX_TOKENS,
                }
            }
        )
    except Exception:
        pass
    # Set the flag even on failure: a broken litellm import will not recover per turn,
    # and retrying it every rollout would just re-pay the import cost for nothing.
    _litellm_registered = True


class _AdapterExhausted(RuntimeError):
    """The adapter has no completion left for this session."""


class _TimeBudgetExhausted(RuntimeError):
    """The rollout's wall-clock budget ran out between Terminus-2 episodes."""


class _AdapterLLM:
    """Terminus-2's LLM seam, backed by ``AnthropicAdapter.complete`` in-process.

    Terminus-2 only ever does ``await llm.call(prompt=..., message_history=...)``
    plus the two limit getters, so this is the whole surface. The adapter speaks
    Anthropic messages, which is also what it captures for training.
    """

    def __init__(
        self,
        adapter: Any,
        *,
        session_id: str,
        max_context: int,
        turn_max_tokens: int,
        deadline: float | None = None,
    ) -> None:
        self._adapter = adapter
        self._session_id = session_id
        self._max_context = max_context
        self._turn_max_tokens = turn_max_tokens
        # Terminus-2's loop lives inside harbor, so this is the one hook we have
        # that runs once per episode -- the same "check between turns" the
        # vanillux loop does with its own deadline.
        self._deadline = deadline
        # Counted, not suppressed: a summarization subagent call is captured into
        # the training trajectory like any other turn, so a run needs to be able
        # to see how much of its data came from one.
        self.subagent_calls = 0

    def get_model_context_limit(self) -> int:
        return self._max_context

    def get_model_output_limit(self) -> int | None:
        # Terminus-2 puts this number in the retry it sends after a truncated turn
        # ("you exceeded N tokens, break it into chunks"). None degrades that to
        # "the maximum output length", which the model cannot act on.
        #
        # Ask the adapter rather than quoting TMAX_TURN_MAX_TOKENS: generation runs on
        # the SamplingConfig the rollouter opened the session with, and a recipe can
        # set that independently -- the 27B one raises it to 32768 against this
        # module's 16384 default. Quoting the smaller number tells the model to chunk
        # under half the budget it actually has.
        session_max_tokens = self._adapter.session_max_tokens(self._session_id)
        return session_max_tokens or self._turn_max_tokens

    async def call(self, prompt: str, message_history=None, **_kwargs):
        from harbor.llms.base import (  # type: ignore
            ContextLengthExceededError,
            LLMResponse,
            OutputLengthExceededError,
        )

        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise _TimeBudgetExhausted(
                f"time budget spent before an episode for {self._session_id}"
            )
        messages = list(message_history or [])
        messages.append({"role": "user", "content": prompt})
        # No max_tokens: the adapter does not read it from the body (generation runs
        # on the session's SamplingConfig), and sending it reads as though it sets the
        # cap. See get_model_output_limit for where the real one comes from.
        reply = await self._adapter.complete(
            self._session_id,
            {"messages": messages, "stream": False},
        )
        if reply is None:
            # The session is closed or the generator yielded nothing; ending the
            # trajectory is what the other harnesses do here too.
            raise _AdapterExhausted(
                f"adapter returned no completion for {self._session_id}"
            )
        text = "".join(
            block.get("text", "")
            for block in (reply.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        # A turn cut off at max_tokens has to be raised, not returned. Terminus-2
        # handles it inside its LLM call -- salvage a complete action out of the
        # truncated text, else re-ask for a shorter one -- and neither step costs an
        # episode. Returned as an ordinary reply it instead reaches the XML parser,
        # fails there, and burns an episode on the parser-warning retry. Both of
        # harbor's own backends raise here for the same reason.
        #
        # The adapter reports "max_tokens" for two different things, and they need
        # opposite handling. A turn that ran into the per-turn cap is worth another
        # try: Terminus-2 salvages an action out of the truncated text or re-asks for
        # a shorter one, and neither costs an episode. A turn that ran into the
        # *context* wall is not -- the retry appends to a history that is already at
        # budget, so it truncates in the same place, and left to reach the XML parser
        # instead it fails there and the loop spins to the turn cap doing nothing.
        # Raised as ContextLengthExceededError it either summarizes (freeing budget)
        # or ends the trajectory, both of which terminate.
        #
        # Which one happened cannot be read off output_tokens: the adapter clamps the
        # per-turn cap down to the context that is left, so a turn with only a few
        # hundred tokens of headroom stops at "max_tokens" after a few hundred tokens
        # and looks exactly like a capped generation. Compare the whole turn against
        # the context budget instead -- on the wall, prompt + completion is the
        # budget. With no context budget configured the adapter does not clamp at
        # all, so there is no wall to hit and a stop can only be the per-turn cap.
        if reply.get("stop_reason") in ("max_tokens", "length"):
            usage = reply.get("usage") or {}
            in_tok = int(usage.get("input_tokens") or 0)
            out_tok = int(usage.get("output_tokens") or 0)
            at_context_wall = self._max_context > 0 and (
                out_tok == 0
                or in_tok + out_tok >= self._max_context - _CONTEXT_TAIL_MARGIN
            )
            if not at_context_wall:
                raise OutputLengthExceededError(
                    f"turn hit the generation cap after {out_tok} tokens "
                    f"for {self._session_id}",
                    truncated_response=text,
                )
            raise ContextLengthExceededError(
                f"turn hit max_context={self._max_context} "
                f"(prompt={in_tok} completion={out_tok}) for {self._session_id}"
            )
        return LLMResponse(content=text)


class _CountingParser:
    """Terminus-2's response parser, counting the turns it could not use.

    ``AgentRun.format_errors`` is what tells a run whether the policy can even
    speak Terminus-2's XML, and nothing was filling it in -- every rollout reported
    0, which reads as "the format is fine" but meant "never measured". Terminus-2
    reaches its parser through exactly one call, so wrapping it is enough.

    A turn counts as an error when the parser produced neither a command nor a
    completion signal: whatever the model emitted, the terminal did not move.
    Delegation is by ``__getattr__`` because Terminus-2 feature-detects
    ``salvage_truncated_response`` on the parser.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.format_errors = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def parse_response(self, content: str) -> Any:
        result = self._inner.parse_response(content)
        if not getattr(result, "commands", None) and not getattr(
            result, "is_task_complete", False
        ):
            self.format_errors += 1
        return result


# Terminus-2 starts its pane with `pipe-pane 'cat > <agent_dir>/terminus_2.pane'`,
# which copies every byte the terminal ever shows into a file inside the sandbox,
# with no bound. That is right where it comes from -- one agent, one task, one
# machine, and you want the whole transcript to read afterwards -- and wrong
# here. A command that floods the terminal fills the task's own disk at pipe
# speed: `yes` fills 2 GiB in two seconds and 10 GiB in twenty-one, and the
# rollout dies of a full disk while the thing that filled it was the log.
#
# The cap is the first half of the fix and applies always. 8 MiB is far more
# terminal text than a rollout produces -- these tasks' own disk use has a
# median of 175 MB, so the log is noise until something floods -- and it keeps
# the beginning, which is the half that says what led up to a flood.
_PANE_CAP_BYTES = 8 * 1024 * 1024

# The second half. Nothing reads that transcript: `terminus_2.pane` appears
# exactly once in harbor, in the line that writes it, and we do not fetch it
# either. Meanwhile the rollout dump has a slot for each turn's messages and
# this harness never fills them -- 89,091 turns from one run carry no prompt,
# completion or environment message at all. So a rollout's only record of what
# the model actually did is written inside the sandbox and thrown away with it.
#
# Setting this collects it. It is off by default because it is not free: a run
# of 22,000 rollouts fetching up to 8 MiB each is tens of gigabytes, so turn it
# on for a run you intend to audit rather than for all of them.
_PANE_DUMP_DIR_ENV = "TMAX_PANE_DUMP_DIR"


def _pane_dump_path(session_id: str) -> Path | None:
    """Where this session's terminal transcript is saved, or None when off."""
    root = os.environ.get(_PANE_DUMP_DIR_ENV, "")
    if not root:
        return None
    safe = (session_id or "unknown").replace("/", "_")
    return Path(root) / f"{safe}.pane"


def _exec_trace_path(session_id: str) -> Path | None:
    """Per-session JSONL for the sandbox exec trace, or None when tracing is off.

    ``TMAX_EXEC_TRACE_DIR`` turns it on. A rollout's wall time is dominated by
    what happens between generations -- the training loop records only the
    total, and the rollout dump records only the transcript -- so without this
    there is no way to tell a slow agent command from a slow harness.
    """
    root = os.environ.get("TMAX_EXEC_TRACE_DIR", "")
    if not root:
        return None
    safe = (session_id or "unknown").replace("/", "_")
    return Path(root) / f"{safe}.jsonl"


class _SandboxEnvironment:
    """Our ``Sandbox`` in the shape Terminus-2 expects of a harbor environment."""

    def __init__(
        self,
        sandbox: Any,
        *,
        agent_dir: Path,
        user: str = "root",
        trace_session_id: str = "",
    ) -> None:
        self._sandbox = sandbox
        self.default_user = user
        # Terminus-2 only reads ``trial_paths.agent_dir``, and only to place its
        # asciinema recording -- which we leave off.
        self.trial_paths = _TrialPaths(agent_dir=agent_dir)
        self.environment_dir = agent_dir
        self.environment_name = "titan-sandbox"
        self.session_id = ""
        # Names the exec-trace file; empty disables it (see _exec_trace_path).
        self._trace_session_id = trace_session_id
        # Set when Terminus-2 starts its pane, so the transcript can be fetched
        # before the sandbox goes away. None until then.
        self.pane_path: str | None = None

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ):
        from harbor.environments.base import ExecResult  # type: ignore

        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        command = self._bound_pane_pipe(command)
        started_at = time.time()
        exit_code, stdout, stderr = await self._sandbox.exec(
            command,
            user=str(user or self.default_user),
            env=env,
            check=False,
            **({"timeout": timeout_sec} if timeout_sec else {}),
        )
        self._trace_exec(command, started_at, exit_code)
        if exit_code != 0:
            # Terminus-2 surfaces a tmux failure as "Failed to start tmux session.
            # Error: <stderr>", which says nothing when the provider returns
            # non-zero with an empty stderr. Log what actually ran so the failure
            # can be attributed instead of guessed at.
            logger.warning(
                "[terminus] exec exit=%d cmd=%r stdout=%r stderr=%r",
                exit_code,
                command[:400],
                (stdout or "")[-400:],
                (stderr or "")[-400:],
            )
        return ExecResult(stdout=stdout, stderr=stderr, return_code=exit_code)

    _PANE_PIPE = re.compile(r"pipe-pane\s+(?:-\w+\s+\S+\s+)*'cat > (?P<path>[^']+)'")

    def _bound_pane_pipe(self, command: str) -> str:
        """Cap the pane transcript, and remember where it is.

        Terminus-2 builds its tmux session with an unbounded
        ``pipe-pane 'cat > <path>'``. Rewriting it to ``head -c`` stops the file
        at _PANE_CAP_BYTES: head exits at the cap, tmux's pipe takes EPIPE, and
        the terminal keeps working with nothing more written. Left alone, a
        command that floods the terminal fills the task's own disk.

        Matching on the command text rather than patching harbor keeps the fix
        on our side of the seam, where every command it issues already passes.
        """
        m = self._PANE_PIPE.search(command)
        if m is None:
            return command
        path = m.group("path")
        self.pane_path = path
        bounded = (
            f"head -c {_PANE_CAP_BYTES} > {shlex.quote(path)}"
        )
        return command.replace(f"'cat > {path}'", f"'{bounded}'", 1)

    def _trace_exec(self, command: str, started_at: float, exit_code: int) -> None:
        """Append one exec to the session's trace. Best-effort; never raises.

        Every sandbox command the agent drives passes through ``exec``, including
        the ``tmux send-keys`` that carries the agent's own command text and the
        ``tmux wait done`` that blocks for its runtime -- so a trace of both
        attributes a slow turn to the command that caused it.
        """
        path = _exec_trace_path(self._trace_session_id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "t": round(started_at, 3),
                "secs": round(time.time() - started_at, 3),
                "exit": exit_code,
                "cmd": command[:400],
            }
            with open(path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception:
            logger.debug("exec trace write failed", exc_info=True)

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        with open(source_path, "rb") as f:
            await self._sandbox.write_file(
                target_path, f.read(), user=self.default_user
            )

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        content = await self._sandbox.read_file(source_path, user=self.default_user)
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_text(content)

    async def is_dir(self, path: str, user: str | int | None = None) -> bool:
        exit_code, _out, _err = await self._sandbox.exec(
            f"test -d {shlex.quote(path)}",
            user=str(user or self.default_user),
            check=False,
            timeout=60,
        )
        return exit_code == 0


@dataclass(frozen=True, slots=True)
class _TrialPaths:
    """The one path Terminus-2 reads off the environment."""

    agent_dir: Path


def _episodes(agent: Any) -> int:
    """Episodes Terminus-2 entered, 0 before its loop starts or if it never ran."""
    return int(getattr(agent, "_n_episodes", 0) or 0)


def _count_subagent_calls(agent: Any, llm: _AdapterLLM) -> None:
    """Tally Terminus-2's summarization subagent calls onto ``llm``.

    Those calls go through the same ``_AdapterLLM`` and the same session as the
    agent's own turns, so they are captured into the training trajectory while
    asking the policy for prose (a summary, a list of questions) rather than an
    action. With summarization off none of this runs; the counter exists so that
    turning it back on does not silently mix the two.
    """
    if not hasattr(agent, "_run_subagent"):
        return
    inner = agent._run_subagent

    async def counting(*args: Any, **kwargs: Any) -> Any:
        llm.subagent_calls += 1
        return await inner(*args, **kwargs)

    agent._run_subagent = counting


async def terminus_agent(task: AgentTask) -> AgentRun:
    """Drive Terminus-2 against the task's sandbox and the adapter's policy."""
    from harbor.agents.terminus_2 import Terminus2  # type: ignore
    from harbor.llms.base import ContextLengthExceededError  # type: ignore
    from harbor.models.agent.context import AgentContext  # type: ignore

    # Quiet litellm's per-turn token_counter for our placeholder model name (see
    # _register_titan_actor_with_litellm). Idempotent, cheap after the first call.
    _register_titan_actor_with_litellm()

    submitted = False
    turns = 0
    format_errors = 0
    finish_reason = "unknown"
    agent = None
    parser: _CountingParser | None = None
    llm: _AdapterLLM | None = None
    # Same contract the vanillux loop honors: stop at a turn boundary once the
    # rollout's wall clock is spent. Without it Terminus-2 runs until the
    # rollouter's outer guard kills it, which lands as an infra failure and a
    # reward of 0 rather than a graded attempt.
    deadline = time.monotonic() + task.time_budget_sec if task.time_budget_sec else None
    with tempfile.TemporaryDirectory(prefix="tt-terminus-") as logs_dir:
        env = _SandboxEnvironment(
            task.sandbox, agent_dir=Path(logs_dir), trace_session_id=task.session_id
        )
        try:
            max_episodes = task.max_turns or _DEFAULT_MAX_TURNS
            agent = Terminus2(
                logs_dir=Path(logs_dir),
                model_name=_MODEL_NAME,
                parser_name=_PARSER,
                record_terminal_session=False,
                max_turns=max_episodes,
                suppress_max_turns_warning=True,
                enable_summarize=_SUMMARIZE,
                proactive_summarization_threshold=(
                    _PROACTIVE_SUMMARIZE_THRESHOLD if _SUMMARIZE else 0
                ),
            )
            # Swap the LiteLLM backend for the in-process adapter before setup;
            # Terminus-2 only reads self._llm through ``call`` and the limit getters.
            llm = _AdapterLLM(
                task.adapter,
                session_id=task.session_id,
                max_context=_MAX_CONTEXT,
                turn_max_tokens=_TURN_MAX_TOKENS,
                deadline=deadline,
            )
            agent._llm = llm
            inner_parser = getattr(agent, "_parser", None)
            if inner_parser is None:
                # Terminus-2 always builds one, so this only trips on an upstream
                # change. Losing the count is better than losing the rollout, but
                # say so rather than reporting a clean 0.
                logger.warning(
                    "[terminus] no parser to wrap; format_errors will read 0"
                )
            else:
                parser = _CountingParser(inner_parser)
                agent._parser = parser
            _count_subagent_calls(agent, llm)
            context = AgentContext()
            # tmux bring-up rides on DaytonaSandbox's own session-create retry;
            # no retry loop here.
            await agent.setup(env)
            await agent.run(task.instruction, env, context)
            turns = _episodes(agent)
            # Terminus-2's loop has THREE exits, and only one of them is a submit:
            # it runs the episodes out; it returns early on a confirmed
            # <task_complete>true</task_complete> (the second consecutive one, at
            # which point ``_pending_completion`` is still set); or it returns early
            # because ``is_session_alive()`` went false, i.e. the tmux session died
            # under it. Reading "ended before the cap" as the submit signal folds that
            # third case into "submit" and scores a dead session as a real attempt.
            if turns >= max_episodes:
                finish_reason = "hit_max_turns"
            elif getattr(agent, "_pending_completion", False):
                finish_reason = "submit"
            else:
                finish_reason = "stopped_early"
            submitted = finish_reason == "submit"
        except _TimeBudgetExhausted:
            turns = _episodes(agent)
            finish_reason = "hit_time_budget"
        except ContextLengthExceededError:
            # Only reachable with summarization off, where it is the ordinary way a
            # trajectory ends: the history filled the context. Reporting it as
            # "error" would pool it with real crashes and hide how often the context,
            # not the task, is what stopped the agent.
            turns = _episodes(agent)
            finish_reason = "hit_context_limit"
        except Exception as e:
            logger.warning(
                "[terminus] session=%s failed: %s: %s",
                task.session_id,
                type(e).__name__,
                str(e)[:200],
            )
            # Keep the episodes the agent did get through; the turns it captured are
            # still trained on, so reporting 0 here misattributes them.
            turns = _episodes(agent)
            finish_reason = "error"
        finally:
            # Collect the transcript before the sandbox goes, when a run has
            # asked for it. This is the only record of what the model actually
            # typed and what came back: the rollout dump keeps a slot per turn
            # for the messages and this harness never fills it.
            dump_to = _pane_dump_path(task.session_id)
            if dump_to is not None and env.pane_path:
                try:
                    text = await task.sandbox.read_file(env.pane_path, user="root")
                    dump_to.parent.mkdir(parents=True, exist_ok=True)
                    dump_to.write_text(text or "", errors="replace")
                except Exception as e:  # noqa: BLE001 -- never fail a graded rollout
                    logger.warning(
                        "[terminus] session=%s: could not collect %s: %s: %s",
                        task.session_id,
                        env.pane_path,
                        type(e).__name__,
                        str(e)[:200],
                    )
            if parser is not None:
                format_errors = parser.format_errors
            if llm is not None and llm.subagent_calls:
                # Summarization is off by default, so this is 0 on the normal path.
                # Nonzero means that many of the captured turns are a subagent being
                # asked for prose (a summary, a list of questions) rather than the
                # agent acting, and they are trained on all the same.
                logger.warning(
                    f"[terminus] session={task.session_id}: "
                    f"{llm.subagent_calls} summarization subagent call(s) captured "
                    f"into the trajectory"
                )

    return AgentRun(
        turns=turns,
        submitted=submitted,
        format_errors=format_errors,
        finish_reason=finish_reason,
    )


register_agent("terminus", terminus_agent)
