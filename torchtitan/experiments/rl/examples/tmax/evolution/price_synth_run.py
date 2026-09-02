#!/usr/bin/env python3
"""Price a synthesis run from its recorded token usage.

The open decision on this pipeline is what scale to run it at, which is a price
question, and the price turns on a choice nobody had written down: the model.
`SYNTH_MODEL` defaults to `gpt-5.6`, which is an alias — the API bills
`gpt-5.6-sol`, the dearest of three tiers spanning 25x. The authors run
`api_deepseek_deepseek-v4-pro` (their README, docs/rst-authors/), and that one
fact accounts for almost all of the gap against the $0.05 a task RST reports.

Rates are USD per million tokens, read from each vendor's pricing page on
2026-08-16. They move — DeepSeek publishes peak/off-peak rates from 16:00 UTC
that same day — so re-check before quoting a total, and treat what follows as
arithmetic on rates you supply rather than as a quote.

Usage: price_synth_run.py results/synth_priced.jsonl [--yield 0.2]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

RATES = {
    # OpenAI, https://platform.openai.com/docs/pricing
    "gpt-5.6-sol": (5.00, 30.00),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-luna": (0.20, 1.20),
    # DeepSeek, https://deepseek.ai/pricing — cache-miss input; a stable prefix
    # bills at roughly a hundredth of it, and this pipeline sends the same seed
    # through five steps, so the real figure should come in under this one.
    "deepseek-v4-pro": (0.435, 0.870),
}
RST_REPORTED = 0.05


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--yield", dest="yield_rate", type=float,
                    help="fraction of attempts that produce a usable task; "
                         "without it, only the per-attempt price is shown")
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.results).read_text().splitlines()
            if l.strip()]
    n = len(rows)
    tin = sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in rows)
    tout = sum((r.get("usage") or {}).get("completion_tokens", 0) for r in rows)
    treason = sum((r.get("usage") or {}).get("reasoning_tokens", 0) for r in rows)
    billed = sorted({m for r in rows
                     for m in ((r.get("usage") or {}).get("models") or [])})

    print(f"{n} attempts, {tin:,} input and {tout:,} output tokens "
          f"({treason:,} of the output is reasoning)")
    print(f"per attempt: {tin // n:,} in, {tout // n:,} out")
    print(f"billed as: {', '.join(billed) or 'not recorded — pre-dates the fix'}\n")

    print(f"{'model':<18} {'per attempt':>12} {'per 1k':>10} {'vs RST':>8}")
    for model, (rin, rout) in RATES.items():
        each = (tin / n * rin + tout / n * rout) / 1e6
        print(f"{model:<18} {'$' + format(each, '.3f'):>12} "
              f"{'$' + format(each * 1000, ',.0f'):>10} "
              f"{format(each / RST_REPORTED, '.1f') + 'x':>8}")

    print(f"\nRST reports ${RST_REPORTED:.2f} an accepted task, on the DeepSeek "
          "row above.")

    if args.yield_rate:
        print(f"\nat a {args.yield_rate:.0%} yield, per usable task:")
        for model, (rin, rout) in RATES.items():
            each = (tin / n * rin + tout / n * rout) / 1e6 / args.yield_rate
            print(f"  {model:<18} ${each:.2f}   1,000 usable: "
                  f"${each * 1000:,.0f}")

    outcomes = collections.Counter(r["status"] for r in rows)
    print(f"\noutcomes: {dict(outcomes)}")


if __name__ == "__main__":
    main()
