# TB 2.1 → 10 nearest training tasks, agent-reranked

> **v2 is the current answer** — `results_v2/`, seven corpora, 76,786 tasks.
> `results/` is v1 (five corpora, 56,307) and is kept for comparison. The two
> share only 14.8% of their picks. Everything below describes the method; the
> v2 section near the end covers what changed.

For each of the 89 Terminal-Bench 2.1 tasks, the 10 training tasks that would
actually help a model solve it — drawn from all five HF pools mixed, ranked by
required **skill**, **domain** and **task form**.

The TF-IDF run one directory up (`../README.md`) answers a different question:
it pools all five corpora and takes a global top-30 on lexical cosine. That
cannot answer this one. "Write the result to `/app/out.txt`" appears in half
the pool, and two tasks can share every content word while exercising nothing
in common. So here retrieval only narrows the field and an agent decides.

**The two results barely overlap: only 12.5% of these 890 rows (111) appear
anywhere in the TF-IDF top-30, and 34 of the 89 tasks share nothing with it.**

## The answer

| file | rows | what |
|---|---|---|
| `results/tb21_agentic_top10.csv` | 890 | **the deliverable** — 10 per TB task, mixed sources, ranked, with the three axis scores and a reason |
| `results/tb21_agentic_top10_ids.csv` | 89 | one row per TB task: the 10 ids pipe-joined, their sources, mean `overall` |
| `results/tb21_agentic_per_source_top10.csv` | 4,450 | intermediate — best 10 from *each* pool, when you want guaranteed per-pool coverage |
| `results/agentic_summary.json` | — | source distribution, score means, near-duplicate report, validation problems (currently 0) |

## Layout

```
agentic/
  run.sh                 driver; stops in front of the one stage a script can't run
  verify.py              proves the published outputs still reproduce
  env.json               pinned interpreter, package versions, model snapshot
  cache_manifest.json    sha256 + row count of every corpus file

  bootstrap_cache.py     1. datasets      -> work/cache/*.jsonl
  build_candidates.py    2. cache         -> work/candidates/  (4 retrievers)
  make_packets.py        3. candidates    -> work/packets/     (agent-sized)
  dispatch.py            4. packets       -> the 18 team prompts
  AGENT_SPEC.md             the rubric handed to every agent
  merge_agentic.py       5. rerank/       -> results/*.csv         (validates every id)
  tools.py                  the agents' search interface to the pool

  rerank/                89 agent judgement files — KEEP, not regenerable
  results/               derived CSVs — regenerable from rerank/
  work/                  scratch — regenerable from the datasets, safe to delete
```

`rerank/` and `results/` are the results. `work/` is 1.1GB of rebuildable
intermediates; deleting it costs one `./run.sh --from cache` (minus the agents).

## Reproducing

```bash
./run.sh --verify          # checks the published outputs still reproduce (~1 min)
./run.sh --verify --full   # also replays retrieval end to end
./run.sh                   # rebuild everything, pausing before the rerank
./run.sh --from merge      # re-derive results/*.csv from rerank/
```

**Reproducibility here means three different things, and `verify.py` checks
them separately rather than pretending they are the same:**

| stage | reproducible? | checked by |
|---|---|---|
| A. corpus → `work/cache/` | **exactly** — sha256 pinned | `bootstrap_cache.py --verify` |
| B. cache → candidates → packets | **exactly** — same bytes | `verify.py --full` |
| C. packets → `rerank/` (the agents) | **no** — re-running gives different lists | integrity only: 89 files, every id real |
| D. `rerank/` → `results/*.csv` | **exactly** — same bytes | `verify.py` (default) |

Stage C is the honest gap. LLM judgement is not a deterministic function, so
re-running the agents will not reproduce these 890 rows. What is reproducible
about it is everything around it: the same corpus (A), the same candidate
packets (B), the same team split and the same prompt text (`dispatch.py`), the
same rubric (`AGENT_SPEC.md`), the same model (`env.json`). The judgements
themselves are kept as data in `rerank/`, and `results/` is a pure function of them.

So: **treat `rerank/` as an input, not an artifact.** It is the one thing in
this directory that cannot be recomputed.

