"""The unseen-literal audit: what counts as a requirement, what counts as
visible, and the five hidden-contract shapes it exists to catch."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verifier_literals as vl


def test_report_keys_the_verifier_reads_are_requirements() -> None:
    src = '''
import json
report = json.load(open("/app/artifacts/processing_report.yaml"))
assert report["source"] == "toto.yaml"
assert report.get("input_records") == 286
for key in ("unique_algorithms", "duplicate_records_removed"):
    assert key in report
'''
    lits = vl.extract(src)
    assert {"source", "input_records", "unique_algorithms", "duplicate_records_removed"} <= lits
    # A README that describes the report in prose does not state the keys.
    # `source` slips through: the prose uses the word, and a word match is the
    # only test there is. The compound keys do not.
    readme = ("Copy the input to toto.yaml. processing_report.yaml: source basename "
              "and SHA-256, input and unique counts.")
    assert vl.unseen(src, "python", readme) == [
        "duplicate_records_removed", "input_records", "unique_algorithms"]
    # Stating them does.
    readme2 = readme + "\nKeys: source, input_records, unique_algorithms, duplicate_records_removed."
    assert vl.unseen(src, "python", readme2) == []


def test_regex_anchors_and_dict_comparisons_and_extensions() -> None:
    src = '''
import re
expected = {"commit": git("rev-parse", "HEAD")}
content = open("/app/history-report.txt").read()
assert re.search(rf"(?mi)^Commit:\\s*{expected['commit']}\\s*$", content)
assert re.search(r"(?m)^Source blob:\\s*[0-9a-f]{40}$", content)
manifest = json.load(open("/app/dist/manifest.json"))
assert manifest == {"artifact": {"filename": name, "sha256": digest, "size_bytes": size}}
relative_path = element.attrib["file"]
assert relative_path == f"{contract['target']['stream_directory']}/{name}.bin"
'''
    got = vl.unseen(src, "python", "Write an audit record identifying the commit and its parent.")
    assert "Commit:" in got and "Source blob:" in got
    assert "size_bytes" in got and ".bin" in got
    # The verifier's own `expected` dict is its bookkeeping, not a demand.
    assert "commit" not in got
    # `commit` as a word in prose is not the label `Commit:`; the label has to appear.
    assert vl.unseen(src, "python", "Lines: `Commit: <sha>`, `Source blob: <sha>`; "
                     "manifest key artifact with filename, sha256, size_bytes; the contract's "
                     "target.stream_directory; each stream element has a file attribute, "
                     "streams/<name>.bin") == []


def test_what_the_verifier_plants_is_not_a_requirement() -> None:
    src = '''
mutation = {"name": "Avery Graham", "username": "avery", "email": "a@b.c"}
(APP / "data/users.json").write_text(json.dumps([mutation]))
subprocess.run(["python3", "/app/process_users.py", "--strict"], check=True)
assert "* Avery\\n" in OUTPUT.read_text()
assert OUTPUT.read_text() != "", "output must not be empty"
'''
    got = vl.extract(src)
    assert "Avery Graham" not in got and "* Avery" not in got and "--strict" not in got
    assert "output must not be empty" not in got


def test_seed_baseline_and_visibility_are_word_bounded() -> None:
    src = 'assert report["real"] > 0\nassert report["timing"] > 0\n'
    # "realistic" does not state the key "real"; the seed already used "timing".
    assert vl.unseen(src, "python", "a realistic workload", baseline={"timing"}) == ["real"]
    assert vl.unseen(src, "python", "the real elapsed time", baseline={"timing"}) == []


def test_flags_paths_and_phrases_are_left_to_other_checks() -> None:
    src = '''
cmd = ["git", "log", "--all", "--name-status"]
assert open("/app/out.txt").read().startswith("Artifact reconciliation report")
'''
    got = vl.unseen(src, "python", "")
    assert "--all" not in got and "--name-status" not in got
    assert "Artifact reconciliation report" not in got     # a phrase, not a key shape


def test_audit_package_reads_the_build_context(tmp_path) -> None:
    pkg = tmp_path / "pkg"
    (pkg / "environment" / "docs").mkdir(parents=True)
    (pkg / "tests").mkdir()
    (pkg / "instruction.md").write_text("Follow /app/docs/CONTRACT.md.\n")
    (pkg / "environment" / "Dockerfile").write_text("FROM scratch\nCOPY docs /app/docs\n")
    (pkg / "environment" / "docs" / "CONTRACT.md").write_text("Report keys: source_sha256, checked_streams.\n")
    (pkg / "tests" / "test_state.py").write_text(
        'assert report["source_sha256"]\nassert report["checked_streams"] == 3\nassert report["valid"] is True\n')
    assert vl.audit_package(pkg, "tests/test_state.py") == ["valid"]
