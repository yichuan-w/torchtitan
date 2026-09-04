#!/usr/bin/env python3
"""Staged task synthesis, following RST's five-step rewrite protocol.

The paper does not ask a model for a whole task in one shot. It fixes a
transformation contract first and then rewrites one artifact per step, each
against that contract:

    STEP 0  contract only, no files written
    STEP 1  solution/solve.sh
    STEP 2  tests/test_state.py and tests/test.sh
    STEP 3  instruction.md, filtered for discoverability
    STEP 4  environment/Dockerfile and task.toml, usually unchanged

That ordering is what keeps the four artifacts agreeing with each other. Asking
for all of them at once makes the model reconcile four mutually-constraining
files in a single pass, and the failure shows up later as a verifier checking
something the instruction never mentions.

Prompts here follow the paper's Appendix (see docs/rst-prompts-verbatim.txt);
wording is condensed but the requirements, forbidden shortcuts and return
schemas are kept.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request

import synth_operators as ops

# The paper's STEP 0 consumes an operator card per operator, and prints none of
# them. Ours were written from the protocol it does print (synth_make_cards.py);
# the authors' own account of theirs arrived on 2026-08-15, so that is what this
# defaults to — reproducing RST with cards we invented reproduces something
# else. Absent a card the step still runs, but the operator degrades into a
# label, which is what the whole taxonomy exists to avoid.
#
# Ours stay in the tree and stay selectable, because which set produces better
# tasks is a measurement nobody has taken: they are fourteen times longer, and
# longer is not the same as better. SYNTH_CARDS=data/operator_cards.json runs
# the comparison.
CARD_FILES = ("data/operator_cards_authors.json", "data/operator_cards.json")


def _find_cards() -> pathlib.Path:
    """Locate the cards without assuming a directory layout.

    The scripts live under scripts/ in the repo and beside data/ on the run
    host, so a fixed parent.parent works in one place and silently misses in
    the other — and a missing card file degrades quietly into unguided
    synthesis, which is worse than an error.
    """
    if os.environ.get("SYNTH_CARDS"):
        return pathlib.Path(os.environ["SYNTH_CARDS"])
    here = pathlib.Path(__file__).resolve().parent
    roots = (here, here.parent, pathlib.Path.cwd())
    for name in CARD_FILES:
        for root in roots:
            if (root / name).exists():
                return root / name
    return here.parent / CARD_FILES[0]


CARDS_PATH = _find_cards()
_CARDS: dict | None = None


def operator_card(operator: str) -> str:
    global _CARDS
    if _CARDS is None:
        try:
            _CARDS = json.loads(CARDS_PATH.read_text())
        except Exception:  # noqa: BLE001
            _CARDS = {}
    card = _CARDS.get(operator)
    if not card:
        return ("(no card available for this operator — infer a construction "
                "recipe from the definition, and say so in why_fit)")
    # ensure_ascii=False because the authors' cards are in Chinese, and escaping
    # turns every character of them into six the model has to pay for and read
    # through.
    return json.dumps({k: v for k, v in card.items()
                       if not k.startswith("_")},
                      ensure_ascii=False, indent=1)[:3500]

# The key is regional: api.openai.com answers 401 with "incorrect regional
# hostname" and names this one.
API_BASE = os.environ.get("SYNTH_API_BASE", "https://us.api.openai.com/v1")
MODEL = os.environ.get("SYNTH_MODEL", "gpt-5.6")
# Reasoning effort for every call. Default high: retune/audit quality is worth
# more than latency here. Override per-run with SYNTH_EFFORT=medium|low.
EFFORT = os.environ.get("SYNTH_EFFORT", "high")
ENV_FILE = pathlib.Path(os.environ.get(
    "SYNTH_ENV_FILE", str(pathlib.Path.home() / "Projects/MyClaw/.env")))


def _api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key.strip()
    if ENV_FILE.exists():
        m = re.search(r"^OPENAI_API_KEY=(.+)$", ENV_FILE.read_text(), re.M)
        if m:
            return m.group(1).strip()
    raise SystemExit("no OPENAI_API_KEY in environment or env file")


# What every call so far has cost, in tokens. The question nobody can answer
# about this pipeline is what scale to run it at, and that is a price question:
# a task costs however many tokens its five steps and its retries took, and
# until they are counted the answer can only be a guess. RST reports $0.05 a
# task, which is the number this has to be compared against.
USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "reasoning_tokens": 0, "cached_tokens": 0}
# What actually billed, which is not what was asked for: `gpt-5.6` is an alias
# and resolves to `gpt-5.6-sol`, the most expensive tier of three that span 25x.
# A run priced against the name in the config would be pricing the wrong model,
# and an alias can be repointed without anything here changing.
BILLED_MODELS: set[str] = set()


def usage_since(mark: dict | None = None) -> dict:
    """Tokens spent since a mark, so a caller can price one task."""
    mark = mark or {}
    out = {k: USAGE[k] - mark.get(k, 0) for k in USAGE}
    out["models"] = sorted(BILLED_MODELS)
    return out


class _EmptyContent(Exception):
    pass


_CLIENT = None


def _client():
    # Use the OpenAI SDK rather than raw urllib: some OpenAI-compatible endpoints
    # answer a 307 that raw urllib will not re-POST, and the SDK follows it. Lazy
    # so importing this module stays cheap.
    global _CLIENT
    if _CLIENT is None:
        from openai import OpenAI

        # Bound the wait. The SDK defaults to read=600s with max_retries=2,
        # and chat() wraps that in its own retries=4, so one unlucky call can
        # block for 600 x 3 x 4 = 2 hours. run_round waits on as_completed over
        # every future, so a handful of those stall the whole round: measured
        # on della 2026-09-01, the loop sat 4.5h at rounds=0 with 11 sockets
        # ESTAB and idle. A healthy effort=high call answers in ~7s, so 180s is
        # already 25x headroom; one SDK retry keeps a genuine blip recoverable
        # while capping the worst case at 180 x 2 x 4 = 24 min.
        _CLIENT = OpenAI(
            api_key=_api_key(),
            base_url=API_BASE,
            timeout=float(os.environ.get("SYNTH_TIMEOUT_SEC", "180")),
            max_retries=int(os.environ.get("SYNTH_MAX_RETRIES", "1")),
        )
    return _CLIENT


def chat(messages: list[dict], max_tokens: int = 12000,
         retries: int = 4) -> str:
    # max_completion_tokens is shared between reasoning and content; at high
    # effort the reasoning share can consume the whole budget and hand back an
    # EMPTY content string (seen live as "JSONDecodeError ... char 0" in every
    # retune the hour effort=high shipped). Give high effort enough headroom.
    if EFFORT == "high":
        max_tokens = max(max_tokens, 24000)
    payload = {"model": MODEL, "messages": messages,
               "max_completion_tokens": max_tokens,
               "reasoning_effort": EFFORT}
    last = ""
    for attempt in range(retries):
        try:
            body = _client().chat.completions.create(**payload).model_dump()
            if body.get("model"):
                BILLED_MODELS.add(body["model"])
            u = body.get("usage") or {}
            USAGE["calls"] += 1
            USAGE["prompt_tokens"] += u.get("prompt_tokens", 0)
            USAGE["completion_tokens"] += u.get("completion_tokens", 0)
            # Reasoning tokens bill as completion tokens and are not listed
            # separately in the total, so they are tracked to show how much
            # of the bill is thinking rather than task text.
            USAGE["reasoning_tokens"] += (
                u.get("completion_tokens_details") or {}
            ).get("reasoning_tokens", 0)
            USAGE["cached_tokens"] += (
                u.get("prompt_tokens_details") or {}
            ).get("cached_tokens", 0)
            content = body["choices"][0]["message"]["content"] or ""
            if not content.strip():
                # Budget consumed by reasoning, no content produced -- retryable
                # (the retry rides the enlarged budget), not a valid empty answer.
                last = ("empty content (finish_reason="
                        f"{body['choices'][0].get('finish_reason')})")
                raise _EmptyContent(last)
            return content
        except _EmptyContent as e:
            last = str(e)
        except Exception as e:  # noqa: BLE001
            # The SDK raises APIStatusError with .status_code for HTTP errors.
            code = getattr(e, "status_code", None)
            if code is not None:
                last = f"HTTP {code}: {str(e)[:300]}"
                if code not in (408, 409, 429) and code < 500:
                    raise RuntimeError(last)
            else:
                last = f"{type(e).__name__}: {e}"
        time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError(f"chat failed after {retries} attempts: {last}")


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\n", "", t)
        t = re.sub(r"\n```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


SYSTEM = """You generate high-quality TerminalWorld task transformations.

