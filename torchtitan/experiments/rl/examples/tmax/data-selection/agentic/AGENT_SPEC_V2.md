# Rerank spec v2 — TB2.1 task → 10 nearest training tasks

> v2 changes: the pool grew from 56,307 to **76,786** across **seven**
> corpora, and every candidate now carries a **verifier grade**. Read the
> verifier section below before scoring anything — it adds a hard rule.

You are reranking retrieval candidates for **training-data selection**. The
question is never "does this text look similar?" It is: *if we trained on this
task, would it help a model solve the TB2.1 task?*

## Your inputs, per TB2.1 task `<TB_ID>`

`work/packets_v2/<TB_ID>.json`

```
{ "tb21_id": ..., "tb21_instruction": "<the full TB2.1 problem statement>",
  "candidates": { "<corpus>": { "n_total": N,
      "shown":    [ {task_id, n_hits, rrf, found_by, inst_chars, snippet,
                     verifier, verifier_strength}, ... ~20 ],
      "rest_ids": [ "<more candidate ids, no text>", ... ] } } }
```

Seven corpora. Candidates are fused across four retrievers (`tfidf_word`,
`tfidf_char`, `bm25`, `dense`); `n_hits` = how many of the four found it, `rrf`
= fused rank score. **Treat this as a reading order, not as an answer.** A
candidate with `n_hits: 1` can easily beat one with `n_hits: 4` — lexical
overlap on boilerplate ("create a file in /app", "write to result.txt") is the
single biggest false-positive source here, and you are the filter for it.

`snippet` is ~520 chars. `inst_chars` tells you how much more there is; when the
snippet is not enough to judge, read the rest with `tools.py show`. `rest_ids`
are real candidates too — expand any that look promising.

## Your tools (run from the `data-selection/` directory)

```bash
PY=/data/yichuan_wang/leann2-venv/bin/python
$PY tools.py show <task_id> [...]         # full instruction
$PY tools.py full <task_id>               # full doc: tests, verifier, env, oracle
$PY tools.py search <corpus|all> <regex>  # regex over all 56,307 instructions
$PY tools.py words <corpus|all> w1 w2 ... # rank docs by how many words hit
$PY tools.py sources                      # corpus names + aliases
```

Corpus aliases: `tmax`, `rts`, `tw`, `smith`, `rebench`, or `all`.

**You are expected to search on your own.** The shortlist is recall-oriented but
not complete. After reading the TB2.1 instruction, pull out the concrete
handles — tool names, protocols, file formats, library names, syscalls, the
failure mode — and run `search` / `words` for them. Anything you find that way
is a legal candidate even if it is not in the shortlist. Do this especially when
the shortlist looks weak for a corpus.

## The verifier grade — read this before scoring

Every candidate carries `verifier`, how hard its tests are to pass *without
actually solving it*:

| grade | meaning |
|---|---|
| `strong` | at least half its tests, and at least three, pin a concrete expected value |
| `ok` | a quarter or more do |
| `weak` | at least one does |
| `behavioral` | nothing pinned, but at least one test runs the artifact and asserts on its exit status — weaker than a pinned value, not vacuous |
| `FREE` | **none of the above** — the whole suite is satisfied by file-exists / non-empty / substring checks |
| `repo-tests` | runs the upstream project's own suite at named pytest node ids (SWE-Smith, SWE-Rebench). Not gameable this way; treat as trustworthy |
| `absent` | no verifier ships with our copy of that corpus (all of Recursive-Task-Synthesis) |

This matters because the output feeds RL. A task graded `FREE` pays full reward
for output that merely looks right — one real example passed its entire suite
with a three-line shell script that did not do the task at all. Training on that
teaches the policy to fake the shape of an answer. **A perfect skill match with
a `FREE` verifier is worse than no match at all.**

**Hard rule: no `FREE` candidate may appear in `final_top10`.** You may still
rank one inside `per_source` — it is honest to show that a pool's best offering
is unverifiable — but say so in `notes`. `absent` candidates may appear in
`final_top10`, since that grade describes our copy of the corpus rather than the
task; prefer a graded equivalent when one exists and note when you could not.

The grade is produced by a regex heuristic, so it is a prior, not a verdict. If
a candidate matters and its grade looks wrong, read the tests with
`tools.py full <task_id>` and say in `reason` what you actually found.

## Scoring: three axes, 1–5 each

Score every candidate you shortlist on all three. Be harsh; 5 should be rare.

**`skill`** — does solving it exercise the same capability?
Not the same words: the same thing the model has to *be able to do*. Parsing a
binary format, reasoning about a race, writing a correct SQL migration, reading
a stack trace back to a commit, driving a CLI with awkward flags, implementing a
published algorithm from its paper description.
5 = the same capability is the crux of both. 3 = adjacent capability, real
transfer. 1 = shares only the shell.

**`domain`** — same technical territory?
Ecosystem, stack, subject matter: kernel/netfilter, Node/npm, PyTorch, Postgres,
Git internals, cryptography, numerical statistics, container runtimes.
5 = same stack and same corner of it. 3 = same broad area. 1 = unrelated.

