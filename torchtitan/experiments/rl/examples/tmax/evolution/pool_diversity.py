#!/usr/bin/env python3
"""Whether the pool is collapsing onto one kind of task, measured on the verifiers.

Diversity has no absolute scale: nobody can say whether 0.71 is a diverse
corpus. So nothing here is quoted on its own. Every number is read against the
same number on the seed corpus, and the alarm is a move, not a level. That
turns "is this pool diverse" -- which needs a calibrated metric nobody has --
into "did evolution narrow it", which only needs a monotone one.

What it reads is the verifier, not the instruction and not the operator label.

  - The operator label is what the author said it wrote, and it is the yardstick
    the family-balance term was already optimising, so scoring the pool with it
    would be marking the homework against its own answer key.
  - The instruction is prose, and a rewrite keeps the seed's wording, so two
    tasks that now demand completely different work still read alike.
  - The verifier is the one artifact that says what the policy must actually
    produce, in a form a parser can read.

A task's fingerprint is the set of capability tokens its verifier depends on:
the kinds of check it makes (a file exists, a JSON key equals, a regex matches,
an exit code is zero) and the tools it reaches for (tar, git, sqlite3). Two
tasks with the same fingerprint ask for the same kind of work, whatever their
prose says.

Three numbers per pool, each meaningful only as a delta against the seeds:

  coverage       distinct tokens the pool touches. Falling means whole
                 capabilities have left the corpus.
  concentration  Simpson index over token frequency: the chance two tasks drawn
                 at random share a token drawn at random. Rising means the pool
                 is piling onto a few kinds of check.
  near-duplicate share of tasks whose nearest neighbour is at Jaccard >= 0.8.
                 Rising means the pool is cloning, which is the failure the
                 other two are slowest to show.

Read it beside the difficulty probe, never alone. The two trade off against
each other: pressure for diversity is what moves a rewrite out of its seed's
domain, and pressure for faithfulness is what collapses the pool. A round is
healthy when neither has moved much, and a round that improved one while the
other slid is the one worth stopping for.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from itertools import combinations
from pathlib import Path

# A verifier's check kinds, keyed by the name it calls or imports. The token is
# what the policy has to satisfy, not the library that happens to express it.
CALL_TOKENS = {
    "exists": "path-exists", "isfile": "path-exists", "isdir": "path-exists",
    "is_file": "path-exists", "is_dir": "path-exists", "listdir": "dir-listing",
    "iterdir": "dir-listing", "glob": "dir-listing", "walk": "dir-listing",
    "read_text": "file-content", "read_bytes": "file-content", "open": "file-content",
    "readlines": "file-content", "getsize": "file-size", "stat": "file-mode",
    "chmod": "file-mode", "access": "file-mode", "st_mode": "file-mode",
    "load": "structured-read", "loads": "structured-read",
    "search": "regex", "match": "regex", "fullmatch": "regex",
    "findall": "regex", "finditer": "regex", "sub": "regex",
    "run": "subprocess", "check_output": "subprocess", "Popen": "subprocess",
    "call": "subprocess", "check_call": "subprocess",
    "md5": "checksum", "sha1": "checksum", "sha256": "checksum", "digest": "checksum",
    "hexdigest": "checksum", "connect": "network-or-db", "urlopen": "network-or-db",
}
MODULE_TOKENS = {
    "json": "json", "yaml": "yaml", "csv": "csv", "toml": "toml",
    "tomllib": "toml", "configparser": "ini", "xml": "xml",
    "sqlite3": "sqlite", "tarfile": "archive", "zipfile": "archive",
    "gzip": "archive", "shutil": "archive", "hashlib": "checksum",
    "subprocess": "subprocess", "socket": "network-or-db", "time": "timing",
    "datetime": "timing", "stat": "file-mode", "difflib": "text-diff",
}
# Tools a verifier shells out to. Shell builtins and the interpreter itself say
# nothing about the task, so they are not tokens.
TOOL_RE = re.compile(r"\b(git|tar|zip|unzip|gzip|make|cmake|gcc|g\+\+|cargo|go|npm|pip|"
                     r"uv|pytest|docker|systemctl|journalctl|curl|wget|jq|yq|sqlite3|psql|"
                     r"awk|sed|grep|find|rsync|openssl|ssh|nc|ping|df|du|ps|lsof|ss|"
                     r"chmod|chown|ln|mount|crontab|ffmpeg|convert|pandoc)\b")
SKIP_TOOLS = {"python", "python3", "bash", "sh", "echo", "cat", "ls", "cd"}


def _module_root(name: str) -> str:
    return (name or "").split(".", 1)[0]


def fingerprint(src: str) -> set[str]:
    """The capability tokens one verifier depends on."""
    tokens: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # A shell verifier, or one this parser cannot read. Tool names still
        # carry signal and are all we take.
        return set(TOOL_RE.findall(src)) - SKIP_TOOLS
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                tokens |= {MODULE_TOKENS[m] for m in [_module_root(a.name)] if m in MODULE_TOKENS}
        elif isinstance(node, ast.ImportFrom):
            root = _module_root(node.module or "")
            if root in MODULE_TOKENS:
                tokens.add(MODULE_TOKENS[root])
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in CALL_TOKENS:
                tokens.add(CALL_TOKENS[name])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens |= set(TOOL_RE.findall(node.value)) - SKIP_TOOLS
        elif isinstance(node, ast.Compare):
            for op in node.ops:
                if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    tokens.add("numeric-bound")
                elif isinstance(op, (ast.In, ast.NotIn)):
                    tokens.add("membership")
    return tokens


def verifier_sources(row: dict) -> list[str]:
    """Every Python verifier a mix row ships, from its tmax fixtures."""
    fixtures = ((row.get("metadata") or {}).get("tmax") or {}).get("fixtures") or {}
    return [text for name, text in fixtures.items() if name.endswith(".py")]


def pool_fingerprints(mix: Path, ids: set[str] | None = None,
                      invert: bool = False) -> dict[str, set[str]]:
    """Fingerprints for the rows of `mix`, optionally only those in `ids`.

    The baseline that controls for everything else is inside the same file: the
    rows evolution has rewritten against the rows it has not. A historical mix
    would differ by the corpus rebuild and every earlier round as well.
    """
    out: dict[str, set[str]] = {}
    for ln in mix.open():
        if not ln.strip():
            continue
        row = json.loads(ln)
        if ids is not None and ((row["label"] in ids) == invert):
            continue
        fp: set[str] = set()
        for src in verifier_sources(row):
            fp |= fingerprint(src)
        if fp:
            out[row["label"]] = fp
    return out


def simpson(fps: dict[str, set[str]]) -> float:
    """The chance two tasks drawn at random share a token drawn at random."""
    counts = Counter(t for fp in fps.values() for t in fp)
    total = sum(counts.values())
    if total < 2:
        return 0.0
    return sum(n * (n - 1) for n in counts.values()) / (total * (total - 1))


def near_duplicate_share(fps: dict[str, set[str]], threshold: float = 0.8) -> float:
    """Share of tasks with at least one neighbour at Jaccard >= threshold."""
    items = [(k, v) for k, v in fps.items() if v]
    if len(items) < 2:
        return 0.0
    paired: set[str] = set()
    for (ka, a), (kb, b) in combinations(items, 2):
        if ka in paired and kb in paired:
            continue
        if len(a & b) / len(a | b) >= threshold:
            paired.add(ka)
            paired.add(kb)
    return len(paired) / len(items)


def report(mix: Path, ids: set[str] | None = None, invert: bool = False) -> dict:
    fps = pool_fingerprints(mix, ids, invert)
    counts = Counter(t for fp in fps.values() for t in fp)
    top = counts.most_common(5)
    total = sum(counts.values()) or 1
    return {
        "tasks": len(fps),
        "coverage": len(counts),
        "concentration": round(simpson(fps), 4),
        "near_duplicate": round(near_duplicate_share(fps), 4),
        "top5_share": round(sum(n for _, n in top) / total, 4),
        "top5": top,
    }


def _line(name: str, a, b) -> str:
    if b is None:
        return f"  {name:<15} {a}"
    arrow = "->" if a != b else "=="
    return f"  {name:<15} {a} {arrow} {b}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mix", type=Path, help="the pool to audit")
    ap.add_argument("--against", type=Path, default=None,
                    help="another pool to read it against; without it only levels are printed, "
                         "and a level on its own says nothing")
    ap.add_argument("--ids", type=Path, default=None,
                    help="labels, one per line: audit only these rows, against the rest of the "
                         "same mix. The rewritten rows against the untouched ones is the "
                         "comparison that controls for everything but evolution")
    ap.add_argument("--json", action="store_true", help="machine-readable, for a per-round log")
    args = ap.parse_args()

    ids = None
    if args.ids:
        ids = {x.strip() for x in args.ids.read_text().split() if x.strip()}
    now = report(args.mix, ids)
    base = (report(args.against) if args.against
            else report(args.mix, ids, invert=True) if ids else None)
    if args.json:
        print(json.dumps({"pool": now, "baseline": base}, sort_keys=True))
        return
    against = (str(args.against) if args.against
               else "the rest of the same mix" if ids else "")
    print(f"pool {args.mix}" + (f"  against {against}" if base else ""))
    for key in ("tasks", "coverage", "concentration", "near_duplicate", "top5_share"):
        print(_line(key, base[key] if base else now[key], now[key] if base else None))
    print("  most common:  " + ", ".join(f"{t}={n}" for t, n in now["top5"]))
    if base:
        gone = {t for t, _ in base["top5"]} - {t for t, _ in now["top5"]}
        if gone:
            print("  left the top: " + ", ".join(sorted(gone)))


if __name__ == "__main__":
    main()