Return ONLY valid JSON. Do not use markdown.

Rules:
- Keep the original task recognizable.
- Rewrite only files explicitly requested in this step.
- Return full file contents, not patches.
- Do not add benchmark canary text.
- Do not add network-only requirements or heavyweight new dependencies unless \
the seed already naturally uses them.
- The final task must be solvable by the rewritten solution and verifiable by \
tests.
- The user instruction must not leak the full solution or private test harness \
paths.
- The rewritten task should preserve the seed's main workflow and add one \
realistic terminal-native subgoal.
- Prefer tasks that require filesystem inspection, CLI composition, config/data \
validation, log/error interpretation, and observable artifact checks.
- Do not turn the task into a narrow "fix this bug" prompt; any \
repair/debugging should be one part of a larger goal-oriented workflow.
- Keep instruction.md a compact public goal, not an acceptance rubric. Only \
publish undiscoverable hard requirements; put discoverable details in workspace \
docs and point to them. Never leave acceptance criteria only inside tests/, and \
never dump path inventories, field schemas, or step checklists that invite \
reward hacking."""

STEP0 = """STEP 0: task transformation contract. Do not rewrite files in this step.

Role: design one controlled, verifiable rewrite contract. The task must preserve \
the seed's core workflow and add one terminal-native requirement that increases \
workflow depth.

Rewrite target protocol:
- Use the preferred operator if it is natural for this seed.
- If it is artificial or unsafe, choose one fallback operator and explain why.
- Do not collapse to config/data consistency unless the seed genuinely exposes \
config/data relationships.
- The operator determines the main transformation mechanism, but the task must \
still feel like a normal terminal workflow.

Quality criteria:
- Start from the seed's original initial state, not a halfway failure state.
- The new requirement is user-visible through artifacts, command output, state, \
or reports.
- Details may be hidden from the instruction only when they stay discoverable \
through local files, scripts, configs, fixtures, logs, or normal command output \
that the instruction fairly points to.
- The work should involve inspection, derivation, execution, validation and \
finalization, not a single deterministic file write.
- Define a short task_chain and at least four observable reward_checks.
- The four are roles, not a count. Every contract carries one of each, tagged \
with `role`: required_evidence (the agent had to find something), \
intermediate_artifact (it had to produce the middle of the workflow, not just \
the end), final_semantics (the end state means what it should, checked by \
content and not by existence), and no_shortcut.
- no_shortcut is the one that decides whether this task can be gamed, so say \
how it is caught, in behaviour rather than in words. Strongest first, and use \
the first that this seed can actually support:
  (a) change an input the answer depends on, run the workflow again, require the \
output to follow, restore. Only choose this when the workflow is re-runnable \
from a clean state — say so in preserved_workflow, because both the solution and \
the instruction have to carry it: the agent is told to build something \
repeatable, or it hands in a correct one-shot answer and fails every attempt;
  (b) derive the expected answer inside the verifier from the current inputs and \
compare, so a copied or stale answer disagrees;
  (c) require an intermediate artifact whose content must be consistent with the \
final one, so producing only the final answer fails.
- Never make no_shortcut a check the reference solution cannot pass. It is the \
first thing the oracle gate rejects.
- Never hide a semantic check that exists only in the verifier with no \
discoverable evidence.

Hard constraints — do not produce any of these:
- superficial refactors of solution style;
- merely strengthening tests without changing the user-visible task;
- hidden-only requirements the agent cannot discover from workspace evidence;
- large dependencies, internet access, services, or long-running workloads;
- a task that is just "fix a bug";
- telling the agent to inspect or rerun /tests, tests/test.sh, or pytest as the \
acceptance loop.

Return schema:
{{"status":"ok|blocked","rewrite_family":"...","rewrite_operator":"...",
 "operator_fit":"preferred|fallback","why_fit":"...","goal":"...",
 "preserved_workflow":"...","new_requirement":"...",
 "task_chain":[{{"stage":"inspect|derive|execute|validate|finalize",
                "artifact_or_state":"..."}}],
 "reward_checks":[{{"name":"...","role":"required_evidence|intermediate_artifact|final_semantics|no_shortcut","what":"...","rejects_shortcut":"..."}}]}}