**`task_form`** — same shape of work?
The genres here are roughly: implement-from-spec, fix-a-failing-test,
find-and-patch-a-regression, recover/repair-corrupted-data, configure-a-service,
reverse-engineer-a-binary-or-protocol, optimise-for-a-budget, build/packaging.
Also weigh whether the difficulty and the number of steps are comparable.
5 = same genre and comparable depth. 1 = different genre.

**`overall`** — one number, 1–5, and it is a judgement, not an average. Weight
`skill` highest, then `domain`, then `task_form`. A candidate that teaches the
exact skill in a different domain usually beats one that shares the domain but
exercises nothing the TB2.1 task needs.

## What to produce

Two things, in one file per TB2.1 task.

1. **`per_source`** — exactly 10 from *each* of the seven corpora, ranked.
   This forces every pool to put forward its best, so a big pool cannot bury a
   small one. If a corpus genuinely has fewer than 10 defensible candidates,
   still return 10 and let the low `overall` scores say so.
2. **`final_top10`** — the answer: the best 10 *overall* for this TB2.1 task,
   drawn from the 70 above. **No per-corpus quota** — if the honest best 10 are
   all from one pool, say so. Rank 1 = closest.

Write to `rerank_v2/<TB_ID>.json`, exactly this schema:

```json
{
  "tb21_id": "<TB_ID>",
  "tb21_profile": {
    "skill": "<the capability the task actually demands, one line>",
    "domain": "<stack / subject area, one line>",
    "task_form": "<genre, one line>"
  },
  "verifier_notes": "<any candidate whose grade you checked and disagreed with>",
  "searches": ["<each tools.py query you ran, verbatim>"],
  "per_source": {
    "TMax-15K": [
      {"rank": 1, "task_id": "...", "skill": 4, "domain": 3, "task_form": 4,
       "overall": 4, "reason": "<=25 words, concrete, says what transfers"}
    ],
    "Recursive-Task-Synthesis": [...],
    "TerminalWorld-Seeds-Clean": [...],
    "SWE-Smith-Seeds-Clean": [...],
    "SWE-Rebench-Tasks-Clean": [...],
    "Terminal-Lego-15k": [...],
    "CalibForge": [...]
  },
  "final_top10": [
    {"rank": 1, "task_id": "...", "source": "...", "skill": 4, "domain": 3,
     "task_form": 4, "overall": 4, "reason": "<=25 words"}
  ],
  "notes": "<optional: weak corpora, judgement calls, anything downstream should know>"
}
```

## Hard rules

- **Never invent a `task_id`.** Every id must come from the candidate file or
  from a `tools.py` result. Ids are validated afterwards and a bad one fails the
  whole task. Copy-paste them; do not retype or abbreviate.
- Exactly 10 per corpus in `per_source` (seven corpora → 70 entries), exactly
  10 in `final_top10`, no duplicate ids inside any single list.
- No `FREE`-graded candidate in `final_top10`.
- `final_top10` ids must all appear in `per_source`.
- `reason` must be specific. "similar task" is useless. "both recover a
  truncated SQLite WAL and must replay it by hand" is useful.
- One JSON file per task, written before you move to the next task. Do not batch
  them up at the end.
- Judge on the instruction. Use `tools.py full` when the instruction alone is
  ambiguous about what the task really requires, but do not let shared Harbor
  scaffolding (Dockerfiles, `test.sh` wrappers, pytest boilerplate) count as
  similarity — it is present in nearly every task and means nothing.

## Corpus-specific notes

- **Terminal-Lego-15k** (15,048, new) is built from StackOverflow questions.
  Real problems, but the weakest verification of the authored corpora: 15.9% is
  `FREE` and only 39% is `strong`/`ok`. It skews easy (9,839 easy / 5,022 medium / 187
  hard) and its categories are its own, not Terminal-Bench's. Where it is
  genuinely strong is Scheme/metacircular evaluation, HTML sanitising and Vim
  macro work — three areas the v1 pool had almost nothing in.
- **CalibForge** (5,431, new) is calibrated by solver disagreement: `multi_solver`
  keeps tasks a heterogeneous solver pool disagrees on, `contrastive_solver`
  targets a strong-pass/weak-fail boundary. It has the best verification of any
  authored corpus here (44% `strong`) and its categories match Terminal-Bench's
  taxonomy. Good coverage of QEMU, Stan/MCMC, curve fitting, Cython and polyglot.
- **TerminalWorld-Seeds-Clean** is the weakest in the pool by rate: 42.3% `FREE`.
  It is small (1,353), so this costs few slots, but do not treat a TerminalWorld
  match as verified unless its grade says so.
- **Recursive-Task-Synthesis** ships no verifier at all in our copy, so all
  37,484 of it is `absent`. That is half the pool; do not read `absent` as a
  quality signal either way.

- **SWE-Smith** and **SWE-Rebench** are GitHub-issue-shaped bug fixes. Their
  `task_form` will rarely match a TB2.1 task, so they will lose on that axis.
  That is correct and expected — do not inflate it. Judge them mainly on
  `skill` and `domain`: same library, same ecosystem, same class of debugging.
- **Recursive-Task-Synthesis** is large (37k) and synthetic; many entries are
  short, formulaic `/app` file-manipulation chores. Discount surface matches on
  that boilerplate hard.
- **TerminalWorld** entries share heavy Harbor scaffolding. Judge the
  instruction only.
