#!/usr/bin/env python3
"""Find graders that can be satisfied without doing the task.

The null-action sweep asked what each task pays for `:` and found nothing: no
grader in 1,063 hands out marks for literally nothing. That probe is too weak.
tw_158378 pays full marks for `prm list` and tw_582696 for `date`, because their
graders are satisfied by a trivial command rather than by an empty one, and no
single command can stand in for "trivial" across a corpus.

What can be looked for is the shape. Three patterns, all read statically:

  fallback   an except branch that supplies the expected value it was meant to
             fetch. tw_582696 does this: it asks an NTP server for the time,
             cannot reach one because the sandbox blocks outbound UDP, and falls
             back to datetime.now() with a 300-second tolerance -- so `date`
             passes and the task teaches nothing.
  negative   every assertion is "this must not exist". The initial image already
             satisfies those, so the grader starts green. tw_158378 is this.
  network    the verifier's verdict depends on a host it does not control.

A hit is not a verdict; it is a task worth booting. The point is to get from
1,063 down to a list a person can read.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

ROOT = Path("/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/tw-extract/tasks")
NET = re.compile(r"requests\.(get|post|put)|urlopen|socket\.socket|httpx\.|urllib3")
# A namespace URI or a string being compared is not a fetch; only a call is.
NEGATIVE = re.compile(r"assert\s+(not\s|.*\bnot in\b|.*\bis None\b)")


def fallback_hits(tree: ast.AST) -> list[str]:
    """except branches that assign the value the try block was fetching."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        assigned_in_try = {
            t.id
            for n in ast.walk(node)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name)
        }
        for handler in node.handlers:
            for n in ast.walk(handler):
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name) and t.id in assigned_in_try:
                            out.append(t.id)
    return sorted(set(out))


def scan(task_dir: Path) -> dict | None:
    tests = sorted((task_dir / "tests").glob("*.py"))
    if not tests:
        return None
    src = "\n".join(p.read_text(errors="replace") for p in tests)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    neg = sum(1 for line in src.splitlines() if NEGATIVE.search(line))
    hit = {}
    fb = fallback_hits(tree)
    if fb:
        hit["fallback"] = fb
    if asserts and neg == len(asserts):
        hit["all_negative"] = len(asserts)
    if NET.search(src):
        hit["verifier_network"] = NET.search(src).group(0)
    return hit or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix",
                    default="/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/mix/mix_live.jsonl")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    live = set()
    for line in open(a.mix):
        if line.strip():
            live.add(json.loads(line)["metadata"].get("instance_id"))
    rows = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir():
            continue
        h = scan(d)
        if h:
            rows.append({"task_id": d.name, "in_live_mix": d.name in live, **h})
    for kind in ("fallback", "all_negative", "verifier_network"):
        sel = [r for r in rows if kind in r]
        inmix = [r for r in sel if r["in_live_mix"]]
        print(f"{kind}: {len(sel)} 道, 其中在 live mix 里 {len(inmix)} 道")
        for r in inmix[:14]:
            print(f"   {r['task_id']}  {r[kind]}")
    print(f"\n扫了 {sum(1 for d in ROOT.iterdir() if d.is_dir())} 个包")
    if a.out:
        Path(a.out).write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"写入 {a.out}")


if __name__ == "__main__":
    main()