Preferred operator ({family}): {operator} — {definition}

Operator card protocol:
- Treat the card below as a construction recipe, not a label.
- Use its construction_pattern to build a concrete task_chain.
- Use its evidence_sources to decide where hidden details stay discoverable.
- Use its expected_artifacts to define evidence, intermediate and final artifacts.
- Use its verifier_strategy and anti_shortcut_strategy to define reward_checks.
- Use its instruction_strategy as a style hint for tone and compactness, not as
  a licence to hide acceptance criteria.

Operator card:
{card}

Seed task:
--- instruction.md ---
{instruction}
--- environment/Dockerfile ---
{dockerfile}
--- solution/solve.sh ---
{solution}"""

STEP1 = """STEP 1: oracle solution protocol. Rewrite solution/solve.sh only.

Role: write the oracle shell implementation for the contract. It must complete \
the full workflow from the original initial state and behave like a strong \
terminal agent's successful trajectory.

Implementation requirements:
- Preserve the seed's main workflow and add the operator's requirement.
- Implement every stage in contract.task_chain in order.
- Inspect inputs before transforming them; do not blindly overwrite final \
artifacts.
- Create or update every expected artifact with semantically meaningful content.
- Create the observable artifacts contract.reward_checks needs.
- Include bounded validation of intermediate artifacts before writing final \
outputs.
- Keep the script deterministic, safe under repeated execution, and runnable \
non-interactively from any working directory.
- Derive every output from the inputs as they are at run time. This is the \
requirement that decides whether the oracle passes its own verifier: when the \
contract's no_shortcut check changes an input and runs the workflow again, a \
script that recomputes will follow and a script that writes a fixed answer will \
not. Being idempotent is not enough — writing the same constant twice is \
idempotent and still wrong. Read the source, compute, then write.

Forbidden shortcuts:
- Hardcoding verifier-only constants without deriving them from discoverable \
evidence.
- Replacing the task with a no-op or single trivial write.
- Removing seed behavior to make the new verifier easier.
- Depending on internet access, external services, or unbounded background work.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{"solution/solve.sh":"FULL FILE CONTENT"}}}}

Task contract:
{contract}

Current task:
{task_context}"""

STEP2 = """STEP 2: verifier protocol. Allowed outputs are tests/test_state.py only.

Role: write the oracle verifier for the contract, not for incidental \
implementation details in solution/solve.sh.

Verification requirements:
- Check the user-visible goal, preserved seed behavior, and all expected \
artifacts.
- Turn contract.reward_checks into distinct verifier subchecks with names that \
match the reward check names, one test function each. Four roles means four test \
functions at minimum; merging two roles into one test loses the dense signal the \
whole contract is for.
- All four roles must be present, and the no_shortcut one has to earn its name \
using whichever form the contract chose: re-run after perturbing an input, \
recompute the expected answer from current inputs and compare, or require an \
intermediate artifact consistent with the final one. Asserting that a file is \
non-empty, or that it does not contain the word "placeholder", is not this check.
- If the contract chose the re-run form, invoke the workflow the same way the \
instruction asks a user to, and restore whatever you perturbed in a finally \
block. Do not invoke solution/solve.sh: it is not present when the agent runs.
- Verify semantic content/state, not only file existence.
- Prefer robust predicates over brittle command-order or exact implementation \
checks.
- Keep tests deterministic and fast.

Failure quality:
- Error messages should help diagnose missing artifacts, wrong formats, or \
inconsistent state.
- Do not leak a full solution recipe through assertion messages.

The file is plain pytest, run as /tests/test_state.py inside the container after \
the agent finishes. It must pass after the oracle solution runs and fail on an \
untouched container.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{"tests/test_state.py":"FULL FILE CONTENT"}}}}

Task contract:
{contract}

Current task:
{task_context}"""

STEP3 = """STEP 3: public instruction rewrite (discoverability-filtered). \
Rewrite instruction.md only.

Role: write instruction.md as a fair public user request for a terminal agent. \
The solution already exists. Do NOT treat the verifier as a rubric to copy.

Style to match for brevity (do not copy content):
"Set up a Python project using Poetry in /app. Create a `.gitignore` containing \
`test/inner_project/inner_project`, run `poetry install`, verify `inner_project` \
imports in that environment, then use the project's console script to write a \
valid JSON report at `/app/report.json` (see `--help` for arguments)."

Critical anti-hacking rule:
- Dumping many absolute paths, schema fields, exact formats, or numbered \
operational steps makes agents shortcut and reward-hack.
- Prefer a compact goal plus pointers to local docs. Put discoverable detail in \
workspace files, not in the instruction.

Path budget:
- At most about 3 absolute paths total, usually the workspace entry plus one \
main deliverable. Never list inventories of intermediate or meta artifacts.

Scope, which is not the same as recipe:
- Withholding paths and formats is right. Withholding the *shape* of the work is \
not, and it is the more common failure here: agents stop after three turns on a \
task whose verifier expects a multi-stage workflow, hand in a first-step answer, \
and fail every check. They were not told there was more.
- So say how much work this is without saying how to do it. That the run has \
several stages; that intermediate results are expected to survive, not just the \
final one; that the workflow must be repeatable if a check will re-run it. None \
of those is a step an agent can follow blindly, and all of them tell it when it \
is not finished.
- If more locations matter, point to a local README/spec/config instead.

Fairness:
- Anything the verifier requires that is NOT discoverable from the workspace \
MUST appear in the instruction.
- That covers properties of the deliverable, not only paths and formats. The one \
that gets missed: if the contract's no_shortcut check re-runs the workflow after \
changing an input, then being re-runnable is a requirement, and an agent who is \
not told will produce a correct one-shot answer and fail every attempt. Say it \
plainly — the workflow has to be repeatable from the current inputs, not a \
transcript of one run.
- Anything discoverable from local files, configs, fixtures or `--help` should \
be pointed at rather than restated.

You are given the acceptance checklist below in filtered form — check names and \
one-line intents only, never the test source. Do not restate it.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{"instruction.md":"FULL FILE CONTENT"}}}}

Task contract:
{contract}

Filtered acceptance checklist:
{checklist}

Current task:
{task_context}"""

STEP4 = """STEP 4: environment alignment protocol.

Use the task contract as the source of truth. Rewrite environment/Dockerfile \
and/or task.toml only if the contract requires a small environment metadata or \
dependency alignment.

**Default preference: return an empty files object.**

