from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from torchtitan.experiments.rl.examples.tmax import layout

_NEW_ROOT = Path(__file__).resolve().parents[1] / "examples" / "tmax" / "new_root.py"


def _new_root():
    ns = runpy.run_path(str(_NEW_ROOT), run_name="not_main")
    return ns["create"]


def _seed(tmp_path: Path) -> Path:
    mix = tmp_path / "seed.jsonl"
    mix.write_text(
        '{"label": "tw_1", "metadata": {"instance_id": "tw_1"}}\n'
        '{"label": "tw_2", "metadata": {"instance_id": "tw_2", "rev": 3}}\n'
    )
    return mix


def test_create_publishes_v1_with_rev_zero_and_links_sources(tmp_path: Path) -> None:
    create = _new_root()
    src = tmp_path / "tw-extract"
    (src / "tasks").mkdir(parents=True)

    root = create(tmp_path / "root", mix=_seed(tmp_path), sources=[src], bin_dir=None,
                  name=None, purpose="t", profile="andy", fork_from=None)

    version, path = root.mix.live_version()
    assert version == 1 and path.name.startswith("v0001--")
    rows = [json.loads(l) for l in root.mix.live.read_text().splitlines()]
    assert [r["metadata"]["rev"] for r in rows] == [0, 3]  # a row's own rev is kept
    manifest = json.loads(layout.MixDir.manifest_of(path).read_text())
    assert manifest["version"] == 1 and manifest["rows"] == 2 and manifest["parent_version"] is None
    assert (root.data / "sources" / "tw-extract").resolve() == src.resolve()
    exp = json.loads(root.experiment_json.read_text())
    assert exp["name"] == "root" and exp["seed_mix_version"] == 1 and exp["forked_from"] is None
    assert root.runs.is_dir() and root.evals.is_dir() and root.evolution.tasks.is_dir()


def test_fork_shares_history_by_hardlink_and_records_origin(tmp_path: Path) -> None:
    create = _new_root()
    origin = create(tmp_path / "origin", mix=_seed(tmp_path), sources=[], bin_dir=None,
                    name=None, purpose="", profile=None, fork_from=None)
    (origin.evolution.task("tw_1").rev(0)).mkdir(parents=True)
    (origin.evolution.task("tw_1").rev(0) / "instruction.md").write_text("seed")

    fork = create(tmp_path / "fork", mix=None, sources=[], bin_dir=None, name=None,
                  purpose="", profile=None, fork_from=origin.path)

    assert fork.mix.live_version()[0] == 1
    assert os.stat(fork.mix.live).st_ino == os.stat(origin.mix.live).st_ino
    assert (fork.evolution.task("tw_1").rev(0) / "instruction.md").read_text() == "seed"
    assert json.loads(fork.experiment_json.read_text())["forked_from"] == str(origin.path)


def test_create_refuses_an_existing_root(tmp_path: Path) -> None:
    create = _new_root()
    create(tmp_path / "root", mix=_seed(tmp_path), sources=[], bin_dir=None,
           name=None, purpose="", profile=None, fork_from=None)

    with pytest.raises(SystemExit):
        create(tmp_path / "root", mix=_seed(tmp_path), sources=[], bin_dir=None,
               name=None, purpose="", profile=None, fork_from=None)
