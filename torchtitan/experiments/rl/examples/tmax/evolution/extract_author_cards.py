#!/usr/bin/env python3
"""Turn the authors' README appendix into the card structure the loop reads.

The paper names the 40 operators and describes the card protocol, but prints no
card. Ours were written from that protocol; these are the authors' own, sent by
煜坤 over WeChat on 2026-08-15 after Yichuan asked for them.

One limit to keep on the record: the README says the code's `OPERATOR_CARDS` is
the source of truth, so this appendix describes the cards rather than being
them. It is the authors' account of their own cards — much closer than a
reconstruction, and still not the literal strings the pipeline feeds a model.

Usage: extract_author_cards.py docs/rst-authors/...README.md -o data/...json
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# The appendix writes each card as a details block with eight bulleted fields,
# labelled in Chinese. The English names are the ones the paper's STEP 0 prompt
# refers to, so the loop keeps those.
FIELDS = {
    "目标": "intent",
    "适用种子": "seed_affordances",
    "构造流程": "construction_pattern",
    "证据来源": "evidence_sources",
    "预期产物": "expected_artifacts",
    "验证策略": "verifier_strategy",
    "公开说明": "instruction_strategy",
    "反捷径": "anti_shortcut_strategy",
}
FAMILIES = {
    "A": "environment_and_runtime",
    "B": "build_test_execution",
    "C": "data_artifacts_reporting",
    "D": "configuration_and_state",
    "E": "diagnosis_audit_forensics",
}

DETAILS = re.compile(
    r"<details>\s*\n<summary><code>([a-z_]+)</code>：([^<]*)</summary>\n"
    r"(.*?)\n</details>", re.S)
BULLET = re.compile(r"^- \*\*([^*]+)\*\*：(.+)$", re.M)
SECTION = re.compile(r"^### ([A-E])\. (.+)$", re.M)
SUMMARY_LINE = re.compile(r"^- `([a-z_]+)`：(.+)$", re.M)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("readme")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    text = Path(args.readme).read_text(encoding="utf-8")
    one_liners = dict(SUMMARY_LINE.findall(text))

    # Which family a card belongs to is the appendix section it sits in.
    bounds = [(m.start(), m.group(1)) for m in SECTION.finditer(text)]

    def family_at(pos: int) -> str:
        letter = "A"
        for start, l in bounds:
            if start < pos:
                letter = l
        return FAMILIES[letter]

    cards: dict[str, dict] = {}
    for m in DETAILS.finditer(text):
        name, title, body = m.group(1), m.group(2).strip(), m.group(3)
        card = {"_family": family_at(m.start()), "_title": title,
                "_definition": one_liners.get(name, "").strip()}
        for label, value in BULLET.findall(body):
            key = FIELDS.get(label.strip())
            if key is None:
                raise SystemExit(f"{name}: unknown field {label!r}")
            card[key] = value.strip()
        missing = set(FIELDS.values()) - set(card)
        if missing:
            raise SystemExit(f"{name}: missing {sorted(missing)}")
        cards[name] = card

    if len(cards) != 40:
        raise SystemExit(f"expected 40 cards, parsed {len(cards)}")
    Path(args.out).write_text(
        json.dumps(cards, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    by_family: dict[str, int] = {}
    for c in cards.values():
        by_family[c["_family"]] = by_family.get(c["_family"], 0) + 1
    print(f"wrote {len(cards)} cards to {args.out}")
    for f, n in sorted(by_family.items()):
        print(f"  {f:28s} {n}")


if __name__ == "__main__":
    main()
