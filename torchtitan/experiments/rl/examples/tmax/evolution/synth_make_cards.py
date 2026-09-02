#!/usr/bin/env python3
"""Draft the 40 operator cards the paper describes but does not publish.

RST states that "each operator is therefore accompanied by an executable card
specifying compatible seed affordances, construction patterns, evidence sources,
expected artifacts, verifier and instruction strategies, and shortcut-rejection
criteria", and its STEP 0 prompt consumes those fields directly. The cards
themselves are not in the paper — the field names appear only inside the
protocol text, never as card content — so anyone reproducing the pipeline has to
write them.

The failure mode to design against is collapse: ask for forty cards in the same
breath and you get one card with forty names on it, which turns the operator
back into a label and undoes the diversity machinery it exists to serve. Two
guards here. Each card is generated with the already-written cards of its family
in context and an explicit instruction to differ from them, and afterwards the
set is checked for construction patterns that are too close to each other.

Usage:
  python3 synth_make_cards.py --out data/operator_cards.json [--only family_id]
"""
from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import synth_client as llm
import synth_operators as ops

CARD_SYSTEM = """You write executable operator cards for a terminal-task \
synthesis system. Return ONLY valid JSON, no markdown.

A card is a construction recipe, not a description. Someone reading only the \
card and a seed task must be able to build a concrete, harder task from it \
without further guidance. Write in the imperative and be specific about \
mechanism; avoid restating the operator's one-line definition in longer words."""

CARD_USER = """Write the operator card for this rewrite operator.

Family: {family}
Operator ID: {operator}
Definition (from the taxonomy): {definition}

The card is consumed by a task-transformation step that will:
- judge whether the operator fits a seed, using seed_affordances
- build a task_chain of inspect / derive / execute / validate / finalize stages,
  using construction_pattern
- decide where hidden details stay discoverable, using evidence_sources
- name the evidence, intermediate and final artifacts, using expected_artifacts
- define at least four reward checks, using verifier_strategy and
  anti_shortcut_strategy
- set the instruction's tone and compactness, using instruction_strategy

Cards already written for this family — yours must be clearly distinguishable \
from them in mechanism, not just in wording:
{siblings}

Return schema, all fields required:
{{
  "seed_affordances": ["observable signals in a seed that make this operator a
                       natural fit; be concrete, e.g. 'a Makefile with more than
                       one target', not 'a build system'"],
  "construction_pattern": "how to turn such a seed into a harder task with this
                           operator, as a sequence of concrete moves",
  "evidence_sources": ["where the agent can legitimately discover what it needs:
                        specific file kinds, command output, logs, --help"],
  "expected_artifacts": ["what must exist afterwards, distinguishing evidence,
                          intermediate and final artifacts"],
  "verifier_strategy": "what the verifier should assert semantically, beyond
                        file existence",
  "anti_shortcut_strategy": "the specific cheat this operator invites, and the
                             check that rejects it",
  "instruction_strategy": "what the instruction states versus what it points at"
}}"""

REQUIRED = ("seed_affordances", "construction_pattern", "evidence_sources",
            "expected_artifacts", "verifier_strategy", "anti_shortcut_strategy",
            "instruction_strategy")


def _text(card: dict) -> str:
    parts = []
    for k in REQUIRED:
        v = card.get(k, "")
        parts.append(" ".join(v) if isinstance(v, list) else str(v))
    return " ".join(parts).lower()


def generate(family: str, operator: str, definition: str,
             siblings: dict[str, dict]) -> dict:
    sib = "\n".join(
        f"- {oid}: {c.get('construction_pattern', '')[:200]}"
        for oid, c in siblings.items()) or "(none yet — this is the first)"
    out = llm.chat([
        {"role": "system", "content": CARD_SYSTEM},
        {"role": "user", "content": CARD_USER.format(
            family=family, operator=operator, definition=definition,
            siblings=sib)}], max_tokens=3000)
    card = llm._parse_json(out)
    missing = [k for k in REQUIRED if not card.get(k)]
    if missing:
        raise ValueError(f"card for {operator} missing {missing}")
    return card


def check_distinctness(cards: dict[str, dict], threshold: float = 0.72) -> list:
    """Report card pairs whose construction patterns read almost the same.

    Near-duplicates mean the operator stopped being a mechanism and became a
    name, which is exactly what the family-balance and inverse-frequency
    machinery is there to prevent from happening at selection time — no point
    protecting diversity in the selector if the cards themselves collapsed.
    """
    ids = list(cards)
    close = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            r = SequenceMatcher(
                None,
                str(cards[a].get("construction_pattern", ""))[:600].lower(),
                str(cards[b].get("construction_pattern", ""))[:600].lower()
            ).ratio()
            if r >= threshold:
                close.append((round(r, 3), a, b))
    return sorted(close, reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/operator_cards.json")
    ap.add_argument("--only", help="restrict to one family id")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cards: dict[str, dict] = json.loads(out.read_text()) if out.exists() else {}

    for family, operators in ops.OPERATORS.items():
        if args.only and family != args.only:
            continue
        siblings = {o: c for o, c in cards.items() if c.get("_family") == family}
        for operator, definition in operators.items():
            if operator in cards:
                continue
            try:
                card = generate(family, operator, definition, siblings)
            except Exception as e:  # noqa: BLE001
                print(f"  {operator}: FAILED {str(e)[:120]}", flush=True)
                continue
            card["_family"] = family
            card["_definition"] = definition
            cards[operator] = card
            siblings[operator] = card
            out.write_text(json.dumps(cards, indent=1, ensure_ascii=False))
            print(f"  {operator}: ok ({len(_text(card))} chars)", flush=True)

    print(f"\n{len(cards)}/40 cards written to {out}")
    close = check_distinctness(cards)
    if close:
        print(f"{len(close)} pair(s) with near-identical construction patterns:")
        for r, a, b in close[:10]:
            print(f"  {r}  {a} ~ {b}")
        print("regenerate those with --only <family> after deleting them")
    else:
        print("no near-duplicate construction patterns")


if __name__ == "__main__":
    main()