### Stage 3 in practice

```bash
python dispatch.py plan      # 89 tasks -> 18 teams of 5 (writes teams.json)
python dispatch.py prompts   # the 18 prompts, verbatim
python dispatch.py status    # who still owes what
```

Hand each prompt to one agent, all 18 in parallel; each writes
`rerank/<tb_id>.json`. Wall clock was ~25 minutes, ~6.6M agent tokens total.

Five per team and not ten: a packet is ~20k tokens, so ten of them plus the
agent's own tool output overruns a subagent's context and it truncates the last
tasks without saying so.

## How it works

### Stage 2 — recall (`build_candidates.py`)

Four retrievers, each **fit per corpus** rather than on the pooled 56k, so a
37k-row corpus cannot starve a 1.3k-row one:

| retriever | what it catches |
|---|---|
| `tfidf_word` | TF-IDF 1–2gram cosine — topical overlap |
| `tfidf_char` | TF-IDF `char_wb` 3–5gram — tool names, filenames, flags (`qemu`, `.pcap`, `--no-ff`) |
| `bm25` | Okapi BM25, length-normalised lexical |
| `dense` | `gte-Qwen2-1.5B-instruct` cosine, 2048-token window — paraphrase and skill-level match |

Top-25 each, unioned and ordered by reciprocal-rank fusion → 62–82 unique
candidates per (task, corpus). Dense vectors are cached in `work/emb/*.npz`, so
a rerun of stage 2 with a warm cache touches no GPU at all and takes minutes.

Two things worth knowing:

- `build_candidates.py` caps its own GPU allocation (`GPU_MEM_CAP_GB = 10`).
  This node's cards run training at ~95% memory; without the cap an embedding
  job can grow into someone else's run. `--no-dense` skips the GPU entirely and
  falls back to the three lexical retrievers.
- It does **not** reuse `compute_tb21_tfidf.py`'s `instruction_of()`. That
  version cuts the instruction at the first blank line, which for the two
  parquet corpora (TMax, RTS) keeps only the opening sentence. Here the
  instruction runs to the next section header, so the retrievers see the whole
  problem statement.

### Stage 4 — precision (the agents, `AGENT_SPEC.md`)

Per task an agent profiles what the TB 2.1 task actually demands, rescores the
candidates, **runs its own searches over all 56,307 rows** (`tools.py search` /
`words`) to catch what retrieval missed, and scores each candidate 1–5 on:

- **`skill`** — same capability at the crux? (parse a binary format, reason
  about a race, read a stack trace back to a commit, implement a paper's
  algorithm). Weighted highest.
- **`domain`** — same stack and subject area?
- **`task_form`** — same genre and comparable depth?

`overall` is a judgement, not an average. Each agent emits 10 per corpus (so
every pool puts its best forward) and then 10 overall drawn from those 50, with
no per-pool quota.

### Stage 5 — validation (`merge_agentic.py`)

Agents write ids by hand, so every id is re-checked against the pool before it
reaches a CSV: id must exist, must belong to the corpus it was filed under,
scores must be ints 1–5, reasons non-empty, exactly 10 per list, no duplicates,
and every `final_top10` id must also appear in `per_source`. A task failing any
check is rejected and named in `agentic_summary.json` rather than merged
silently. All 89 passed.

## What came out

| | |
|---|---|
| final slots by source | TMax 562, TerminalWorld 127, RTS 90, Rebench 71, Smith 40 |
| rank-1 wins | TMax 74, TerminalWorld 9, RTS 6 |
| mean scores | skill 4.10, domain 4.28, task_form 3.63, overall 3.99 |
| `overall` histogram | 5:172, 4:543, 3:173, 2:2 |
| single-source top-10 | 5 tasks |
| near-duplicate pairs inside a top-10 | 1, at cosine 0.78 |

All 18 teams independently reported the same thing: the lexical shortlist was
dominated by boilerplate and clone families, and most of what they kept came
from their own searches. Concrete cases:

- `qemu-startup` — the candidate all four retrievers ranked first is an
  OCR-a-config-screenshot webhook task with no VM in it.
