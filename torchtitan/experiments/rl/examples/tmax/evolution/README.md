# terminalworld-seeds

Seed corpus and task-synthesis loop for the terminal-agent RL work. Two things
live here: the TerminalWorld seed tasks packaged and validated for training use,
and a reimplementation of RST's recursive synthesis that derives harder tasks
from them.

## Running the synthesis loop

Needs Docker and an OpenAI key. Everything runs against a task tar; nothing
needs a sandbox provider.

```bash
export OPENAI_API_KEY=sk-...
export SYNTH_MODEL=gpt-5.6-sol        # optional; alias `gpt-5.6` resolves here

python3 synth_loop.py \
  --seeds results/verified_pass_ids.txt \
  --tar   tasks-00000.tar \
  --out   data/synth \
  --results results/synth.jsonl \
  --rounds 1 --per-round 20 --attempts 4
```

`--attempts` is the rollout count per generated task, used for the difficulty
gate. `--rounds` is recursion depth: round *n+1* seeds from what survived round
*n*, which is where difficulty compounds.

The task tar is the `data/tasks-00000.tar` from
[andylizf/TerminalWorld-Seeds-Clean](https://huggingface.co/datasets/andylizf/TerminalWorld-Seeds-Clean),
and `--seeds` takes task ids one per line — `results/verified_pass_ids.txt` is
the 861 whose reference solution earns a passing grade.

### What comes out

```
data/synth/round_1/<task_id>/     instruction.md, environment/, solution/, tests/
results/synth.jsonl               one record per seed: operator, gate outcomes,
                                  rollout rewards, pass@k, verdict, token usage
results/synth.log                 per-task progress with timestamps
```

Re-running the same command resumes: seeds already in the results file are
skipped, so a killed run continues rather than restarting.

### Gates

A generated task is only kept if it survives four, in order — the loop is mostly
these, since synthesis without them accumulates tasks that look fine and cannot
be trained on.

| gate | rejects |
|---|---|
| preflight | missing `COPY` sources, no `FROM`, empty solve.sh, no test function — before spending a build |
| build | the environment does not build from the Dockerfile alone |
| oracle | the generated solution runs but its verifier does not grade it 1 |
| audit | the verifier asserts paths the instruction and environment never reveal, or the instruction names the verifier back |

What survives is then classified by rollout signal —
`unfair / too_hard / leaking / too_easy / usable / ungraded` — by crossing pass@k
with the audit, so "nobody solved it" is separated from "the task withholds what
is needed", and "everybody solved it" from "the answer is in the prompt".

### How good the output is

`review_synth.py` scores a run on six numbers with a target beside each,
and `results/rewrite_quality.md` records what six prompt versions moved. The
short version: the too-easy end came down from 100% of graded rewrites to 23%,
against seeds that sit at 72%; tasks carrying a check that rejects an answer the
agent never produced went from 40% to 70%, against RST's 100% by construction.

The output was then checked independently. Nine accepted tasks were re-measured
at k=5 by `solve_eval.py` — the same harness and protocol used on the seeds, with
no knowledge of the gate's verdict:

| pass@5 | tasks |
|---|---|
| 1.0 | 3 |
| 0.8 | 1 |
| 0.6 | 3 |
| 0.2 | 1 |
| 0.0 | 1 |

**5 of 9 land in the usable band against the seed corpus's 20.3%.** Four moved
out of the band the loop's k=4 gate had put them in, which is why shipping takes
two stages: the gate filters cheaply, and `collect_accepted.py --verified` keeps
only what a second pass confirms.

```bash
python3 collect_accepted.py \
  --runs 'results/synth_*.jsonl' --tasks data/synth \
  --verified results/solve_accepted.jsonl \
  --out data/accepted --tar data/accepted/tasks-00000.tar
```

### Operators

Five families of eight. `data/operator_cards_authors.json` holds the authors' own
cards, sent by 煜坤 on 2026-08-15 (`docs/rst-authors/`); ours, written from the
paper's protocol before that, are in `data/operator_cards.json` and still
selectable with `SYNTH_CARDS=`.

Selection follows their formula: `S(o) = L(o) × D(f(o)) × P(o)` over the twelve
operators the local scan ranks highest, with `D(f) = max(0.25, 1 + 0.2N − n_f)`
for family balance and `P(o) = 1/(1+n_o)` against repeats. A seed with no local
signal for any operator is declined rather than forced onto one —
`check_operator_selection.py` checks each clause against their document.

## Caveats before you run it

Checked against a fresh clone, not from memory.

**Dependencies.** The loop itself imports nothing outside the standard library —
`synth_loop`, `synth_client`, `docker_validate` and `solve_eval` all run on a
bare `python3` — and shells out to `docker`. The analysis scripts are the
exception: `compare_tw_tmax.py` and `apply_recovery.py` need `pandas` and
`huggingface_hub`.

**The task tar is not in the repo.** `data/` is gitignored because it holds a
1.2GB archive. Pull it from
[TerminalWorld-Seeds-Clean](https://huggingface.co/datasets/andylizf/TerminalWorld-Seeds-Clean)
and pass its path to `--tar`.

**Disk is the constraint, not CPU.** Each worker holds one image at a time and
drops it when the task ends; the run prunes its build cache below 25G free and
aborts rather than filling a shared filesystem below 10G. On a box with 224 cores
we run 24 workers and the limit is still disk.

**Throughput and cost.** About four tasks an hour per worker — five synthesis
calls, three repair passes, a build, an oracle run, and k rollouts of up to 25
turns, plus up to three calibration rounds that repeat the last four. Roughly
$0.76 per attempt on `gpt-5.6-sol`; `deepseek-v4-pro`, which the RST authors use,
prices the same token count at $0.028.

**The provider refuses some of this work.** 5.5% of solver attempts come back
refused by OpenAI's content filter, which reads terminal work — permissions,
process control — as a cybersecurity request, and 33 of the 861 seeds have every
attempt refused. They are recorded as their own outcome, not as failures. A
non-OpenAI model should not hit this.

**Difficulty measured once is not a property of the task.** The gate samples k=4;
re-measuring accepted tasks at k=5 moves roughly a third of them out of the band.
Ship through `ship_dataset.sh`, which re-measures and keeps what held, rather
than trusting the gate.

**The gate and the verification must run the same solver.** Measuring one with a
solver that stops after three turns and the other with a solver told to check its
work produces a number about the solvers. That mistake produced a plausible 62%
here before it was caught.

## Corpus

| | |
|---|---|
| `andylizf/TerminalWorld-Seeds` | all 1,530 TerminalWorld tasks, RST's release layout |
| `andylizf/TerminalWorld-Seeds-Clean` | the 1,353 that build, run and get graded |

Of the 1,353, **861 have a reference solution that earns a passing grade**, 446
score zero, 46 could not be built. Filter `reward_verdict == "pass"` for the 861.
The dataset card is the authoritative account, including two runner defects that
moved these numbers on 2026-08-16 and the columns added to make each verdict
checkable (`run_mode`, `dockerfile_repaired`, `reference_partial`,
`verdict_flipped`).

### How hard the seeds actually are

GPT-5.6-sol, five attempts per task, over the 861. 821 produced a verdict.

| pass@5 | tasks | |
|---|---|---|
| 1.0 | 591 | 72.0% — solved every time, so no gradient |
| 0<p<1 | 167 | 20.3% |
| 0.0 | 63 | 7.7% |

**The seed layer is saturated for a frontier model**: roughly a fifth of it
carries usable training signal. That is consistent with the shape of the tasks —
seed reference solutions have a median of 10 lines against TB2's 61 — and it is
the concrete reason RST runs fifteen rounds of rewriting rather than one. Their
round-1 rewrites land at a median of 67 lines, on TB2's difficulty.

### Reward density, seeds vs RST

RST's contract requires at least four `reward_checks` per task with fixed roles
(`required_evidence` / `intermediate_artifact` / `final_semantics` /
`no_shortcut`). Measured against the seeds:

| | seeds (1,530) | RST |
|---|---|---|
| subtests per task | median 3, mean 4.0, range 0–22 | median 4, range 4–13 |
| ≥4 subtests | 46% | 100% by construction |
| a check that rejects placeholder / hard-coded / stale output | 24% | 100%, as `check_04` |

So dense reward is not something RST introduced — the seeds are already
multi-check, and their checks are not shallow (0.5% assert only that a file
exists; 70% mix existence with semantic comparison). What RST adds is a floor of
four, fixed roles for them, and an anti-shortcut check on every task rather than
on a quarter of them.

## Other tools

| script | what it does |
|---|---|
| `docker_validate.py` | build a task, run its reference solution, read `/logs/verifier/reward.txt`. `--repair` fixes heredocs Docker cannot parse |
| `solve_eval.py` | run a solver over tasks k times and report pass@k. Never stages `solution/`, stages `tests/` only after the solver stops |
| `price_synth_run.py` | price a run from its recorded token usage |
| `build_seed_dataset.py`, `build_clean_dataset.py` | package the corpus |
| `analyze_seed_vs_tb2.py` | difficulty and domain comparison against TB2 |

## Two things to expect from the harness

**The provider refuses some tasks.** 5.5% of solver attempts came back
`refused_by_provider` — OpenAI's content filter reading terminal work
(permissions, process control) as a cybersecurity request. 33 of the 861 had all
five attempts refused and are not evaluable against that provider at all. These
are recorded as their own outcome rather than as failures, because counting them
as failures understates every rate. A non-OpenAI model should not hit this.

**A solver can destroy its own container.** It keeps the container alive through
a shell that traps signals and re-enters its sleep, because `sleep infinity` dies
to a stray `pkill sleep` and takes the attempt with it — that cost two of the
first ten tasks before the fix and 0.2% after. An attempt whose container is lost
is retried once and never counted as a failure.

## Costs

Per synthesis attempt: ~21k input and ~22k output tokens, about 10 model calls.
That is $0.76 on gpt-5.6-sol and $0.028 on deepseek-v4-pro, whose rates put it
next to the $0.05 a task RST reports — the gap is the model, not the pipeline.
RST's own README says they run `deepseek-v4-pro`.