If you change environment files:
- preserve the seed's base image and installation style;
- keep the build self-contained: no COPY from a build context, create fixtures \
with RUN and heredocs;
- do not add internet-only runtime behavior, proxies, secrets or external \
services;
- make only the minimum change the rewritten task needs;
- if the instruction intentionally hides constants, formats or paths, ensure \
they are discoverable through local files or Dockerfile-created fixtures;
- do not rely on the verifier as the only place hidden details exist.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{}}}}
(or with "environment/Dockerfile" and/or "task.toml" keys holding full contents)

Task contract:
{contract}

Current task:
{task_context}"""


def _task_context(files: dict[str, str], limit: int = 3500) -> str:
    """The task's files, each truncated to `limit`.

    The default is for the generation steps, which are writing a file rather
    than reasoning across all of them. The repair passes need the opposite and
    pass a much larger one: their job is to hold the verifier and the solution
    side by side, and a generated verifier runs to a median of 9,800 characters
    with every single one over 4,500. At the old limit every repair was reading
    the first half of the checks against the first two thirds of the solution and
    reporting that they agreed.
    """
    return "\n".join(f"--- {name} ---\n{body[:limit]}"
                     for name, body in files.items())


# Above the largest generated verifier seen (16,724 characters), so the repair
# passes see whole files rather than prefixes.
REPAIR_CONTEXT = 20000


def _checklist(test_src: str) -> str:
    """Check names and intents only — never the test source.

    STEP 3 is given a filtered acceptance checklist rather than the verifier
    because handing over the tests is precisely how an instruction turns into a
    rubric the agent can game.
    """
    items = []
    for m in re.finditer(r"def (test_\w+)\s*\([^)]*\):\s*(?:\"\"\"(.*?)\"\"\")?",
                         test_src, re.S):
        name, doc = m.group(1), (m.group(2) or "").strip().splitlines()
        items.append(f"- {name}: {doc[0].strip() if doc else '(no description)'}")
    return "\n".join(items) or "- (no named checks found)"


RANK_PROMPT = """Judge whether the preferred rewrite operator is natural, safe \
and sufficiently supported by this seed task.

You may keep the preferred operator, or replace it with ONE of the listed \
alternatives. You may not name any operator outside those lists.

Reject instead — "fit":"blocked" — when no operator on the lists would give a \
task that is natural for this seed, supported by what the seed actually \
contains, safe, and affordable to run. Rejecting is the right answer for a seed \
with nothing to build on; a forced rewrite costs a sandbox and fails a gate.

Preferred: {preferred_id} ({preferred_family}) — {preferred_def}
Alternatives:
{alternatives}

Seed task:
--- instruction.md ---
{instruction}
--- environment/Dockerfile ---
{dockerfile}
--- solution/solve.sh ---
{solution}

