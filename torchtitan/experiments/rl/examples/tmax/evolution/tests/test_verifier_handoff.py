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


def test_blind_session_starts_from_previous_revision_not_authors_draft(tmp_path):
    # The author may edit tests/ as a scratch checker; the blind layout must
    # replace that draft with the previous revision's tests.
    seed, package, verifier = tmp_path / "r0", tmp_path / "package", tmp_path / "v"
    for root, text in ((seed, "exit 0\n"), (package, "grep secret_name out\n")):
        (root / "tests").mkdir(parents=True)
        (root / "tests/test.sh").write_text(text)
    (package / "tests/helper.py").write_text("# author's scratch helper\n")
    verifier.mkdir()
    (verifier / "tests").mkdir()
    (verifier / "tests/test.sh").write_text("grep secret_name out\n")
    ec._restore_seed_tests(verifier, ec._seed_tests({"_seed_dir": str(seed)}))
    assert (verifier / "tests/test.sh").read_text() == "exit 0\n"
    assert not (verifier / "tests/helper.py").exists()


def test_unchanged_verifier_is_judged_against_previous_revision(tmp_path):
    # The author's draft differs from the seed, so comparing the blind
    # author's tests against the package would let "changed nothing" through.
    seed, package, verifier = tmp_path / "r0", tmp_path / "package", tmp_path / "v"
    for root, text in ((seed, "exit 0\n"), (package, "draft\n"), (verifier, "exit 0\n")):
        (root / "tests").mkdir(parents=True)
        (root / "tests/test.sh").write_text(text)
    with pytest.raises(RuntimeError, match="changed nothing"):
        ec._take_verifier(
            verifier, package, "tests/test.sh", "exit 0\n", seed_tests=seed / "tests"
        )
    assert (package / "tests/test.sh").read_text() == "draft\n"
    (verifier / "tests/test.sh").write_text("exit 0\n# adds the new check\n")
    (package / "run").mkdir()
    ec._take_verifier(
        verifier, package, "tests/test.sh", "exit 0\n", seed_tests=seed / "tests"
    )
    assert (package / "tests/test.sh").read_text() == "exit 0\n# adds the new check\n"
