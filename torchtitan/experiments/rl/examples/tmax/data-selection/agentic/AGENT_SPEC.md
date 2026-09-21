# Rerank spec — TB2.1 task → 10 nearest training tasks

You are reranking retrieval candidates for **training-data selection**. The
question is never "does this text look similar?" It is: *if we trained on this
task, would it help a model solve the TB2.1 task?*

## Your inputs, per TB2.1 task `<TB_ID>`

`work/packets/<TB_ID>.json`

```
{ "tb21_id": ..., "tb21_instruction": "<the full TB2.1 problem statement>",
  "candidates": { "<corpus>": { "n_total": N,
      "shown":    [ {task_id, n_hits, rrf, found_by, inst_chars, snippet}, ... ~20 ],
      "rest_ids": [ "<more candidate ids, no text>", ... ] } } }
```

Five corpora. Candidates are fused across four retrievers (`tfidf_word`,
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

1. **`per_source`** — exactly 10 from *each* of the five corpora, ranked.
   This forces every pool to put forward its best, so a big pool cannot bury a
   small one. If a corpus genuinely has fewer than 10 defensible candidates,
   still return 10 and let the low `overall` scores say so.
2. **`final_top10`** — the answer: the best 10 *overall* for this TB2.1 task,
   drawn from the 50 above. **No per-corpus quota** — if the honest best 10 are
   all from one pool, say so. Rank 1 = closest.

Write to `rerank/<TB_ID>.json`, exactly this schema:

```json
{
  "tb21_id": "<TB_ID>",
  "tb21_profile": {
    "skill": "<the capability the task actually demands, one line>",
    "domain": "<stack / subject area, one line>",
    "task_form": "<genre, one line>"
  },
  "searches": ["<each tools.py query you ran, verbatim>"],
  "per_source": {
    "TMax-15K": [
      {"rank": 1, "task_id": "...", "skill": 4, "domain": 3, "task_form": 4,
       "overall": 4, "reason": "<=25 words, concrete, says what transfers"}
    ],
    "Recursive-Task-Synthesis": [...],
    "TerminalWorld-Seeds-Clean": [...],
    "SWE-Smith-Seeds-Clean": [...],
    "SWE-Rebench-Tasks-Clean": [...]
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
- Exactly 10 per corpus in `per_source`, exactly 10 in `final_top10`, no
  duplicate ids inside any single list.
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

- **SWE-Smith** and **SWE-Rebench** are GitHub-issue-shaped bug fixes. Their
  `task_form` will rarely match a TB2.1 task, so they will lose on that axis.
  That is correct and expected — do not inflate it. Judge them mainly on
  `skill` and `domain`: same library, same ecosystem, same class of debugging.
- **Recursive-Task-Synthesis** is large (37k) and synthetic; many entries are
  short, formulaic `/app` file-manipulation chores. Discount surface matches on
  that boilerplate hard.
- **TerminalWorld** entries share heavy Harbor scaffolding. Judge the
  instruction only.
