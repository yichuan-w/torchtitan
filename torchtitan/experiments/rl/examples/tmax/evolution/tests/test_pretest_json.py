"""pretest.json: the seed row's pin hook as the loop snapshots it beside
rewrite.json, and how the sandbox tool and the probe read it back."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_sandbox as asb
from torchtitan.experiments.rl.examples.tmax import layout

HOOK = "set -u\nexit 0\n"
STAMP = "image:hamishi740/swerl-tmax-v3:37a79d0fd9b9"


def test_pretest_json_sits_beside_rewrite_json_and_round_trips(tmp_path) -> None:
    rw = layout.RewriteDir(tmp_path / "20260905-120000Z--harder")
    assert rw.pretest == rw.path / "pretest.json"
    # Outside package/, out of the agent's reach.
    assert rw.pretest.parent == rw.meta.parent
    rw.path.mkdir()
    layout.write_pretest(rw.pretest, HOOK, STAMP)
    assert json.loads(rw.pretest.read_text()) == {
        "pre_test_sh": HOOK, "pretest_env_identity": STAMP}
    assert layout.read_pretest(rw.pretest) == (HOOK, STAMP)


def test_read_pretest_is_none_for_a_missing_or_empty_hook(tmp_path) -> None:
    assert layout.read_pretest(tmp_path / "absent.json") is None
    p = tmp_path / "empty.json"
    layout.write_pretest(p, "", "")
    assert layout.read_pretest(p) is None            # a row with no check: nothing to run
    p.write_text("not json")
    assert layout.read_pretest(p) is None


def test_sandbox_tool_reads_the_hook_the_harness_left_in_run(tmp_path) -> None:
    pkg = tmp_path / "package"
    (pkg / "run").mkdir(parents=True)
    assert asb._pretest_of(pkg) is None               # a package driven by hand: no hook
    layout.write_pretest(pkg / "run" / "pretest.json", HOOK, STAMP)
    assert asb._pretest_of(pkg) == (HOOK, STAMP)
