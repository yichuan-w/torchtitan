# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""Harbor's send-keys chunks stay under tmux's byte limit for non-ASCII text."""

from types import SimpleNamespace

import pytest

pytest.importorskip("harbor")

from harbor.agents.terminus_2.tmux_session import TmuxSession  # noqa: E402

from torchtitan.experiments.rl.harness.agents.terminus import (  # noqa: E402
    _cap_tmux_send_keys_length,
)

# tmux's client message limit (MAX_IMSGSIZE) less some header room.
_TMUX_COMMAND_BYTES = 16 * 1024 - 256


def test_multibyte_heredoc_chunks_fit_tmux(monkeypatch) -> None:
    monkeypatch.setattr(TmuxSession, "_TMUX_SEND_KEYS_MAX_COMMAND_LENGTH", 16_000)
    _cap_tmux_send_keys_length()
    heredoc = "cat > /app/x.py << 'EOF'\n" + "# 分片 ── 检查\n" * 3000 + "EOF\n"
    commands = TmuxSession._tmux_send_keys(
        SimpleNamespace(
            _session_name="terminus-2",
            _TMUX_SEND_KEYS_MAX_COMMAND_LENGTH=(
                TmuxSession._TMUX_SEND_KEYS_MAX_COMMAND_LENGTH
            ),
            _split_key_for_tmux=TmuxSession._split_key_for_tmux,
        ),
        [heredoc, "Enter"],
    )
    assert len(commands) > 1
    assert max(len(c.encode()) for c in commands) <= _TMUX_COMMAND_BYTES