- `chess-best-move` — the shortlist was ~40 near-identical Tic-Tac-Toe *build*
  chores sharing one string with the query.
- `model-extraction-relu-logits` — ~169 duplicated "drop NaN rows from
  `/app/input.npy`" chores, retrieved on the `.npy` token alone.
- `rstan-to-pystan` — a regex for `Stan` matches inside "unde**rstan**d".

### Two caveats for anyone consuming the CSVs

**`overall` is not comparable across TB tasks.** Searches over all 56,307
instructions found *zero* coverage for MTEB, chess/FEN, gate-level circuits,
Scheme metacircular evaluation, ARC-AGI grids, gcov/lcov, `torch.distributed`
pipeline or tensor parallelism, COBOL, CoreWars/Redcode, Stan/R, and model
extraction; one hit each for fastText and MuJoCo. Those tasks top out at 3–4 by
construction. Do not threshold globally.

**SWE-Smith and SWE-Rebench score 1–2 on `task_form` almost everywhere** — they
are GitHub-issue bug fixes, a different genre. That is the genre, not a quality
signal; read their `skill`/`domain`.

### Near-duplicate clusters

Agents kept 2–3 representatives per clone family instead of filling slots with
copies, so the CSVs are already thinned. Families they named, in case a
downstream mixer pools across tasks: RTS `secret.key` history-rewrite (300+),
RTS ARM64 assembly (100+), RTS `sds-mnist` (~100), RTS pypiserver (~180), RTS
echo-vs-printf escapes (~200), RTS git-katas (110 copies of one kata), and
SWE-Smith single-repo monocultures (fvcore for anything PyTorch, conan for
anything build, gunicorn for anything service). `agentic_summary.json` reports
text-similarity duplicates that survived.

## Querying the pool

`tools.py` is the agents' interface and is useful on its own:

```bash
PY=/data/yichuan_wang/leann2-venv/bin/python
$PY tools.py show <task_id> [...]         # full instruction
$PY tools.py full <task_id>               # full doc: tests, verifier, env, oracle
$PY tools.py search <corpus|all> <regex>  # regex over all 56,307 instructions
$PY tools.py words <corpus|all> w1 w2 ... # rank docs by how many words hit
$PY tools.py sources                      # corpus names + aliases
```

Aliases: `tmax`, `rts`, `tw`, `smith`, `rebench`, `all`. The sqlite index at
`work/pool.sqlite` builds itself on first use from `work/cache/`.

## Inputs

Five published HF datasets plus the TB 2.1 task trees, 56,307 training rows and
89 queries. Provenance, download URLs and the local paths on this machine are
documented in `../README.md`; `bootstrap_cache.py` reads them through
`../compute_tb21_tfidf.py`'s loaders so the corpus text is composed identically
to the TF-IDF run, and falls back to downloading from HF when a local parquet is
absent. `cache_manifest.json` pins what was actually read.


---

# v2 — the enlarged pool

Two datasets were added: **Terminal-Lego-15k** (15,048 tasks built from
StackOverflow questions) and **CalibForge** (5,431 tasks calibrated by solver
disagreement). The pool went from 56,307 to **76,786** across seven corpora,
and every candidate now carries a **verifier grade**.

| | v1 | v2 |
|---|---|---|
| mean skill / domain / task_form | 4.10 / 4.28 / 3.63 | **4.37 / 4.55 / 4.18** |
| mean overall | 3.99 | **4.25** |
| candidates scored 5 | 172 | **268** |
| candidates scored ≤3 | 175 | **42** |
| tasks whose top-10 is one corpus | 5 | **0** |

`task_form` rising by 0.55 is the informative one: the new corpora contain
tasks of the same *shape*, not merely the same subject.

Final slots by source: CalibForge 500, TMax 217, Terminal-Lego 97,
TerminalWorld 25, Rebench 18, RTS 17, Smith 16. CalibForge takes 71 of the 89
rank-1 slots. Eighteen teams reported the same reason independently — it
contains whole task *families* built on the same templates as TB 2.1 tasks.

## The verifier grade

