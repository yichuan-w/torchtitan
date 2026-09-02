#!/usr/bin/env python3
"""A/B the retune arms on real 0/16 signals: chat vs codex, same trace.

For each fresh 0/16 signal with a real transcript, run BOTH the chat retune
(evolve.simplify, trace in the prompt, truncated at REPAIR_CONTEXT) and the
codex retune (evolve_codex, full trace as files + AGENTS.md). Score each on the
same gate the loop uses: the leak/dark audit (no NEW verifier leak or dark path
introduced by the rewrite). Also record whether the chat arm truncated the
trace. Neither arm touches Daytona -- both are pure model calls.

Output: per-signal rows + a summary, written to --out (jsonl) and stdout.

  ab_retune.py --limit 15 --out results/ab_retune.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evolve as ev
import evolve_codex as ec
import feedback_loop as fb
import synth_loop as sl
import synth_client as llm

BASE = Path(os.environ.get("TRL_BASE", "/scratch/gpfs/TRIDAO/al9080/terminal-rl"))
POOL = [BASE / "data/tw-extract/tasks", BASE / "data/swe-extract/tasks"]


def resolve_src(tid: str):
    for r in POOL:
        if (r / tid / "instruction.md").exists():
            return r / tid
    return None


def audit_pass(orig_task: dict, new_instruction: str) -> tuple[bool, str]:
    """The loop's instruction-only gate: no NEW verifier leak or dark path."""
    new_task = {**orig_task, "instruction": new_instruction}
    before, after = sl.audit(orig_task), sl.audit(new_task)
    new_leaks = [x for x in after["leaks"] if x not in before["leaks"]]
    new_dark = [p for p in after["dark_paths"] if p not in before["dark_paths"]]
    ok = not new_leaks and not new_dark
    return ok, f"leaks={new_leaks} dark={new_dark}" if not ok else ""


def fresh_zero_signals(limit: int) -> list[Path]:
    runs = sorted((BASE / "runs").glob("tw-mix-take7-*/launch.info"),
                  key=os.path.getmtime)
    restart = os.path.getmtime(runs[-1]) if runs else 0
    sigs = list((BASE / "evolution/consumed").glob("*.json")) + \
        list((BASE / "evolution/signals").glob("*.json"))
    out = []
    for p in sorted(sigs, key=os.path.getmtime, reverse=True):
        if os.path.getmtime(p) <= restart:
            continue
        try:
            s = json.load(open(p))
        except Exception:
            continue
        if s.get("solved") != 0 or not s.get("attempts"):
            continue
        tb = sum(len(st.get("cmd", "")) + len(str(st.get("out", "")))
                 for a in s["attempts"] for st in (a.get("transcript") or []))
        if tb < 500:  # skip still-empty ones
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--out", default=str(BASE / "results/ab_retune.jsonl"))
    args = ap.parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    sigs = fresh_zero_signals(args.limit)
    print(f"real-trace 0/16 signals: {len(sigs)}")
    rows = []
    with open(args.out, "w") as f:
        for i, sp in enumerate(sigs):
            s = json.load(open(sp))
            tid = s["task_id"]
            src = resolve_src(tid)
            if src is None:
                continue
            task = ev.load(str(src))
            trace = fb.format_trace(s["attempts"])
            truncated = len(trace) > llm.REPAIR_CONTEXT
            rec = {"task_id": tid, "trace_bytes": len(trace),
                   "truncated_for_chat": truncated,
                   "cap": llm.REPAIR_CONTEXT}

            # chat arm
            t0 = time.time()
            try:
                chat_new = ev.simplify(task, solved=0, attempts=s["total"],
                                       trajectory=trace, hint="specific")
                ok, why = audit_pass(task, chat_new["instruction"])
                rec["chat"] = {"ok": ok, "why": why, "hint_level": chat_new.get("_hint"),
                               "instr_delta": len(chat_new["instruction"]) - len(task["instruction"]),
                               "t": round(time.time() - t0, 1)}
            except Exception as e:  # noqa: BLE001
                rec["chat"] = {"ok": None, "why": f"{type(e).__name__}: {e}"[:150]}

            # codex arm (same trace)
            t0 = time.time()
            try:
                cx_new = ec.simplify_codex(task, solved=0, attempts=s["total"],
                                           trajectory=trace, hint="specific")
                ok, why = audit_pass(task, cx_new["instruction"])
                rec["codex"] = {"ok": ok, "why": why,
                                "instr_delta": len(cx_new["instruction"]) - len(task["instruction"]),
                                "t": round(time.time() - t0, 1)}
            except Exception as e:  # noqa: BLE001
                rec["codex"] = {"ok": None, "why": f"{type(e).__name__}: {e}"[:150]}

            rows.append(rec)
            f.write(json.dumps(rec) + "\n")
            f.flush()
            print(f"[{i+1}/{len(sigs)}] {tid} trace={len(trace)}B "
                  f"trunc={truncated} chat_ok={rec['chat'].get('ok')} "
                  f"codex_ok={rec['codex'].get('ok')}", flush=True)

    n = len(rows)
    def rate(arm): return sum(1 for r in rows if r[arm].get("ok")) / n if n else 0
    trunc = sum(1 for r in rows if r["truncated_for_chat"])
    print("\n=== summary ===")
    print(f"signals: {n}")
    print(f"chat truncated (trace > {llm.REPAIR_CONTEXT}): {trunc}/{n}")
    print(f"audit pass — chat: {rate('chat'):.0%}  codex: {rate('codex'):.0%}")


if __name__ == "__main__":
    main()
