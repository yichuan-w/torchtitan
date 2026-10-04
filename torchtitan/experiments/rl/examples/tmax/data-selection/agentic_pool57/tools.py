#!/usr/bin/env python
"""Lookup / search CLI for the rerank agents over the deduplicated pool.

  tools.py show <id> [...]                 full instruction, sources, family, verifier
  tools.py full <id>                       tests / oracle / env when this snapshot has them
  tools.py search <group|source|all> <regex> [-n N]   ripgrep over instructions
  tools.py words  <group|source|all> <w1> <w2> ... [-n N]  BM25 over the pool's FTS index
  tools.py groups                          group names, their sources, sizes

Ids are representative ids like d123456. A near-duplicate family is shown
once; family_size says how many copies or same-template variants it stands for.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys

from common import GROUPS, POOL, PRIOR, ROOT, SOURCE_GROUP, extra_docs, texts_of

WORK = ROOT / "work"
CAP = 5000
_INDEX = None


class _Index:
    """rep_index.json is 215 MB; agents call this tool dozens of times, so look ids up in sqlite."""

    def __init__(self):
        db = WORK / "rep_index.sqlite"
        if not db.exists():
            tmp = db.with_suffix(".partial")
            con = sqlite3.connect(tmp)
            con.execute("CREATE TABLE r(id TEXT PRIMARY KEY, grp TEXT, j TEXT)")
            con.executemany("INSERT INTO r VALUES (?,?,?)", ((i, v["group"], json.dumps(v)) for i, v in
                            json.loads((WORK / "rep_index.json").read_text()).items()))
            con.execute("CREATE INDEX r_grp ON r(grp)")
            con.commit(); con.close(); tmp.replace(db)
        self.con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def get(self, i):
        row = self.con.execute("SELECT j FROM r WHERE id=?", (i,)).fetchone()
        return json.loads(row[0]) if row else None

    def __getitem__(self, i):
        return self.get(i)

    def count(self, g):
        return self.con.execute("SELECT COUNT(*) FROM r WHERE grp=?", (g,)).fetchone()[0]


def index():
    global _INDEX
    if _INDEX is None:
        _INDEX = _Index()
    return _INDEX


def resolve(name):
    if name in ("all", "*"):
        return list(GROUPS), None
    for g in GROUPS:
        if g.lower() == name.lower():
            return [g], None
    for s in SOURCE_GROUP:
        if s.lower() == name.lower():
            return [SOURCE_GROUP[s]], s
    sys.exit(f"unknown group/source {name!r}; groups: {list(GROUPS)}")


def opt_n(rest, default=30):
    if "-n" in rest:
        i = rest.index("-n"); n = int(rest[i + 1]); del rest[i:i + 2]; return n
    return default


def text_of(ids):
    return texts_of(ids)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    cmd, rest = argv[0], list(argv[1:])
    idx = index()
    if cmd == "groups":
        for g, ss in GROUPS.items():
            print(f"{g:18} reps={idx.count(g):>8}  sources={','.join(ss)}")
        return 0
    if cmd == "show":
        texts = text_of(rest)
        for i in rest:
            r = idx.get(i)
            if not r:
                print(f"=== {i}\n!! NOT A CANDIDATE (unknown id, excluded as TB2.1 overlap, or a non-representative family member)\n"); continue
            print(f"=== {i}  group={r['group']} sources={','.join(r['sources'])} family_size={r['family_size']} verifier={r['verifier']}\n{texts[i]}\n")
        return 0
    if cmd == "full":
        i = rest[0]
        if i.startswith("x"):
            d = extra_docs().get(i)
            print(f"=== {i} ({d['task_id']}, Turing-Labs){' NOTE ' + d['note'] if d and d['note'] else ''}\n{d['full'] if d else '!! NOT FOUND'}")
            return 0
        pool = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
        tids = [t for (t,) in pool.execute("SELECT task_id FROM occurrences WHERE doc_id=?", (int(i[1:]),))]
        prior = sqlite3.connect(f"file:{PRIOR / 'pool.sqlite'}?mode=ro", uri=True)
        for t in tids:
            row = prior.execute("SELECT source, full FROM doc WHERE task_id=?", (t,)).fetchone()
            if row and row[1]:
                print(f"=== {i} ({t}, {row[0]})\n{row[1]}"); return 0
        print(f"=== {i}\n!! tests/oracle/env are not in this snapshot for {tids[:3]}; judge from the instruction (tools.py show).")
        return 0
    if cmd == "search":
        n = opt_n(rest)
        groups, source = resolve(rest[0]); pat = rest[1]
        hits, any_capped = 0, False
        for g in groups:
            # Capped per group: a common word matches millions of aggregator rows.
            p = subprocess.run([str(ROOT / "bin/rg"), "-i", "-m", str(CAP), "-e", pat, str(WORK / "text" / f"{g}.tsv")],
                               capture_output=True, text=True)
            if p.returncode == 2:
                sys.exit(p.stderr.strip())
            lines = p.stdout.splitlines()
            any_capped = any_capped or len(lines) >= CAP
            for line in lines:
                did, srcs, text = line.split("\t", 2)
                if source and source not in srcs.split(","):
                    continue
                hits += 1
                if hits <= n:
                    m = re.search(pat, text, re.I)
                    lo = max(0, m.start() - 120) if m else 0
                    print(f"{did}\t[{g}:{srcs}]\t...{text[lo:lo + 360]}...")
        capped = " (a group hit the cap; true count is higher)" if any_capped else ""
        print(f"\n-- {hits} match(es){capped}; showed up to {n}", file=sys.stderr)
        return 0
    if cmd == "words":
        n = opt_n(rest)
        groups, source = resolve(rest[0])
        words = [w for w in rest[1:] if w.strip()]
        expr = " OR ".join('"' + w.replace('"', '""') + '"' for w in words)
        pool = sqlite3.connect(f"file:{POOL}?mode=ro", uri=True)
        meta = sqlite3.connect(f"file:{WORK / 'meta.sqlite'}?mode=ro", uri=True)
        out, seen = [], set()
        for did, _ in pool.execute("SELECT rowid, rank FROM search WHERE search MATCH ? ORDER BY rank LIMIT 20000", (expr,)):
            row = meta.execute("SELECT rep, grp FROM m WHERE doc=?", (did,)).fetchone()
            if not row or row[1] not in groups or row[0] in seen:
                continue
            r = idx[row[0]]
            if source and source not in r["sources"]:
                continue
            seen.add(row[0]); out.append((row[0], row[1], r))
            if len(out) >= n:
                break
        texts = text_of([o[0] for o in out])
        for rid, g, r in out:
            t = " ".join((texts[rid] or "").split())
            k = sum(1 for w in words if w.lower() in t.lower())
            print(f"{k}/{len(words)}\t{rid}\t[{g}:{','.join(r['sources'])}]\t{t[:220]}")
        print(f"\n-- {len(out)} shown (BM25 order over the pool's FTS index)", file=sys.stderr)
        return 0
    sys.exit(f"unknown command {cmd!r}; see --help")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