Return schema:
{{"chosen_operator":"operator id or blocked","fit":"preferred|fallback|blocked",\
"why":"one sentence"}}"""


# The selection the authors run, which the paper describes without publishing:
# rank every operator by how well it fits this seed, keep the best few, and only
# then apply the two terms that spread the batch out. Sent by 煜坤 on
# 2026-08-15 (docs/rst-authors/).
LOCAL_POOL = 12          # operators that reach the diversity stage
FALLBACK_COUNT = 5       # alternatives offered alongside the preferred one
FAMILY_TARGET_SHARE = 0.2   # five families, each aimed at a fifth of the batch
FAMILY_FLOOR = 0.25         # an over-used family is damped, never excluded
# One operator fits almost any seed, so on local score alone it would take the
# pool. The authors damp it by a constant rather than reweighting the scan.
BROAD_OPERATOR = {"config_data_consistency": 0.35}

# How much of that spreading pressure applies. Synthesis builds the pool from
# nothing and wants all of it. Evolution starts from a task that already exists
# and only has to make it harder, so the family term -- which boosts whichever
# family is behind, across the whole pool -- moves a task out of the kind of
# work it was about. Measured over 463 rewrites on wd-20260904a it held the
# five families at 96/96/93/91/87, and that near-uniformity is the drift
# Yichuan reported. The operator term damps a repeat within the operators this
# seed has a foothold for, which is a narrower push, so it stays on.
DIVERSITY_MODES = ("family+freq", "freq", "off")
EVOLUTION_DIVERSITY_DEFAULT = "freq"


def _diversity_mode(mode: str | None) -> str:
    """The spreading terms to apply, from the argument or SWE_OPERATOR_DIVERSITY."""
    if mode is None:
        mode = os.environ.get("SWE_OPERATOR_DIVERSITY", "").strip() or EVOLUTION_DIVERSITY_DEFAULT
    if mode not in DIVERSITY_MODES:
        raise ValueError(f"diversity mode {mode!r} is not one of {DIVERSITY_MODES}")
    return mode


_KEYWORDS: dict[str, list[str]] | None = None


def _operator_keywords() -> dict[str, list[str]]:
    """Per-operator keywords, derived once from the cards by derive_keywords.py."""
    global _KEYWORDS
    if _KEYWORDS is None:
        path = CARDS_PATH.parent / "operator_keywords.json"
        try:
            _KEYWORDS = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            _KEYWORDS = {}
    return _KEYWORDS


def local_fit(seed: dict) -> dict[str, float]:
    """L(o): additive, not a rate — confirmed by the authors on 2026-08-16.

    "L(o) 不是命中率，而是加分制：基础分为 1，每命中一个预设关键词加 1，再根据
    文件扩展名、构建文件、日志和环境标记增加结构分。"

    The distinction decides how often a seed gets declined. Ours divided hits by
    the number of markers, so an operator with no keyword in the seed scored
    zero — and zero is the first of the four ways to be blocked, which is why we
    blocked 3-14% of seeds against their measured 0.33-0.57%. With a base of 1
    nothing scores zero, that path stops firing on absent signal, and what it
    still catches is the model declining a seed it has actually read.
    """
    blob = (seed["instruction"] + seed["dockerfile"] + seed["solution"]).lower()
    names = " ".join((seed.get("env_files") or {}).keys()).lower()
    # File structure as the authors describe it: extensions, build files, logs,
    # environment markers. Read off the seed's own files, not its prose.
    structure = 0
    for ext in (".py", ".c", ".cpp", ".go", ".rs", ".js", ".ts", ".java", ".rb",
                ".sh", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv", ".xml"):
        if ext in names:
            structure += 1
    for build in ("makefile", "dockerfile", "cmake", "package.json", "pom.xml",
                  "cargo.toml", "requirements", "pipfile", "go.mod", "gemfile"):
        if build in names or build in blob:
            structure += 1
    for logmark in (".log", "logs/", "stderr", "traceback", "journalctl"):
        if logmark in names or logmark in blob:
            structure += 1
    for envmark in ("env ", "export ", "apt-get", "apk add", "pip install",
                    "systemctl", "service ", "entrypoint"):
        if envmark in blob:
            structure += 1

    per_op = _operator_keywords()
    fit: dict[str, float] = {}
    for fam, operators in ops.OPERATORS.items():
        fam_hits = sum(1 for m in ops.AFFORDANCE[fam] if m in blob)
        for op, definition in operators.items():
            # Per-operator keywords derived from that operator's own card, which
            # is where the authors put them: `seed_affordances` says what a seed
            # needs for it. Ours were one hand-written list per family, so forty
            # operators shared five lists — a granularity that misses, and a
            # keyword that misses is how an operator scores low on a seed it fits.
            kw = per_op.get(op)
            if kw:
                op_hits = sum(1 for w in kw if w in blob)
            else:
                words = [w for w in re.findall(r"[a-z]{4,}", definition.lower())
                         if w not in ("verify", "ensure", "align", "check",
                                      "with", "that", "from", "into", "using")]
                op_hits = sum(1 for w in words if w in blob)
            fit[op] = (1 + fam_hits + op_hits + structure) \
                * BROAD_OPERATOR.get(op, 1.0)
    return fit


def score_operators(seed: dict, used_ops: dict[str, int],
                    used_fams: dict[str, int],
                    mode: str = "family+freq") -> list[tuple[float, str, str]]:
    """S(o) = L(o) x D(f(o)) x P(o), over the operators the scan surfaced.

        D(f) = max(0.25, 1 + 0.2N - n_f)     family balance   ("family+freq")
        P(o) = 1 / (1 + n_o)                 operator inverse frequency ("freq")

    `mode` drops the terms it does not name: "freq" leaves D at 1, "off"
    leaves both at 1 and ranks on local fit alone. See DIVERSITY_MODES.

    Multiplied rather than added, which is the part that matters: under a sum,
    an operator with no foothold in the seed still wins on being under-used, and
    the batch fills with transformations the seeds cannot support. Under a
    product, no local signal means no score — the same rule the blocked test
    reads.

    D compares a family's count against the share of the batch it should have
    by now, so a family that is behind is boosted by however far behind it is,
    and one that is ahead is damped to a floor rather than shut out.
    """
    fit = local_fit(seed)
    fam_of = {op: fam for fam, operators in ops.OPERATORS.items()
              for op in operators}
    pool = sorted(fit.items(), key=lambda kv: -kv[1])[:LOCAL_POOL]

    assigned = sum(used_fams.values())
    scored = []
    for op, local in pool:
        if local <= 0:
            continue
        fam = fam_of[op]
        balance = max(FAMILY_FLOOR,
                      1 + FAMILY_TARGET_SHARE * assigned - used_fams.get(fam, 0)
                      ) if mode == "family+freq" else 1.0
        inv_freq = 1.0 / (1 + used_ops.get(op, 0)) if mode != "off" else 1.0
        scored.append((local * balance * inv_freq, fam, op))
    scored.sort(reverse=True)
    return scored


class Blocked(Exception):
    """No operator this seed can support, so nothing downstream should run.

    The authors block in four places: an operator with no local signal, a model
    that rejects one as unnatural, unsupported, unsafe or too expensive, a step 0
    where neither the preferred operator nor any fallback yields a natural,
    publicly solvable and verifiable task, and any later stage returning blocked.
    A blocked seed never reaches the sandbox.

    Ours had no such path: every seed was forced onto whichever operator scored
    highest, however little the seed had to offer it, and the cost of that lands
    in the gates — a build spent on a task that could not have worked.
    """


def operator_shortlist(seed: dict, used_ops: dict[str, int],
                       used_fams: dict[str, int],
                       k: int = 1 + FALLBACK_COUNT,
                       mode: str | None = None
                       ) -> list[tuple[str, str, str]]:
    """The scored candidates, in score order, without collapsing them to one.

    `pick_operator` picks the axis before anything has opened the package: the
    scan reads the seed, then a chat call ranks six candidates from truncated
    instruction, Dockerfile and solution text. Whoever runs next lays the whole
    package out and reads it properly, and disagrees with that choice most of
    the time -- measured over the agentic evolve path, 69% of sessions ended in
    operator-misfit and another 27% never started because the ranker rejected
    all six, leaving 1.6% of all-solved tasks actually evolved.

    So hand over the shortlist and let the side that reads the package choose
    inside it. The anti-collapse constraint the ranker existed to enforce is not
    in the ranking, it is in the candidate set: `score_operators` multiplies
    local fit by family balance and inverse frequency, so an operator the seed
    has no foothold for scores zero and never appears here at all. Choosing
    within this list cannot collapse the pool; substituting an operator outside
    it still can, and that remains forbidden.

    Blocked is raised only when the local scan surfaced nothing -- a real miss,
    not a judgement made from truncated text.

    This is evolution's entry, so `mode` defaults to EVOLUTION_DIVERSITY_DEFAULT
    and SWE_OPERATOR_DIVERSITY overrides it. `pick_operator`, which synthesis
    calls, keeps the full pressure.
    """
    scored = score_operators(seed, used_ops, used_fams, _diversity_mode(mode))
    if not scored:
        raise Blocked("local scan found no operator with any signal in the seed")
    return [(fam, op, ops.OPERATORS[fam][op]) for _, fam, op in scored[:k]]


def pick_operator(seed: dict, used_ops: dict[str, int],
                  used_fams: dict[str, int], rng) -> tuple[str, str, str]:
    """Preferred operator plus up to five alternatives, then a model ranking.

    The ranking step may substitute one of the alternatives or reject them all,
    but cannot introduce an operator the local scan did not surface — that
    constraint is what keeps the model from steering every seed toward whichever
    operator it finds easiest to write.
    """
    scored = score_operators(seed, used_ops, used_fams)
    if not scored:
        raise Blocked("local scan found no operator with any signal in the seed")
    preferred = scored[0]
    alternatives = scored[1:1 + FALLBACK_COUNT]
    allowed = {op: fam for _, fam, op in [preferred, *alternatives]}

    alt_text = "\n".join(f"- {op} ({fam}) — {ops.OPERATORS[fam][op]}"
                         for _, fam, op in alternatives) or "(none)"
    try:
        out = _parse_json(chat([
            {"role": "system", "content": "Return ONLY valid JSON."},
            {"role": "user", "content": RANK_PROMPT.format(
                preferred_id=preferred[2], preferred_family=preferred[1],
                preferred_def=ops.OPERATORS[preferred[1]][preferred[2]],
                alternatives=alt_text,
                instruction=seed["instruction"][:2500],
                dockerfile=seed["dockerfile"][:1500],
                solution=seed["solution"][:1500])}], max_tokens=600))
        chosen = str(out.get("chosen_operator", "")).strip()
        if str(out.get("fit", "")).strip() == "blocked" or chosen == "blocked":
            raise Blocked(str(out.get("why", ""))[:200] or "model rejected all")
        if chosen in allowed:
            fam = allowed[chosen]
            return fam, chosen, ops.OPERATORS[fam][chosen]
    except Blocked:
        raise
    except Exception:  # noqa: BLE001
        pass  # ranking is an improvement, not a dependency
    return preferred[1], preferred[2], ops.OPERATORS[preferred[1]][preferred[2]]


def synthesize(seed: dict, family: str, operator: str,
               definition: str) -> tuple[dict, dict]:
    """Run the five steps; return (contract, files) for the derived task."""
    files = {"instruction.md": seed["instruction"],
             "environment/Dockerfile": seed["dockerfile"],
             "solution/solve.sh": seed["solution"]}

    contract = _parse_json(chat([
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": STEP0.format(
            family=family, operator=operator, definition=definition,
            card=operator_card(operator),
            instruction=seed["instruction"][:5000],
            dockerfile=seed["dockerfile"][:3500],
            solution=seed["solution"][:3500])}]))
    if contract.get("status") == "blocked":
        raise RuntimeError(f"step0 blocked: {contract.get('why_fit', '')[:200]}")
    cj = json.dumps(contract, indent=1)[:6000]

    for step, tmpl, extra in (
            ("step1", STEP1, {}),
            ("step2", STEP2, {}),
            ("step3", STEP3, {"checklist": ""}),
            ("step4", STEP4, {})):
        if step == "step3":
            extra["checklist"] = _checklist(files.get("tests/test_state.py", ""))
        out = _parse_json(chat([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": tmpl.format(
                contract=cj, task_context=_task_context(files), **extra)}]))
        if out.get("status") == "blocked":
            raise RuntimeError(f"{step} blocked: {str(out.get('rationale'))[:200]}")
        for name, body in (out.get("files") or {}).items():
            if body:
                files[name] = body

    # Off by SYNTH_CONSISTENCY=0. The pass rewrites whole files, so it can
    # introduce a disagreement as easily as it removes one, and whether it helps
    # is a measurement rather than an argument.
    if os.environ.get("SYNTH_CONSISTENCY", "1") != "0":
        files = cross_file_consistency(contract, files, cj)
    # Then one narrow pass with a single job. The consistency step repairs four
    # kinds of disagreement at once and the oracle gate keeps taking 26-42% of
    # the run, which is where every added requirement lands: the solution is
    # written before the verifier exists, so it never sees what will grade it.
    # Coverage first, then the oracle. Running them the other way put oracle
    # failures back from 11% to 26% and pushed too_hard up with them: the
    # coverage pass adds checks to the verifier, and anything it added after the
    # oracle repair had never been reconciled with the solution — so the
    # reference failed it, and so did every agent.
    if not _has_four_roles(files.get("tests/test_state.py", "")):
        files = _repair(COVERAGE_REPAIR, contract, files, cj)
    if os.environ.get("SYNTH_ORACLE_REPAIR", "1") != "0":
        files = _repair(ORACLE_REPAIR, contract, files, cj)
    return contract, files


CONSISTENCY = """Cross-file consistency pass. Compare the instruction, \
solution, verifier, environment and transformation contract, and repair \
discrepancies.

