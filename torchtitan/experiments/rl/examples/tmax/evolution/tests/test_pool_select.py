"""pool_select.py's selection rule on synthetic evals: no sandbox, no GPU."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pool_select as ps  # noqa: E402
from test_evolve_bench import _eval, _jsonl, _row  # noqa: E402


def _rows(tasks: dict[str, str], prefix: str = "") -> list[dict]:
    out = []
    for t, corpus in tasks.items():
        r = _row(prefix + t)
        r["metadata"]["corpus"] = corpus
        out.append(r)
    return out


def test_pool_select_rule_conflicts_unmeasured_and_counts(tmp_path, monkeypatch):
    corpora = {"e1": "agentpick-repair", "e2": "agentpick-clean-fast", "h1": "calibforge-clean-v2",
               "both": "agentpick-clean-fast", "mid": "agentpick-clean-fast", "short": "agentpick-repair",
               "k16": "agentpick-calibforge-repair", "h50": "agentpick-clean-fast"}
    entry = {t: c for t, c in corpora.items() if t != "h50"}
    s122 = _jsonl(tmp_path / "e122.jsonl", _rows(entry))
    s9 = _jsonl(tmp_path / "e9.jsonl", _rows(corpora))
    combo = _jsonl(tmp_path / "combo.jsonl", _rows({"h50": "agentpick-clean-fast"}, "BASE__")
                   + _rows({"h50": "agentpick-clean-fast"}, "A0__"))
    F, P = "F" * 12, "P" * 12
    ev122 = _eval(tmp_path, "122", s122, {"e1": F, "e2": "I" + F, "h1": "PF" * 6, "both": F, "mid": "PF" * 6,
                                          "short": "F" * 11 + "I", "k16": "PPPPPPPPPPPPFFFF"})
    evc = _eval(tmp_path, "combo", combo, {"BASE__h50": F, "A0__h50": P})
    ev9 = _eval(tmp_path, "9", s9, {"e1": "PF" * 6, "e2": P, "h1": P, "both": P, "mid": "PF" * 6, "short": P,
                                    "k16": "PPPPPPPPPPPPFFFF", "h50": "PF" * 6})
    m122, meta122, _ = ps.measure([str(ev122), f"BASE__={evc}"])
    m9, meta9, _ = ps.measure([str(ev9)])
    assert "A0__h50" not in m122 and m122["h50"]["passed"] == 0
    assert m122["k16"] == {"passed": 12, "graded": 12, "eval": str(ev122.resolve())}  # first 12 graded
    sel = ps.select(m122, m9)
    # e2: 122B 0/12 (infra not counted) and 9B 12/12 -> both rules: a conflict, like "both".
    assert sel["directions"] == {"e1": "easier", "h1": "harder", "h50": "easier", "k16": "harder"}
    assert sorted(sel["conflict"]) == ["both", "e2"]
    assert sel["unmeasured"] == {"short": {"122b": "0/11", "9b": "12/12"}}
    assert sel["neither"] == ["mid"]
    tsv = tmp_path / "defects.tsv"
    tsv.write_text("task_id\tdefect_class\tfixed\tnote\ne1\thackable\t\tx\n")
    rep = ps.report(sel, {**meta122, **meta9}, ps.labels(tsv))
    assert rep["directions"] == {"easier": 2, "harder": 2} and rep["pool"] == 4
    assert rep["repair_vs_other"] == {"repair": {"easier": 1, "harder": 1}, "other": {"easier": 1, "harder": 1}}
    assert rep["by_defect_class"] == {"hackable": {"easier": 1}}

    monkeypatch.setattr(ps.eb, "_bench_commit", lambda: "test")
    out = tmp_path / "pool" / "map.json"
    subprocess.run([sys.executable, str(Path(ps.__file__)), "--eval-122b", str(ev122), f"BASE__={evc}",
                    "--eval-9b", str(ev9), "--labels", str(tsv), "--out", str(out)], check=True,
                   capture_output=True, text=True)
    assert json.load(open(out)) == sel["directions"]
    full = json.load(open(out.with_name("map.report.json")))
    assert full["conflict"] == sel["conflict"] and full["inputs"]["labels"] == str(tsv.resolve())
