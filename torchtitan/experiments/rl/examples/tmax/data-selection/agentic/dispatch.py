#!/usr/bin/env python
"""Emit the per-team prompts for the rerank stage, and report its progress.

This is the one stage a script cannot run end to end: it needs an agent
runtime. What it *can* do is make the stage exactly repeatable — the same team
split, the same prompt text, every time — so the only thing that varies between
runs is the model's judgement.

  dispatch.py plan               write teams.json (the task -> team split)
  dispatch.py prompts            print every team prompt
  dispatch.py prompts --team 03  print one
  dispatch.py status             which tasks are done, which team owes what

Hand each prompt to one agent, all teams in parallel. Each writes
rerank/<tb_id>.json. Then run merge_agentic.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "work" / "cache"
PACKETS = HERE / "work" / "packets"
RERANK = HERE / "rerank"
TEAMS = HERE / "teams.json"

# 5 and not 10: a packet is ~20k tokens, so ten of them plus the agent's own
# tool output overruns a subagent context and it silently truncates late tasks.
TASKS_PER_TEAM = 5

PROMPT = """You are rerank team{team}. Work in this directory:
{root}

FIRST, read `AGENT_SPEC.md` there in full. It is the complete task definition: \
the scoring rubric, the tools, the output schema, and the hard rules. Follow it exactly.

Your {n} TB2.1 tasks, in this order:
{tasks}

For each task: read `work/packets/<id>.json`, profile what the TB2.1 task actually \
demands, rerank the candidates on the three axes (skill / domain / task_form), run \
your own `tools.py search` and `tools.py words` queries to find what the retrievers \
missed, then write `rerank/<id>.json` before starting the next task.

Two things the spec cannot stress enough:
- The retrieval order is lexical and it is wrong often. Boilerplate overlap ("write \
to /app/result.txt", "run the tests", "create a file") is the dominant false \
positive. You are the filter for it.
- Judge transfer, not resemblance: would training on this task help a model solve \
the TB2.1 task?

Spend roughly 10-15 minutes of real work per task. Do not skip the self-directed \
searching — the shortlist is recall-oriented, not complete.

When all {n} files are written, reply with: the task ids you completed, any corpus \
where you could not find 10 defensible candidates, and anything the merge step \
should know."""


def tb_ids() -> list[str]:
    src = CACHE / "tb21_docs.jsonl"
    if not src.exists():
        raise SystemExit(f"missing {src} — run bootstrap_cache.py first")
    return sorted(json.loads(line)["task_id"] for line in src.open() if line.strip())


def load_teams() -> dict[str, list[str]]:
    if not TEAMS.exists():
        raise SystemExit(f"missing {TEAMS} — run `dispatch.py plan` first")
    return json.loads(TEAMS.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "prompts", "status"])
    ap.add_argument("--team", help="only this team, e.g. 03")
    ap.add_argument("--per-team", type=int, default=TASKS_PER_TEAM)
    args = ap.parse_args()

    if args.cmd == "plan":
        ids = tb_ids()
        groups = [ids[i : i + args.per_team] for i in range(0, len(ids), args.per_team)]
        teams = {f"team{i + 1:02d}": g for i, g in enumerate(groups)}
        TEAMS.write_text(json.dumps(teams, indent=1))
        print(f"[dispatch] {len(ids)} tasks -> {len(teams)} teams of <={args.per_team}")
        print(f"[dispatch] wrote {TEAMS}")
        return 0

    teams = load_teams()

    if args.cmd == "prompts":
        missing = [t for t in sum(teams.values(), []) if not (PACKETS / f"{t}.json").exists()]
        if missing:
            print(f"[dispatch] WARNING {len(missing)} packets missing, e.g. {missing[:3]}")
        for name, ids in teams.items():
            if args.team and not name.endswith(args.team):
                continue
            print("=" * 78)
            print(f"### {name}  ({len(ids)} tasks)")
            print("=" * 78)
            print(
                PROMPT.format(
                    team=name.removeprefix("team"),
                    root=HERE,
                    n=len(ids),
                    tasks="\n".join(f"  {i}" for i in ids),
                )
            )
            print()
        return 0

    done = {p.stem for p in RERANK.glob("*.json") if not p.name.startswith("_")}
    total = sum(len(v) for v in teams.values())
    print(f"[dispatch] {len(done)}/{total} rerank files written")
    for name, ids in sorted(teams.items()):
        pending = [i for i in ids if i not in done]
        if pending:
            print(f"  {name} pending: {' '.join(pending)}")
    stray = done - set(sum(teams.values(), []))
    if stray:
        print(f"[dispatch] WARNING {len(stray)} files not in any team: {sorted(stray)[:5]}")
    return 0 if len(done) == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
