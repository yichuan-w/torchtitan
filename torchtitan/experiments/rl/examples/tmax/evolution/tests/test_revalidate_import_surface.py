"""agent_sandbox reaches daytona_revalidate's names through `dr.<name>`; every one of them must exist
on the REAL module. The training side hit the gap this guards: `./sandbox check` called
dr.protected_entries_of, which daytona_revalidate imported nothing of, and every reaudit task that
carried the protected_* columns died at sandbox boot with AttributeError. The fake revalidator the
seam tests inject exposed the name, so they stayed green; this test asks the real module."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_sandbox as asb
import daytona_revalidate as dr
import pytest


@pytest.fixture(autouse=True)
def _checkout(monkeypatch):
    # pack_to_dataset resolves the checkout's own adapters through TRL_TT (the seam tests'
    # fake revalidator loads integrity_baseline that way); this file sits inside the checkout.
    monkeypatch.setenv("TRL_TT", str(Path(__file__).resolve().parents[7]))


_DR_REF = re.compile(r"\bdr\.([A-Za-z_][A-Za-z0-9_]*)")


def referenced_names() -> set[str]:
    return set(_DR_REF.findall(Path(asb.__file__).read_text(encoding="utf-8")))


def test_every_dr_name_the_sandbox_tool_uses_exists_on_the_real_revalidator() -> None:
    names = referenced_names()
    assert names, "agent_sandbox references no dr.<name>: the regex or the module moved"
    assert (
        "protected_entries_of" in names
    )  # the reference that broke: it stays under this test
    missing = sorted(n for n in names if not hasattr(dr, n))
    assert (
        not missing
    ), f"agent_sandbox uses dr.{missing} but daytona_revalidate does not provide them"


def test_the_fake_revalidator_in_the_seam_tests_is_not_wider_than_the_real_one() -> None:
    """The seam tests' fake exposes only names the real module also has (a fake wider than the
    real thing is how the AttributeError hid)."""
    import test_protected_loop as tpl

    fake = tpl._fake_revalidator([])
    exposed = {n for n in vars(fake) if not n.startswith("__")}
    assert exposed <= {n for n in dir(dr)}, sorted(exposed - set(dir(dr)))
