# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Echoed heredoc lines collapse to one marker; anything else is shown as is."""

from torchtitan.experiments.rl.harness.agents.terminus import (
    _collapse_echo,
    _typed_keys,
)

_TYPED = "cat > /app/a.py << 'EOF'\nimport os\n\ndef f():\n    return 1\nEOF\n"
_PROMPT = "root@box:/app# "


def test_heredoc_echo_collapses_keeping_command_and_delimiter() -> None:
    text = (
        f"{_PROMPT}cat > /app/a.py << 'EOF'\n> import os\n>\n> def f():\n"
        f">     return 1\n> EOF\n{_PROMPT}python3 /app/a.py\nok\n{_PROMPT}"
    )
    out, removed = _collapse_echo(text, _TYPED, 3)
    assert removed == 4
    assert out == (
        f"{_PROMPT}cat > /app/a.py << 'EOF'\n"
        "[4 lines you typed were echoed back; omitted]\n> EOF\n"
        f"{_PROMPT}python3 /app/a.py\nok\n{_PROMPT}"
    )


def test_blank_lines_and_typeahead_inside_a_run_are_echo() -> None:
    text = f"{_PROMPT}cat > a << 'EOF'\n> import os\n\na> def f():\n>     return 1\n> EOF\n{_PROMPT}"
    out, removed = _collapse_echo(text, _TYPED, 3)
    assert removed == 4 and "[4 lines you typed" in out


def test_unterminated_heredoc_is_left_whole() -> None:
    text = f"{_PROMPT}cat > a << 'EOF'\n> import os\n>\n> def f():\n>     return 1\n"
    assert _collapse_echo(text, _TYPED, 3) == (text, 0)


def test_lines_not_typed_are_not_collapsed() -> None:
    # Program output that happens to look like a continuation prompt.
    text = "> quoted one\n> quoted two\n> quoted three\n> quoted four\ndone\n"
    assert _collapse_echo(text, _TYPED, 3) == (text, 0)


def test_short_runs_and_disabled() -> None:
    text = f"{_PROMPT}cat > a << 'EOF'\n> import os\n> EOF\n{_PROMPT}"
    assert _collapse_echo(text, _TYPED, 3) == (text, 0)
    long_text = f"{_PROMPT}x\n> import os\n>\n> def f():\n>     return 1\n> EOF\n{_PROMPT}"
    assert _collapse_echo(long_text, _TYPED, 0) == (long_text, 0)
    assert _collapse_echo(long_text, "", 3) == (long_text, 0)


def test_typed_keys_reads_send_keys() -> None:
    assert _typed_keys("tmux send-keys -t terminus-2 -- 'ls -la\n' Enter") == "ls -la\n\n"
    assert _typed_keys("tmux send-keys -t terminus-2 C-c") == ""
    assert _typed_keys("timeout 30 tmux send-keys -t s 'a' 'b'") == "ab"
    assert _typed_keys("tmux capture-pane -p") == ""