A task whose tests can be passed without solving it is bad training data
however well it matches. One real Terminal-Lego task passed its entire suite
with a three-line shell script that did not do the task. So every pooled task
is graded (`verifier_strength.py`, from the original sources, never from the
lossy composed cache) and the grade travels in the packet:

| grade | meaning | pool |
|---|---|---|
| `strong` | ≥half and ≥3 assertions pin a value, or ≥8 do | 17,100 |
| `ok` | ≥quarter, or ≥4 | 17,900 |
| `weak` | at least one | 1,500 |
| `behavioral` | nothing pinned, but a test runs the artifact and asserts on its exit status | 600 |
| `FREE` | nothing but file-exists / non-empty / substring-in-own-source | 602 |
| `repo-tests` | runs the upstream project's suite (SWE-Smith, SWE-Rebench) | 2,869 |
| `absent` | no verifier ships with our copy (all of RTS) | 37,484 |

**No `FREE` candidate may enter `final_top10`**, enforced in
`merge_agentic.py --v2`, not left to the agents.

### This grader is a heuristic and errs in both directions

It was wrong six times during this run, each time found by an agent reading the
tests. Do not treat a grade as a verdict.

Errors toward *under*-grading (careful verification called vacuous): counting
TMax's `test_initial_state` — whose job is to check preconditions — as a reward
test; missing comparison against a loaded oracle or a fuzzed reference binary;
missing `== f"..."` because the f-string prefix was absent from the pattern;
missing thresholds against a variable (`mse <= threshold`) and regexes pinning
a computed value; and reading `assert "login:" in combined` as a substring
check when `combined` is socket output.

The structural fix was to stop enumerating what counts as *good* verification —
an open-ended list, so anything unusual fell through — and enumerate the
**vacuous** forms instead, a short closed list, crediting everything else.

Errors toward *over*-grading survive: `tw_248132` grades `strong` but every
assertion is against files the agent writes itself (its shipped oracle is a
`touch` script), and `tw_28581` grades `strong` on regexes over script source
that is never executed.

`verifier_overrides.json` records grades a human-read agent overrode, with the
reason. It currently holds one.

## Contamination screen — do this before training

`work/tb21_near_duplicates.tsv` lists **38 (TB2.1, training task) pairs at dense
cosine ≥ 0.85**, covering 16 of the 89 TB 2.1 tasks. The closest is
`polyglot-c-py` ↔ CalibForge `software-engineering_20260617_210354_017` at
**0.944** — teams independently described that task as "one file that is valid
Python and valid C, both printing the same Fibonacci number", which is the
TB 2.1 task. Training on these is training on the eval set.

By corpus: CalibForge 20, TMax 15, TerminalWorld 2, RTS 1. **TMax contributes
15 pairs of its own**, so this is not a defect of the new data; the enlarged
pool just makes an existing problem easier to see.

CalibForge also ships **98 auto-generated filler tasks** whose text pastes a
TB 2.1 task name in as a domain label ("…for the `path-tracing` domain") while
the body is a generic directory-hashing utility. They match by name, grade
`ok`, and teach nothing — the worst possible combination. They are listed in
`work/calibforge_inspector_filler.txt` and `make_packets.py` now drops them.
None reached any result: the agents rejected every one unprompted.

## Running v2

```bash
python add_corpora_v2.py                                  # the two new corpora -> work/cache
python verifier_strength.py                               # grade all 76,786
python build_candidates.py --out work/candidates_v2       # seven-corpus retrieval
python make_packets.py --src work/candidates_v2 --dst work/packets_v2 --show 16 --snippet 460
python dispatch.py prompts                                # 18 team prompts (point them at AGENT_SPEC_V2.md)
python promote_pass.py --dispatch                         # after the run: who was barred by a stale grade
python merge_agentic.py --v2 --strict --rerank rerank_v2 --out results_v2
```

`promote_pass.py` exists because the grader was corrected mid-run: it finds
tasks whose `final_top10` lost a slot to a grade that has since been fixed. It
flagged 22 of 89; five correction agents re-read them and changed 13, declining
the rest on merit — including one that corrected an earlier agent's reading.
