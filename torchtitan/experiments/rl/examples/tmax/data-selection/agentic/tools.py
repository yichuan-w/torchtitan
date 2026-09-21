#!/usr/bin/env python
"""Corpus lookup / search CLI for the reranking agents.

The shortlist in work/candidates/<tb_id>.json is recall-oriented and may miss
things. This gives an agent the whole 56,307-row pool to search on its own.

  tools.py show <task_id> [...]            full instruction of those tasks
  tools.py full <task_id>                  full composed doc (tests, oracle, env)
  tools.py search <corpus|all> <regex>     regex over instructions -> id + snippet
  tools.py words <corpus|all> <w1> <w2>..  rank docs by how many of the words hit
  tools.py tb <tb_id>                      the TB2.1 query task itself
  tools.py sources                         corpus names and sizes

<corpus> accepts a prefix: tmax, rts, tw, smith, rebench.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "work" / "cache"
DB = HERE / "work" / "pool.sqlite"
CORPORA = {
    "TMax-15K": "tmax",
    "Terminal-Lego-15k": "lego",
    "CalibForge": "calib",
    "Recursive-Task-Synthesis": "rts",
    "TerminalWorld-Seeds-Clean": "tw",
    "SWE-Smith-Seeds-Clean": "smith",
    "SWE-Rebench-Tasks-Clean": "rebench",
}
SECTION_RE = re.compile(
    r"(?m)^(?:INSTRUCTION|META|VERIFIER|ASSETS|ORACLE|DOCKERFILE|TESTS)$"
)


def instruction_of(row: dict) -> str:
    inst = row.get("instruction")
    if inst:
        return inst.strip()
    text = row.get("text") or ""
    m = SECTION_RE.search(text)
    if not m or m.group(0) != "INSTRUCTION":
        return text[:8000]
    body = text[m.end() :]
    nxt = SECTION_RE.search(body)
    return (body[: nxt.start()] if nxt else body).strip()[:8000]


def build_db() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute(
        "CREATE TABLE doc (task_id TEXT PRIMARY KEY, source TEXT, "
        "instruction TEXT, full TEXT, origin TEXT)"
    )
    for name in list(CORPORA) + ["TB2.1"]:
        path = CACHE / (f"{name}.jsonl" if name != "TB2.1" else "tb21_docs.jsonl")
        rows = []
        with path.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                rows.append(
                    (
                        r["task_id"],
                        name,
                        instruction_of(r),
                        r.get("text", ""),
                        r.get("origin", ""),
                    )
                )
        con.executemany("INSERT OR REPLACE INTO doc VALUES (?,?,?,?,?)", rows)
        print(f"[tools] indexed {name}: {len(rows)}", file=sys.stderr)
    con.execute("CREATE INDEX idx_src ON doc(source)")
    con.commit()
    con.close()
    tmp.replace(DB)


def con() -> sqlite3.Connection:
    if not DB.exists():
        build_db()
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def resolve(name: str) -> list[str]:
    if name in ("all", "*"):
        return list(CORPORA)
    n = name.lower()
    hits = [c for c, alias in CORPORA.items() if alias == n or c.lower() == n]
    if not hits:
        hits = [c for c in CORPORA if n in c.lower()]
    if not hits:
        sys.exit(f"unknown corpus {name!r}; choose from {list(CORPORA.values())}")
    return hits


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    c = con()

    if cmd == "sources":
        for src, n in c.execute("SELECT source, COUNT(*) FROM doc GROUP BY source"):
            print(f"{src:28} {n:>6}  alias={CORPORA.get(src, '-')}")
        return 0

    if cmd in ("show", "full", "tb"):
        col = "full" if cmd == "full" else "instruction"
        for tid in rest:
            row = c.execute(
                f"SELECT source, {col} FROM doc WHERE task_id=?", (tid,)
            ).fetchone()
            if not row:
                print(f"=== {tid}\n!! NOT FOUND\n")
                continue
            print(f"=== {tid}  [{row[0]}]\n{row[1]}\n")
        return 0

    if cmd == "search":
        if len(rest) < 2:
            sys.exit("usage: search <corpus|all> <regex> [-n N]")
        srcs, pat = resolve(rest[0]), rest[1]
        n = int(rest[rest.index("-n") + 1]) if "-n" in rest else 30
        rx = re.compile(pat, re.I)
        hits = 0
        q = f"SELECT task_id, source, instruction FROM doc WHERE source IN ({','.join('?' * len(srcs))})"
        for tid, src, inst in c.execute(q, srcs):
            m = rx.search(inst)
            if not m:
                continue
            hits += 1
            if hits > n:
                continue
            lo = max(0, m.start() - 120)
            print(f"{tid}\t[{src}]\t...{inst[lo:m.end() + 220]}...".replace("\n", " "))
        print(f"\n-- {hits} match(es); showed up to {n}", file=sys.stderr)
        return 0

    if cmd == "words":
        if len(rest) < 2:
            sys.exit("usage: words <corpus|all> <word> [word ...] [-n N]")
        srcs = resolve(rest[0])
        n = int(rest[rest.index("-n") + 1]) if "-n" in rest else 30
        words = [w for w in rest[1:] if w != "-n" and not w.isdigit()]
        rxs = [re.compile(re.escape(w), re.I) for w in words]
        scored = []
        q = f"SELECT task_id, source, instruction FROM doc WHERE source IN ({','.join('?' * len(srcs))})"
        for tid, src, inst in c.execute(q, srcs):
            k = sum(1 for rx in rxs if rx.search(inst))
            if k:
                scored.append((k, tid, src, inst))
        scored.sort(key=lambda x: -x[0])
        for k, tid, src, inst in scored[:n]:
            print(f"{k}/{len(words)}\t{tid}\t[{src}]\t{inst[:220]}".replace("\n", " "))
        print(f"\n-- {len(scored)} doc(s) matched >=1 word", file=sys.stderr)
        return 0

    sys.exit(f"unknown command {cmd!r}; see --help")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
