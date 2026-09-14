#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.
"""Where a root's Claude spend went, from the per-turn token_count events the
Codex CLI records under every session: by token class, by session kind, by
the fate of the rewrite the session belonged to, and the per-turn shape
(context size, visible output vs thinking).

    offline_cost.py --root <root> [--since <rewrite-dir stamp prefix>] \
        [--price in=5 cw=6.25 cr=0.5 out=25]

Prices are USD per million tokens: uncached input, cache write, cache read,
output (thinking included). The defaults are Claude Opus 5 list prices.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--since", default="")
    ap.add_argument(
        "--price", nargs="*", default=[], help="k=v per-million prices: in cw cr out"
    )
    a = ap.parse_args()
    price = {"in": 5.0, "cw": 6.25, "cr": 0.50, "out": 25.0}
    for kv in a.price:
        k, v = kv.split("=")
        price[k] = float(v)

    def usd(c: dict) -> float:
        return sum(c[k] / 1e6 * price[k] for k in price)

    tot: collections.Counter = collections.Counter()
    by_kind: dict = collections.defaultdict(collections.Counter)
    by_fate: dict = collections.defaultdict(collections.Counter)
    n_kind: collections.Counter = collections.Counter()
    n_fate: collections.Counter = collections.Counter()
    turns_kind: collections.Counter = collections.Counter()
    thinking = out_total = turns_total = 0
    ctx: list[int] = []
    out: list[int] = []
    pattern = os.path.join(
        a.root, "evolution/tasks/*/rewrites/*/sessions/*/codex/sessions/*/*/*/*.jsonl"
    )
    for j in glob.glob(pattern):
        rw_dir = j.split("/sessions/")[0]
        if a.since and os.path.basename(rw_dir) < a.since:
            continue
        kind = j.split("/sessions/")[1].split("/")[0].split("--")[-1]
        try:
            meta = json.load(open(os.path.join(rw_dir, "rewrite.json")))
        except Exception:  # noqa: BLE001
            meta = {}
        status, reason = meta.get("status"), (meta.get("reason") or "")
        if status == "failed":
            if "TimeoutExpired" in reason:
                fate = "failed:session-timeout"
            elif any(
                s in reason
                for s in (
                    "Semantic probe",
                    "blind verifier",
                    "probe author",
                    "Verifier author",
                )
            ):
                fate = "failed:verdict"
            else:
                fate = "failed:session-died"
        elif status == "interrupted":
            fate = "interrupted(loop stopped)"
        elif status == "kept":
            fate = "kept(give-up)"
        else:
            fate = status or "?"
        last = None
        t = 0
        for line in open(j, errors="replace"):
            if '"token_count"' not in line:
                continue
            try:
                info = json.loads(line)["payload"]["info"]
            except Exception:  # noqa: BLE001
                continue
            if not info or not info.get("total_token_usage"):
                continue
            last = info["total_token_usage"]
            lu = info.get("last_token_usage") or {}
            t += 1
            ctx.append(lu.get("input_tokens", 0))
            out.append(lu.get("output_tokens", 0))
        if not last:
            continue
        c = {
            "in": last["input_tokens"]
            - last.get("cached_input_tokens", 0)
            - last.get("cache_write_input_tokens", 0),
            "cw": last.get("cache_write_input_tokens", 0),
            "cr": last.get("cached_input_tokens", 0),
            "out": last["output_tokens"],
        }
        thinking += last.get("reasoning_output_tokens", 0)
        out_total += last["output_tokens"]
        turns_total += t
        for k, v in c.items():
            tot[k] += v
            by_kind[kind][k] += v
            by_fate[fate][k] += v
        n_kind[kind] += 1
        n_fate[fate] += 1
        turns_kind[kind] += t
    print(
        f"TOTAL ${usd(tot):,.0f}  sessions={sum(n_kind.values())} turns={turns_total}"
    )
    print(
        "by token class: "
        + ", ".join(
            f"{k}={tot[k] / 1e6:,.1f}M ${tot[k] / 1e6 * price[k]:,.0f}" for k in price
        )
    )
    print(
        f"output tokens: {out_total / 1e6:.1f}M, thinking {thinking / 1e6:.1f}M ({100 * thinking / max(out_total, 1):.0f}%)"
    )
    if ctx:
        ctx.sort()
        out.sort()
        m = len(ctx)
        print(
            f"per turn: context median {ctx[m // 2]:,} p90 {ctx[int(m * .9)]:,} | output median {out[m // 2]:,} "
            f"p90 {out[int(m * .9)]:,} | ${usd(tot) / max(turns_total, 1):.3f}/turn"
        )
    print("--- by session kind")
    for k, c in sorted(by_kind.items(), key=lambda x: -usd(x[1])):
        print(
            f"  {k:13s} n={n_kind[k]:4d} turns/session={turns_kind[k] / n_kind[k]:5.1f} "
            f"${usd(c) / n_kind[k]:6.2f}/session  ${usd(c):,.0f}"
        )
    print("--- by fate of the rewrite the session belonged to")
    for k, c in sorted(by_fate.items(), key=lambda x: -usd(x[1])):
        print(f"  {k:26s} n={n_fate[k]:4d}  ${usd(c):,.0f}")


if __name__ == "__main__":
    main()
