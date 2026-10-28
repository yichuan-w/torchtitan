# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Did the agent's last commands destroy its own sandbox?

A rollout whose sandbox dies mid-episode is masked as an infra failure, which
is right when the platform lost it and wrong when the agent killed it: run
1026b masked 205 rollouts whose last turns ran ``pkill daytona``, ``kill 1``,
``tmux kill-server``, ``pkill -9 bash`` and the like, so the behaviour was never
penalised and spread. The rollouter scores such a rollout 0 instead, but only
when the sandbox actually failed during the agent stage AND the last turns'
commands match one of the rules below; the command alone proves nothing.

The rules are the ones measured on 1026b's 531 masked training rollouts; ones
with no hit or with false matches there (``pkill sleep``, one-letter pkill
patterns, ``rm`` of shared libraries, ``halt``) are left out.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from typing import Any

from torchtitan.experiments.rl.examples.tmax.rollout_record import (
    _KEYSTROKES,
    _VANILLUX_COMMAND,
)

# A heredoc body is file content, not a command (a fork bomb written into a source file).
_HEREDOC = re.compile(
    r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)(?:^|\n)\s*\2\s*(?:\n|$)", re.S
)
# Command position: line start, after a separator or `$(`, or after sudo/then/do/timeout N/nohup/exec.
_SEP = (
    r"(?:^|[;&|(`]\s*|\$\(\s*|\n\s*|\bsudo\s+|\bthen\s+|\bdo\s+"
    r"|\btimeout\s+\S+\s+|\bnohup\s+|\bexec\s+)"
)
_END = r"(?=\s|;|&|\||\)|$)"
SELF_DESTRUCT_RULES: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern, re.M)
    for name, pattern in {
        # The Daytona daemon (pid 1): the sandbox API dies with it.
        # Its toolbox process carries the sandbox API too.
        "kill_daytona": _SEP + r"(?:pkill|killall)\b[^\n;&|]*\bdaytona\b"
        r"|" + _SEP + r"(?:pkill|killall)\b[^\n;&]*\btoolbox\b"
        r"|\bkill\b[^\n;&|]*\$\((?:pgrep|pidof)[^)]*\b(?:daytona|tmux)\b"
        r"|grep[^\n|]*\bdaytona\b[^\n]*\|\s*xargs\s+(?:-\S+\s+)*kill\b"
        r"|\bkill\b[^\n;]*\$\(ps\b[^)]*\bdaytona\b",
        # Every process (`kill -9 -1`, `kill -INT -1`, a kill over every pid `ps`
        # lists), or pid 1.
        "kill_all_or_pid1": _SEP
        + r"kill\s+(?:-(?:\w+|-|s\s+\w+)\s+)+-1"
        + _END
        + r"|"
        + r"\bkill\b[^\n;]*\$\(\s*ps\b[^)|]*\|\s*awk\s+['\"]\{\s*print\s+\$[12]\s*\}['\"]\s*\)"
        + r"|"
        + _SEP
        + r"kill\s+(?:-\S+\s+)*(?:\d+\s+)*1"
        + _END
        + r"|"
        + _SEP
        + r"killall5\b",
        # Children of pid 1, the tmux server among them.
        "pkill_children_of_pid1": _SEP + r"pkill\s+(?:-\S+\s+)*-P\s+1" + _END,
        # The harness's tmux server or session; the server retitles itself
        # "tmux: server", so a bare `pkill server` kills it.
        "tmux_kill": _SEP
        + r"tmux\s+kill-server\b"
        + r"|"
        + _SEP
        + r"tmux\s+kill-session(?:\s+-t\s+['\"]?terminus[^\s;'\"]*['\"]?)?"
        r"(?=\s*(?:;|&|\||\n|$|2>))"
        + r"|"
        + _SEP
        + r"(?:pkill|killall)\b[^\n;&|]*\btmux\b"
        + r"|"
        + _SEP
        + r"pkill\s+(?:-(?!f\b)\S+\s+)*(?:-f\s+)?['\"]?server['\"]?(?=\s|;|$|&|\s*2>)",
        "remove_tmux": _SEP
        + r"(?:apt|apt-get|aptitude|yum|dnf|apk)\b[^\n;&]*"
        r"\b(?:remove|purge|autoremove|del|erase)\b[^\n;&]*\btmux\b"
        + r"|"
        + _SEP
        + r"dpkg\s+(?:-\S+\s+)*(?:-r|-P|--remove|--purge)\s+(?:\S+\s+)*tmux\b",
        # The pane shell (`bash --login`), or its parent.
        "kill_pane_shell": _SEP
        + r"(?:pkill|killall)\s+(?:-\S+\s+)*(?:-f\s+)?['\"]?(?:bash|sh|-u\s+root)['\"]?"
        r"(?=\s|;|$|\s*2>)"
        + r"|"
        + _SEP
        + r"pkill\s+(?:-\S+\s+)*-f\s+['\"]?login['\"]?(?=\s|;|$|\s*2>)"
        + r"|"
        + _SEP
        + r"kill\s+(?:-\S+\s+)*(?:\$\$|\$PPID|\$\{PPID\})",
        "rm_rf_root": _SEP + r"rm\s+(?:-\S+\s+)*(?:/|/\*)" + _END
        # `find / ... -delete` with no name filter deletes the harness's files too.
        + r"|"
        + _SEP
        + r"find\s+/\s(?:(?!-i?name\b|-i?path\b|-i?regex\b)[^\n;|&])*-delete\b",
        "machine_down": _SEP + r"(?:reboot|poweroff|shutdown)" + _END,
    }.items()
}


def last_turn_commands(captured: Sequence[Any], num_turns: int = 2) -> list[str]:
    """The commands the last ``num_turns`` captured turns sent to the sandbox.

    Two, because a failure is often caught at the first keystroke of the turn
    after the one that killed the sandbox. Terminus-2 (plain_text) carries
    commands as ``<keystrokes>`` in the text block; vanillux/mini-swe-agent as a
    tool call, either a ``tool_use`` block or a qwen3 ``<parameter=command>``."""
    commands: list[str] = []
    for turn in captured[-num_turns:] if num_turns > 0 else ():
        message = getattr(turn, "completion_message", None) or {}
        content = message.get("content") or []
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in content:
            if block.get("type") == "tool_use":
                tool_input = block.get("input")
                command = (
                    tool_input.get("command") if isinstance(tool_input, dict) else None
                )
                if isinstance(command, str):
                    commands.append(command)
            elif block.get("type") == "text":
                text = block.get("text") or ""
                commands += _KEYSTROKES.findall(text) or _VANILLUX_COMMAND.findall(
                    text
                )
    return commands


def match_self_destruct(commands: Sequence[str]) -> str | None:
    """``"<rule>: <command line>"`` for the first matching command, or None."""
    for command in commands:
        text = _HEREDOC.sub("\n", html.unescape(command))
        for name, rule in SELF_DESTRUCT_RULES.items():
            hit = rule.search(text)
            if hit is not None:
                start = text.rfind("\n", 0, hit.start()) + 1
                end = text.find("\n", hit.end())
                line = text[start : end if end >= 0 else len(text)].strip()
                return f"{name}: {line}"[:200]
    return None