Repair these specifically:
- artifacts the solution produces but the contract omits;
- **verifier requirements unsupported by public evidence** — anything the \
verifier checks that the instruction never states and the workspace never \
reveals. Either make it discoverable (a fixture, config, README the instruction \
points at) or state it in the instruction. Do not silently drop the check.
- instruction requirements the solution does not satisfy or the verifier does \
not check;
- **verifier checks the solution does not satisfy** — go through the verifier one \
check at a time and name the lines of solve.sh that make it pass. A check with \
nothing behind it is the single largest way these tasks fail: the oracle runs \
cleanly, the verifier scores it zero, and the task is thrown away. Fix the \
solution where the check is the fair one, and fix the check where it asks for \
something the task never promised.
- **re-run agreement**, when any check runs the workflow again after changing an \
input: solve.sh has to recompute its outputs from the inputs as they are at that \
moment rather than write a fixed answer, and the instruction has to tell the \
agent the workflow must be repeatable. All three or none — a check like this \
with a one-shot solution fails the oracle, and with a silent instruction it makes \
the task unsolvable for the agent.
- paths, filenames, formats or constants that disagree between any two files.

Return only the files you actually change, with full contents. Return an empty \
files object if everything already agrees.

Return schema:
{{"status":"ok","rationale":"...","files":{{}}}}

Task contract:
{contract}

Current task:
{task_context}"""


ORACLE_REPAIR = """Make the reference solution pass the verifier, or say it cannot.

The solution was written before the verifier existed, so it was written blind to \
the checks that grade it. This is its one chance to see them. It is also the \
largest single loss in this pipeline: a solution that runs cleanly and scores \
zero costs a full image build and the task is discarded.

Method, and do it literally rather than by impression:
- take each test function in tests/test_state.py one at a time;
- name the lines of solution/solve.sh that make it pass;
- where nothing does, add what is missing to solve.sh.

Which side to change:
- Prefer changing solve.sh. The verifier encodes what the task asks for, and \
weakening it to fit an incomplete solution is how a task becomes worthless.
- Change the check only when it asks for something the instruction never \
promised and the workspace never reveals — that check is unfair and would fail \
for an agent too.
- A check that re-runs the workflow after changing an input needs solve.sh to \
recompute its outputs from the inputs as they are at that moment. Writing a \
fixed answer passes the first run and fails the re-run.

Return only the files you change, with full contents, and an empty files object \
if the solution already satisfies every check.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{}}}}

