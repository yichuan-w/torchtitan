"""The rollouter sets debconf's frontend to Noninteractive before the agent runs."""

import asyncio
from unittest.mock import AsyncMock

from torchtitan.experiments.rl.examples.tmax import rollouter


def test_debconf_is_set_noninteractive_as_root():
    sandbox = AsyncMock()
    sandbox.exec.return_value = (0, "", "")
    asyncio.run(rollouter._set_debconf_noninteractive(sandbox))
    (cmd,), kwargs = sandbox.exec.call_args
    assert "debconf/frontend select Noninteractive" in cmd
    assert cmd.startswith("command -v debconf-set-selections")
    assert kwargs["user"] == "root" and kwargs["check"] is False


def test_a_failed_debconf_setup_never_fails_the_rollout():
    sandbox = AsyncMock()
    sandbox.exec.side_effect = RuntimeError("sandbox gone")
    asyncio.run(rollouter._set_debconf_noninteractive(sandbox))
