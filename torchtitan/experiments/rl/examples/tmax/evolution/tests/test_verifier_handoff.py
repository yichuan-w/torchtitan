# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec


@pytest.mark.parametrize("entry_changed", [False, True])
def test_shell_verifier_keeps_its_updated_support_files(tmp_path, entry_changed):
    package, verifier = tmp_path / "package", tmp_path / "verifier"
    script = '#!/bin/sh\ncd "$(dirname "$0")"\ntest "$(cat expected.txt)" = new\n'
    for root, expected in ((package, "old"), (verifier, "new")):
        (root / "tests").mkdir(parents=True)
        (root / "tests/test.sh").write_text(script)
        (root / "tests/expected.txt").write_text(expected)
    if entry_changed:
        (verifier / "tests/test.sh").write_text(script + "# New verifier\n")
    (package / "tests/stale-fixture.txt").write_text("removed by verifier")
    (package / "run").mkdir()
    (package / "run/checks.jsonl").write_text('{"reward": 1}\n')
    entry = ec._take_verifier(verifier, package, "tests/test.sh", script)
    result = subprocess.run(["sh", str(package / entry)], check=False)
    assert result.returncode == 0
    assert not (package / "tests/stale-fixture.txt").exists()
    assert not (package / "run/checks.jsonl").exists()


def test_unchanged_verifier_is_rejected_without_removing_tests(tmp_path):
    package, verifier = tmp_path / "package", tmp_path / "verifier"
    for root in (package, verifier):
        (root / "tests").mkdir(parents=True)
        (root / "tests/test.sh").write_text("exit 0\n")
    with pytest.raises(RuntimeError, match="changed nothing"):
        ec._take_verifier(verifier, package, "tests/test.sh", "exit 0\n")
    assert (package / "tests/test.sh").read_text() == "exit 0\n"
