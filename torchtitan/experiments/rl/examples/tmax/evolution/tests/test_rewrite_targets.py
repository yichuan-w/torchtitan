# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""#306 items 1, 2 and 6: rewrite targets, rewriter context and the harder
slot share, each behind a flag that leaves the prompts and the scheduler as
they were when unset."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import simplify_operators as so

FLAGS = (
    "EVOLVE_EASIER_TARGET",
    "EVOLVE_REWRITE_CONTEXT",
    "EVOLVE_HARDER_TARGET",
    "EVOLVE_HARDER_SLOT_FRAC",
)


@pytest.fixture(autouse=True)
def _flags_unset(monkeypatch):
    for name in FLAGS:
        monkeypatch.delenv(name, raising=False)


# -- item 1: easier target ---------------------------------------------------


@pytest.mark.parametrize("hint", ["none", "vague", "specific"])
def test_easier_prompt_without_the_flag_has_no_target(hint) -> None:
    text = so.prompt(hint, 12)
    assert text == so.prompt(hint) == so.prompt(hint, 8)
    assert "Aim for" not in text
    assert "size\nthe change to what the attempts show.\n\nThe student works" in text


def test_easier_target_names_the_band_and_the_bias(monkeypatch) -> None:
    monkeypatch.setenv("EVOLVE_EASIER_TARGET", "6-7")
    text = so.prompt("vague", 12)
    assert "Aim for 6 to 7 of 12 solved in that next group" in text
    assert "predicted about 2\nmore solves in 12 than the next group produced" in text
    # The target sits in the goal, before the budget paragraph.
    assert text.index("Aim for 6 to 7") < text.index("The student works within")


@pytest.mark.parametrize(
    "spec,n,band",
    [("6-7", 12, (6, 7)), ("6-7", 8, (4, 5)), ("6-7", 16, (8, 9)), ("3-5", 12, (3, 5)),
     ("3-5", 4, (1, 2)), ("1-11", 2, (1, 1))],
)
def test_target_band_scales_to_the_group(monkeypatch, spec, n, band) -> None:
    monkeypatch.setenv("EVOLVE_EASIER_TARGET", spec)
    assert so.target_band("EVOLVE_EASIER_TARGET", n) == band


@pytest.mark.parametrize("spec", ["6", "7-6", "0-3", "6-12", "half"])
def test_target_band_rejects_a_malformed_band(monkeypatch, spec) -> None:
    monkeypatch.setenv("EVOLVE_EASIER_TARGET", spec)
    with pytest.raises(ValueError, match="EVOLVE_EASIER_TARGET"):
        so.prompt("vague", 12)
