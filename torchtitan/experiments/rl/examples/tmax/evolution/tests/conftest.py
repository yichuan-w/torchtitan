"""Let this suite run on a host without torch.

The modules under test are stdlib (plus pyarrow), but they import from the
torchtitan package tree, and torchtitan/experiments/rl/__init__.py imports the
training stack. With torch present nothing here runs. Without it, the package
levels that would import torch are replaced by empty packages whose __path__
points at the real directories, so ``from torchtitan.experiments.rl.examples.tmax
import layout`` still loads the real layout.py; the Daytona agent module, which
needs the SDK, is replaced by one whose boot raises -- the tests that reach it
replace boot_agent_sandbox with a fake.
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_PKG = Path(__file__).resolve().parents[6]  # <checkout>/torchtitan


def _stub(name: str, path: Path | None, **attrs) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    if path is not None:
        mod.__path__ = [str(path)]
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    parent, _, leaf = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], leaf, mod)
    return mod


def _no_daytona(*_a, **_k):
    raise RuntimeError(
        "no Daytona SDK on this host; a test that boots must replace boot_agent_sandbox"
    )


def _torchless() -> None:
    root = str(_PKG.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import torch  # noqa: F401

        return
    except ImportError:
        pass
    importlib.import_module("torchtitan.experiments")  # the real one: nothing heavy
    _stub("torchtitan.experiments.rl", _PKG / "experiments/rl")
    importlib.import_module("torchtitan.experiments.rl.examples")  # real, empty
    _stub(
        "torchtitan.experiments.rl.examples.tmax", _PKG / "experiments/rl/examples/tmax"
    )
    _stub("torchtitan.experiments.rl.harness", _PKG / "experiments/rl/harness")
    _stub(
        "torchtitan.experiments.rl.harness.agents",
        _PKG / "experiments/rl/harness/agents",
    )
    _stub(
        "torchtitan.experiments.rl.harness.agents.claude_code",
        None,
        boot_agent_sandbox=_no_daytona,
    )


_torchless()
