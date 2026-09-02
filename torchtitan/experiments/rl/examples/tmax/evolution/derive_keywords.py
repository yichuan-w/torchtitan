#!/usr/bin/env python3
"""Turn each operator card's `seed_affordances` into tokens you can grep a seed for.

L(o) — the local fit score — needs a keyword list per operator. Ours was
hand-written and one list per *family*, so forty operators shared five lists of
about a dozen words each. The authors' blocked rate is 0.33-0.57% end to end and
ours was ten to twenty times that, and a keyword list that misses is exactly how
an operator ends up scoring low on a seed it actually fits.

The list was never ours to invent. Every card already carries a
`seed_affordances` field saying what a seed needs for that operator —
"包含 requirements、package manifest、运行命令或版本敏感测试" for
dependency_version_alignment. It is prose, and mostly Chinese, while the seeds
are English shell and Dockerfiles, so it cannot be grepped directly: across the
40 cards it yields 1.4 English tokens each and 26 of them fewer than two.

So this translates once, writes the result to disk, and the loop reads the file.
Derived from the authors' description rather than from what any of us guessed a
terminal task looks like — and reviewable, because the output is a plain list per
operator rather than a judgement inside a prompt.

Usage: derive_keywords.py [--cards data/operator_cards_authors.json]
                          [--out data/operator_keywords.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import synth_client as llm  # noqa: E402

PROMPT = """Turn one operator's seed requirements into strings you can grep for.

The operator is `{operator}`. A seed suits it when it has:

    {affordances}

The seeds are real terminal tasks: an `instruction.md` in English, a Dockerfile,
and a `solve.sh`. You are writing the list of literal substrings that, if present
in those three files, indicate the seed has what this operator needs.

Rules that decide whether this list is useful:

- Lowercase literals only, matched as substrings. No regex, no globs.
- Concrete over abstract: `requirements.txt`, `pip install`, `package.json`,
  `pyproject` — not `dependency` or `version management`.
- Include the commands, filenames, extensions and flags that actually appear in
  such a task. A seed about services contains `systemctl`, `daemon`, `:8080`,
  `curl localhost` — the word "service" alone matches too much.
- 8 to 20 entries. A short precise list beats a long vague one, and one entry
  that matches every task is worse than none.
- Nothing so generic it appears in most terminal tasks: skip `run`, `file`,
  `output`, `install` on its own.

Return schema:
{{"keywords":["...","..."]}}"""


def derive(operator: str, card: dict) -> tuple[str, list[str]]:
    try:
        out = llm._parse_json(llm.chat([
            {"role": "system", "content": "Return ONLY valid JSON."},
            {"role": "user", "content": PROMPT.format(
                operator=operator,
                affordances=card["seed_affordances"])}], max_tokens=1200))
        words = [w.strip().lower() for w in (out.get("keywords") or [])
                 if isinstance(w, str) and 2 < len(w.strip()) < 40]
        return operator, sorted(set(words))
    except Exception as e:  # noqa: BLE001
        print(f"  {operator}: failed, {type(e).__name__}", file=sys.stderr)
        return operator, []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default="data/operator_cards_authors.json")
    ap.add_argument("--out", default="data/operator_keywords.json")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    cards = json.loads(Path(args.cards).read_text())
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = dict(ex.map(lambda kv: derive(*kv), cards.items()))

    empty = [k for k, v in results.items() if not v]
    Path(args.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n")
    sizes = [len(v) for v in results.values() if v]
    print(f"wrote {len(results)} operators to {args.out}")
    print(f"  keywords each: min {min(sizes)}, median "
          f"{sorted(sizes)[len(sizes) // 2]}, max {max(sizes)}")
    if empty:
        print(f"  no keywords derived for: {', '.join(empty)}")
    for op in list(results)[:3]:
        print(f"\n  {op}:\n    {results[op]}")


if __name__ == "__main__":
    main()