Task contract:
{contract}

Current task:
{task_context}"""


ORACLE_REPAIR_OBSERVED = """The reference solution was run against the verifier \
and failed. Make it pass, or say it cannot.

This is the same job as the blind repair pass, with one difference that matters: \
you are no longer guessing how the solution behaves. Below is what actually \
happened when it ran. Read it first and let it decide where you look — an \
impression of what the code should do is what produced this failure.

Method:
- start from the observed output: which check failed, and what did the run print;
- find the lines of solution/solve.sh responsible for that specific check;
- fix those. Do not rewrite what the run shows is already working.

Which side to change:
- Prefer changing solve.sh. The verifier encodes what the task asks for, and \
weakening it to fit an incomplete solution is how a task becomes worthless.
- Change a check only when it asks for something the instruction never promised \
and the workspace never reveals — that check is unfair and would fail an agent too.
- If the failure is environmental rather than logical (a missing tool, no network) \
say so with status "blocked" instead of coding around it.

Return only the files you change, with full contents.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{}}}}

Observed run (exit {exit_code}):
{observed}

Task contract:
{contract}

Current task:
{task_context}"""


COVERAGE_REPAIR = """Add the contract checks the verifier is missing. \
Rewrite tests/test_state.py only.

The verifier must carry all four roles as separate test functions:
- required_evidence — the agent had to find something, not guess it;
- intermediate_artifact — it produced the middle of the workflow, not only the end;
- final_semantics — the end state means what it should, checked by content;
- no_shortcut — an answer that was copied, hardcoded, or written for the verifier \
is caught.

no_shortcut is the one that is usually missing, and it has to be earned in \
behaviour. Use whichever the task supports: change an input the answer depends on \
and re-run the workflow, asserting the output followed and restoring what you \
changed; or recompute the expected answer inside the verifier from the current \
inputs and compare; or require an intermediate artifact whose content must agree \
with the final one. Asserting that a file is non-empty, or lacks the word \
"placeholder", is not this check.

Two constraints on whatever you add:
- the reference solution in solution/solve.sh must pass it — check that before \
returning, because a check it fails costs the task everything;
- do not invoke solution/solve.sh from the verifier; it is not present when the \
agent runs. Invoke the workflow the way the instruction asks a user to.

Keep the checks that are already there. Return the full file.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{"tests/test_state.py":"FULL FILE CONTENT"}}}}

Task contract:
{contract}

Current task:
{task_context}"""


_TEST_BODY = re.compile(
    r"^\s*def\s+(test_\w+)\s*\([^)]*\)\s*(?:->[^:\n]+)?:((?:\n(?:[ \t].*)?)*)", re.M)
_NOSC_WORDS = re.compile(
    r"no_?shortcut|placeholder|hard[_-]?cod|stale|dummy|verifier[_-]?only"
    r"|fabricat|forged|precomputed", re.I)
_MUTATES = re.compile(r"write_bytes|write_text|\.write\(|shutil\.copy"
                      r"|os\.remove|unlink\(|truncate|touch\(")
_RERUNS = re.compile(r"run_workflow|subprocess\.(run|check_|Popen)|os\.system"
                     r"|run_cmd|sh\(")


def _has_four_roles(src: str) -> bool:
    """Four checks, one of which would catch an unproduced answer.

    The same test preflight applies, run here first so a shortfall costs one
    call instead of the whole task.
    """
    fns = _TEST_BODY.findall(src or "")
    if len(fns) < 4:
        return False
    # The re-run is often in a module-level helper the check calls, so that half
    # is searched across the file while the mutation stays local to the check.
    return any(_NOSC_WORDS.search(b)
               or (_MUTATES.search(b) and _RERUNS.search(src)) for _, b in fns)


SPEC_REPAIR = """Fill in what the instruction leaves out. \
Rewrite instruction.md only.

This task is underspecified: the verifier requires something the instruction \
never states, so an agent could do the job correctly and still fail. The gap:

{finding}

The verifier is right and the reference solution is right — the instruction is \
the file that is wrong, because it omits a requirement they both assume. Add that \
requirement to instruction.md so an agent reading only the instruction knows it: \
the exact name, path, format, count, or value the verifier checks for.

Two lines you must not cross:
- state the requirement, do not hand over the method. "The output file must be \
named report.json and contain a `status` field" is a requirement; "run `foo | \
jq .status > report.json`" is the solution. Add the first, never the second.
- do not touch tests/test_state.py or solution/solve.sh. If the only way to make \
the task fair is to weaken the verifier, this is not underspecification — return \
blocked and say so.

The reference solution must still pass unchanged; you are not changing what the \
task does, only telling the agent what was already required of it.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{"instruction.md":"FULL FILE CONTENT"}}}}

Task contract:
{contract}

Current task:
{task_context}"""


RETUNE = """Retune this task's difficulty. It was built, verified, and then \
attempted by an agent, and the attempts landed in the wrong place.

{finding}

Rewrite the files that need to change. Keep the operator, keep the contract's \
four checks and their roles, and keep the workflow the task is about — this is a \
calibration, not a new task.

How far to move, which matters more than which direction. The target is a task a \
capable agent solves roughly half the time: not every attempt, not none. Of the \
last batch retuned from one extreme, a third landed on the other one — the \
adjustment was the right sign and several times too large. So make one change and make it \
count: a stage added or removed, a check tightened or explained, a sentence added \
to the instruction. One, not three, because the result is measured after this and \
three changes at once make that reading useless. Not a rebuild around a harder \
idea either.

You may be asked again. If this is not the first pass, the history below says \
what the last change did — a change that moved nothing means going further in \
the same direction, and one that overshot means going back part of the way.

The transcript below is what the agent actually ran. Read it before deciding, \
because it says where the attempt went wrong and the repairs are opposite: an \
agent that ran three commands and stopped was not told how much work this is, \
one that looped on the same failing command could not find something the task \
assumes it would, and one that produced the right artifact and still failed is \
being judged on something the instruction never asked for.

If it was solved every time, the work is too shallow. Deepen it where the \
operator points: another stage whose output the next stage consumes, evidence \
that has to be found rather than assumed, a semantic check on the end state \
instead of its shape. Do not make it harder by hiding things — an instruction \
that withholds what the verifier needs produces a task nobody can solve, which \
is the other failure.

