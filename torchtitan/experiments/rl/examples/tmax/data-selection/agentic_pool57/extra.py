"""Stage 2b: add corpora that are not in the search pool (Turing Labs).

Turing's 2026-09-20 delivery (copied to work/extra/turing) is training-TB2 (20
expert TB2-style tasks) plus samples/ (18 tasks in six categories, many
multi-step: one instruction per steps/NN-*). Each task becomes one
representative with id x<NNN>; a multi-step task's text is its steps'
instructions in order. The contamination screen is dedup.py's, against the same
89 TB2.1 queries. Rows are appended to work/reps.jsonl (original kept as
work/reps.base.jsonl) and the full text (instruction, tests, solution) goes to
work/extra_docs.jsonl for tools.py full.
"""

from __future__ import annotations

import json
import re
import shutil

import numpy as np

import dedup
from common import ROOT, log, normalize, read_jsonl

SOURCE = "Turing-Labs"
GROUP = "Terminal-env"
# Tasks whose reference solution failed oracle validation (2026-08), one name per
# line; kept beside the data rather than here because the task names are not public.
ORACLE_FAILED_FILE = "work/extra/turing/oracle_failed.txt"


def task_roots(base):
    for t in sorted((base / "training-TB2").iterdir()):
        if t.is_dir():
            yield f"training-TB2/{t.name}", t
    for cat in sorted((base / "samples").iterdir()):
        for t in sorted(cat.iterdir()):
            if t.is_dir():
                inner = t / t.name
                yield f"samples/{cat.name}/{t.name}", inner if inner.is_dir() else t


def read(p, cap=20000):
    try:
        return p.read_text(errors="replace")[:cap]
    except OSError:
        return ""


def compose(root):
    steps = sorted((root / "steps").glob("*/instruction.md")) if (root / "steps").is_dir() else []
    if steps:
        text = "\n\n".join(f"## Step {p.parent.name}\n\n{read(p)}" for p in steps)
    else:
        text = read(root / "instruction.md")
    full = [f"INSTRUCTION\n{text}"]
    for sub in ("tests", "solution"):
        for p in sorted((root / sub).rglob("*")) if (root / sub).is_dir() else []:
            if p.is_file() and p.suffix in (".py", ".sh", ".md", ".toml", ".txt", "") and p.stat().st_size < 200_000:
                full.append(f"{sub.upper()} {p.relative_to(root)}\n{read(p)}")
    if (root / "task.toml").exists():
        full.append(f"META task.toml\n{read(root / 'task.toml')}")
    return text.strip(), "\n\n".join(full)


def main():
    work = ROOT / "work"
    base = work / "extra" / "turing"
    np.seterr(over="ignore")
    queries = list(read_jsonl(work / "queries.jsonl"))
    qsets = [dedup.shingles(q["instruction"]) for q in queries]
    names = {q["name"] for q in queries}
    qnorm = {normalize(q["instruction"]) for q in queries}
    failed_file = ROOT / ORACLE_FAILED_FILE
    oracle_failed = set(failed_file.read_text().split()) if failed_file.exists() else set()
    if not (work / "reps.base.jsonl").exists():
        shutil.copyfile(work / "reps.jsonl", work / "reps.base.jsonl")
    reps, docs, contam = [], [], []
    for n, (tid, root) in enumerate(task_roots(base), 1):
        text, full = compose(root)
        if not text:
            log("extra", item=tid, status="skip", reason="no_instruction"); continue
        xid = f"x{n:03d}"
        sh = dedup.shingles(text)
        cont = max(float(np.isin(q, sh).mean()) for q in qsets)
        hit = sorted({seg for seg in re.split(r"[/:]", tid) if seg in names})
        if hit or normalize(text) in qnorm or cont >= dedup.CONTAM:
            contam.append({"id": xid, "task_id": tid, "name_hit": hit, "max_containment": round(cont, 4)})
            log("extra", item=tid, status="excluded", reason="tb21_overlap", containment=round(cont, 4)); continue
        note = "oracle_failed_2026-08" if root.name in oracle_failed else ""
        reps.append({"id": xid, "group": GROUP, "text": text, "occ": [[SOURCE, tid, "turing-delivery-20260920"]],
                     "family": [xid], "note": note})
        docs.append({"id": xid, "task_id": tid, "text": text, "full": full, "note": note})
        log("extra", item=tid, status="added", id=xid, chars=len(text), max_containment=round(cont, 4), note=note or "-")
    base_ids = {d["id"] for d in read_jsonl(work / "reps.base.jsonl")}
    with (work / "reps.jsonl").open("w") as fh:
        for d in read_jsonl(work / "reps.base.jsonl"):
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
        for d in reps:
            assert d["id"] not in base_ids
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with (work / "extra_docs.jsonl").open("w") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    (work / "extra_contamination.json").write_text(json.dumps(contam, indent=1))
    log("extra", item="done", added=len(reps), excluded=len(contam))


if __name__ == "__main__":
    main()
