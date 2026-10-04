"""Paths, source groups and logging shared by every stage.

The run directory is wherever these scripts sit on Centralia; the pool is read
in place and never written.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("POOL57_ROOT", Path(__file__).resolve().parent))
POOL = Path("/data/yichuan_wang/terminal-search-pool-20261002/pool.sqlite")
# The 09-29 eight-corpus run: verifier grades, the CalibForge filler
# blocklist, and full task text (tests, oracle) for the eight corpora it held.
PRIOR = Path("/data/yichuan_wang/andy-mimo-tb-eight-corpora-20260929")

# Evaluation is TB2.1 only. A benchmark is excluded when it *is* TB2.1
# (TB2-Verified, TAB, tb21_docs) or is not a terminal task at all
# (AgentWorldBench: single observation turns; WorldVQA: image QA). The other
# benchmarks are fair training candidates; Terminal-Wrench rows that overlap
# TB2.1 are removed by the contamination screen like any other row.
BENCHMARKS_ALLOWED = ["OT-TBLite", "OT-TB-dev", "TB-Pro", "LHTB", "TerminalWorld", "Terminal-Wrench"]

# 32 sources is too many for "10 from each" per query, so per_source is per
# group. Retrieval is still fit per *source* so a small source is not buried.
GROUPS = {
    "TMax": ["TMax-15K", "TMax-SFT"],
    "CalibForge": ["CalibForge"],
    "Terminal-Lego": ["Terminal-Lego-15k"],
    "Bulk-synthetic": ["TaskTrove", "Nemotron-Synthetic-Tasks", "AgentTrove", "Recursive-Task-Synthesis"],
    "SWE-repo": ["SWE-smith", "SWE-Gym", "R2E-Gym", "SWE-Smith-Seeds-Clean", "SWE-Rebench-Tasks-Clean"],
    "Terminal-env": ["CLI-Gym", "Endless-Terminals", "FACET", "LiteCoder-RL", "OT-Agent-RL-5K",
                     "OT-Agent-v1-RL", "SETA", "Skill2Env", "SkillGym", "TermiGen",
                     "TerminalTraj-5k", "MiMo-V2.6-RL-oss", "Turing-Labs"],
    "TerminalWorld": ["TerminalWorld-Seeds-Clean", "TerminalWorld"],
    "Other-benchmarks": ["OT-TBLite", "OT-TB-dev", "TB-Pro", "LHTB", "Terminal-Wrench"],
}
SOURCE_GROUP = {s: g for g, ss in GROUPS.items() for s in ss}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(stage: str, **fields) -> None:
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    line = f"[{now()}] stage={stage} {parts}"
    print(line, flush=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    with (ROOT / "logs" / f"{stage}.log").open("a") as fh:
        fh.write(line + "\n")


def read_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


_CANARY = re.compile(r"<!--.*?-->", re.S)


_EXTRA = None


def extra_docs() -> dict:
    """Tasks added by extra.py (ids x<NNN>); they are not rows of pool.sqlite."""
    global _EXTRA
    if _EXTRA is None:
        p = ROOT / "work" / "extra_docs.jsonl"
        _EXTRA = {d["id"]: d for d in read_jsonl(p)} if p.exists() else {}
    return _EXTRA


def texts_of(ids) -> dict:
    """Instruction text for representative ids: d<N> from the pool, x<N> from extra_docs."""
    import sqlite3
    pool = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
    out = {}
    for i in ids:
        if i.startswith("x"):
            out[i] = extra_docs().get(i, {}).get("text")
        else:
            row = pool.execute("SELECT instruction FROM docs WHERE id=?", (int(i[1:]),)).fetchone()
            out[i] = row[0] if row else None
    return out


def occurrences_of(i) -> list:
    """[(source, original task id)] for a representative id."""
    import sqlite3
    if i.startswith("x"):
        d = extra_docs()[i]
        return [("Turing-Labs", d["task_id"])]
    pool = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
    return pool.execute("SELECT source, task_id FROM occurrences WHERE doc_id=?", (int(i[1:]),)).fetchall()


def normalize(text: str) -> str:
    """Lowercased, canary-stripped, whitespace-collapsed text for comparisons."""
    return " ".join(_CANARY.sub(" ", text or "").lower().split())
