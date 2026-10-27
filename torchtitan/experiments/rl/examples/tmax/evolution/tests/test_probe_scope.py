"""Independent controls are scoped to the clauses a revision changed."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import evolve_codex as ec  # noqa: E402


def _probe(tmp_path, cases):
    probe = tmp_path / "probe"
    d = probe / "run/verifier-probes"
    d.mkdir(parents=True)
    (d / "correct.sh").write_text("echo ok\n")
    for i, _ in enumerate(cases, 1):
        (d / f"wrong-{i}.sh").write_text(f"echo wrong {i}\n")
    (d / "contract.json").write_text(json.dumps({"cases": cases}))
    return probe


def test_cases_quoting_kept_text_are_dropped_and_the_rest_renumbered(tmp_path):
    prev = tmp_path / "prev"
    prev.mkdir()
    (prev / "instruction.md").write_text("Write the sum to /app/out.txt.\nUse  two decimals.\n")
    cases = [
        {"quote": "Use two decimals.", "source": "instruction.md"},
        {"quote": "Sort rows by date.", "source": "instruction.md"},
    ]
    probe = _probe(tmp_path, cases)
    dropped = ec._scope_cases(probe, prev)
    d = probe / "run/verifier-probes"
    contract = json.loads((d / "contract.json").read_text())
    assert [c["quote"] for c in dropped] == ["Use two decimals."]
    assert [c["quote"] for c in contract["cases"]] == ["Sort rows by date."]
    assert (d / "wrong-1.sh").read_text() == "echo wrong 2\n"
    assert not (d / "wrong-2.sh").exists()


def test_nothing_dropped_when_every_quote_is_new(tmp_path):
    prev = tmp_path / "prev"
    prev.mkdir()
    (prev / "instruction.md").write_text("old text\n")
    probe = _probe(tmp_path, [{"quote": "new clause", "source": "instruction.md"}])
    assert ec._scope_cases(probe, prev) == []
    assert (probe / "run/verifier-probes/wrong-1.sh").exists()


def test_declared_no_changed_requirement(tmp_path):
    probe = _probe(tmp_path, [])
    d = probe / "run/verifier-probes/contract.json"
    d.write_text(json.dumps({"cases": [], "no_changed_requirement": "added a hint only"}))
    assert ec._no_changed_requirement(probe)
    d.write_text(json.dumps({"cases": []}))
    assert not ec._no_changed_requirement(probe)