If nothing solved it, the task asks for more than it says or more than fits. \
Cut one stage, or state in the instruction what the agent was expected to know: \
which artifacts must survive, that the workflow will be re-run, where the \
evidence lives. Prefer telling the agent over removing the check — the check is \
what makes the task worth training on.

Whatever you change, the reference solution must still pass the verifier. Return \
every file you touch, in full.

Return schema:
{{"status":"ok|blocked","rationale":"...","files":{{}}}}

Task contract:
{contract}

Current task:
{task_context}"""


DIAGNOSE = """在改这道题之前，先判断它本身有没有毛病。

可以依赖的事实（跑出来的，不是猜的）：镜像建得起来，容器里网络是通的——
apt-get、pip、curl 都能用，做题时装工具装得上。**要联网不是毛病**，不要因为
题目要装东西、要下载什么就否掉它。

一道题可以在两个相反的方向上坏掉，两个方向都要查：

太严——agent 赢不了：
  unsolvable     参考解里有一个 agent 无从得知的值（密码、常量、只有作者知道的
                 路径），在指令、环境里都不出现，也没法在容器里跑出来。
                 这道题对 agent 不成立，加提示没有意义。
  underspecified 验证器要求的东西，指令没说、工作区也发现不了。agent 做对了
                 也会挂——要改的是指令，不是难度。

太松——agent 不干活也能赢：
  hackable       存在一小串捷径命令，不做题面要求的事也能让验证器全绿——
                 touch 一个空文件、echo 一行期望的输出。验证器只查"文件存在
                 且非空"而题面要求真干活，就是典型。

  solvable       都不是：该有的都有，过验证器必须真做事。做不出来只是难，
                 这种才值得给提示。

依据：逐条对照 solution/solve.sh 用到的每个值，问它在 instruction.md、
Dockerfile、环境文件里出不出现，或者能不能在容器里跑出来。再逐条看验证器的
断言，问一串最短的捷径命令能不能全部满足。

引用具体的行或值，不要概括。两个方向都坏时，报更严的那个（unsolvable /
underspecified），shortcut 照样填。

返回：
{{"verdict":"solvable|underspecified|unsolvable|hackable",
  "evidence":"引用的那一行或那个值",
  "shortcut":"能骗过验证器的最短命令串，没有则留空",
  "why":"两句话，具体"}}

--- instruction.md ---
{instruction}

--- solution/solve.sh ---
{solution}

--- tests/test_state.py ---
{tests}

--- environment/Dockerfile ---
{dockerfile}"""


def cross_file_consistency(contract: dict, files: dict[str, str],
                           contract_json: str) -> dict[str, str]:
    """Reconcile the four artifacts against the contract after staged rewriting.

    Staged generation keeps each file aligned to the contract, but not
    necessarily to each other: the verifier is written before the instruction
    exists in its final form, so a check can end up resting on evidence the
    instruction never points at. This is the pass that catches it, and it is
    where dark checks are supposed to die — before validation, not after.
    """
    return _repair(CONSISTENCY, contract, files, contract_json)


WRITABLE = ("instruction.md", "solution/solve.sh", "tests/test_state.py",
            "environment/Dockerfile", "task.toml")


def _repair(template: str, contract: dict, files: dict[str, str],
            contract_json: str, **extra) -> dict[str, str]:
    """One repair pass. A failure here leaves the files as they were."""
    try:
        out = _parse_json(chat([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": template.format(
                contract=contract_json,
                task_context=_task_context(files, limit=REPAIR_CONTEXT),
                **extra)}]))
    except Exception:  # noqa: BLE001
        return files
    for name, body in (out.get("files") or {}).items():
        if body and name in WRITABLE:
            files[name] = body
    return files


# --------------------------------------------------------------------------
# Solver agent, used to measure whether a synthesized task is reachable
# --------------------------------------------------------------------------

AGENT_SYSTEM = """You are working inside a Linux container over a terminal. \
Solve the task described by the user.

Reply with ONE shell command per turn and nothing else — no prose, no code \
fences, no explanation. You will receive its stdout, stderr and exit status, \
then you send the next command. Chain with && or ; when steps belong together.

You cannot see any test files and must not look for them; they do not exist \
yet. Work only from the task description and what you find in the container.

Before you finish, check your own work: list the artifacts you were asked to \
produce and read them back, confirm they contain what the task said they should, \
and confirm each stage you were asked to run actually ran. Tasks here usually \
have more than one step, and stopping after the first is the most common way to \
fail one — if the task mentions intermediate results, a report, or repeating the \
workflow, none of that is optional.

When the task is complete and you have checked it, reply with exactly: DONE"""


def agent_step(instruction: str, history: list[tuple[str, str]]) -> str:
    msgs = [{"role": "system", "content": AGENT_SYSTEM},
            {"role": "user", "content": f"Task:\n\n{instruction}"}]
    for cmd, out in history:
        msgs.append({"role": "assistant", "content": cmd})
        msgs.append({"role": "user", "content": out[:4000]})
    cmd = chat(msgs, max_tokens=2000).strip()
    if cmd.startswith("```"):
        cmd = re.sub(r"^```[a-z]*\n?", "", cmd)
        cmd = re.sub(r"\n?```$", "", cmd).strip()
    return cmd


def diagnose_unsolved(task: dict) -> dict:
    """Is this task broken, and in which of the two directions?

    Retuning assumes an unsolved task is merely hard and adds explanation. The
    verdicts here say when that assumption is wrong — and how much each verdict
    is worth is measured, not assumed: crossed against pass@5 over the whole seed
    corpus, `unsolvable` tasks were solved at 0.18 (8.7x over-represented among
    the never-solved), `underspecified` at 0.76 (1.9x), and a since-removed
    `environment` verdict at 0.85 against 0.88 for clean tasks — pure noise,
    because the container has network access and the reader was guessing it did
    not. `hackable` names a claimed shortcut; the loop proves or refutes it by
    running the commands, so a wrong claim here costs one container run, not a
    task.
    """
    try:
        return _parse_json(chat([
            {"role": "system", "content": "Return ONLY valid JSON."},
            {"role": "user", "content": DIAGNOSE.format(
                instruction=task["instruction"][:6000],
                solution=task["solve_sh"][:8000],
                tests=task["test_state_py"][:12000],
                dockerfile=task["dockerfile"][:4000])}], max_tokens=2000))
    except Exception as e:  # noqa: BLE001
        return {"verdict": "unknown", "why": f"{type(e).__name__}: {e}"[:150]}
