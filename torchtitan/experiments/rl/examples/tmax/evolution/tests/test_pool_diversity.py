"""The diversity audit reads what a verifier demands, and only as a paired delta."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pool_diversity as pdv

VERIFIER = '''
import json, subprocess, tarfile

def test_report():
    assert (tmp / "report.json").exists()
    data = json.loads((tmp / "report.json").read_text())
    assert "total" in data
    assert data["count"] > 3
    out = subprocess.run(["git", "status"], capture_output=True)
    assert out.returncode == 0
'''


def test_fingerprint_reads_checks_modules_and_tools() -> None:
    fp = pdv.fingerprint(VERIFIER)
    assert {"json", "archive", "subprocess", "path-exists", "file-content",
            "membership", "numeric-bound", "git"} <= fp


def test_a_shell_verifier_still_yields_its_tools() -> None:
    # Not Python, so the AST pass cannot run; tool names are what is left.
    fp = pdv.fingerprint("#!/bin/bash\ntar -tf out.tar | grep -q payload\n")
    assert fp == {"tar", "grep"}


def test_the_interpreter_and_shell_builtins_are_not_capabilities() -> None:
    assert pdv.fingerprint('x = "python3 -m pytest"; y = "cat f"') == {"pytest"}


def _mix(tmp_path: Path, rows: dict[str, str], name: str = "mix.jsonl") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / name
    with p.open("w") as fh:
        for label, src in rows.items():
            fh.write(json.dumps({"label": label,
                                 "metadata": {"tmax": {"fixtures": {"tests/t.py": src}}}}) + "\n")
    return p


def test_ids_selects_and_invert_gives_the_complement(tmp_path: Path) -> None:
    mix = _mix(tmp_path, {"a": "import json", "b": "import tarfile", "c": "import sqlite3"})
    assert set(pdv.pool_fingerprints(mix, {"a", "b"})) == {"a", "b"}
    assert set(pdv.pool_fingerprints(mix, {"a", "b"}, invert=True)) == {"c"}


def test_a_pool_of_clones_scores_higher_than_a_varied_one(tmp_path: Path) -> None:
    same = {c: "import json" for c in "abcd"}
    varied = {"a": "import json", "b": "import tarfile",
              "c": "import sqlite3", "d": "import csv"}
    clones = pdv.report(_mix(tmp_path / "clones", same))
    spread = pdv.report(_mix(tmp_path / "spread", varied))
    assert clones["near_duplicate"] == 1.0 and spread["near_duplicate"] == 0.0
    assert clones["concentration"] > spread["concentration"]
    assert clones["coverage"] < spread["coverage"]


def test_a_row_whose_verifier_yields_nothing_is_left_out(tmp_path: Path) -> None:
    mix = _mix(tmp_path, {"a": "import json", "b": "x = 1"})
    assert set(pdv.pool_fingerprints(mix)) == {"a"}
