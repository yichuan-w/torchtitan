# How hard the seeds are: GPT-5.6-sol at pass@5

The corpus had been validated for consistency — the environment builds, the
reference solution runs, the verifier grades it — and never for difficulty.
Consistency says a task is well-formed. It does not say whether a task is worth
training on, and the two failure modes look identical from inside a validation
run: a task every model solves teaches nothing, and so does one no model solves.

So all 861 tasks whose reference solution earns a passing grade were given to a
solver, five attempts each, fresh container per attempt. 821 produced a verdict.

## The distribution

| pass@5 | tasks | share | |
|---|---|---|---|
| 1.0 | 591 | 72.0% | solved every attempt |
| 0.8 | 97 | 11.8% | |
| 0.6 | 25 | 3.0% | |
| 0.5 | 3 | 0.4% | |
| 0.4 | 14 | 1.7% | |
| 0.2 | 28 | 3.4% | |
| 0.0 | 63 | 7.7% | solved on no attempt |

Grouped by what a policy can do with them:

| | tasks | share |
|---|---|---|
| no gradient — solved every time | 591 | 72.0% |
| **carries signal** | **167** | **20.3%** |
| no gradient — solved never | 63 | 7.7% |

**The seed layer is saturated.** Under GRPO the 591 contribute an advantage of
exactly zero: every rollout in the group earns the same reward, the group has no
variance, and the gradient is zero. The 63 at the other end contribute zero for
the same reason from the opposite side. Roughly a fifth of the corpus is doing
any work.

This is the concrete reason RST rewrites fifteen times rather than once. It is
not a data-volume exercise — it is difficulty manufacture. Seed reference
solutions run to a median of 10 lines against TB2's 61, and RST's own round-1
rewrites land at 67, on TB2's difficulty. The pass@5 curve is the behavioural
form of that same gap: not "these look easy" but "the strongest available model
solves them five times out of five".

## Where the difficulty is

Saturation by domain, over domains with at least 15 graded tasks:

| domain | solved every time | |
|---|---|---|
| Scientific Computing | 16/18 | 89% |
| File & Storage | 21/25 | 84% |
| Version Control | 25/30 | 83% |
| Networking | 20/26 | 77% |
| Scripting & Automation | 109/143 | 76% |
| Data Analysis | 12/16 | 75% |
| Environment Setup | 63/86 | 73% |
| Software Development | 128/178 | 72% |
| System Administration | 78/109 | 72% |
| Security | 51/73 | 70% |
| Cloud & Infrastructure | 10/15 | 67% |
| Database Operations | 16/24 | 67% |
| Debugging & Testing | 9/16 | 56% |
| **Containers & Orchestration** | **13/30** | **43%** |

Containers and debugging hold roughly twice the usable signal of the rest. If
seeds have to be selected rather than rewritten, that is where to select from.

Attempts run to a median of 4 turns — 4 when the task is solved, 6 when it is
not. Nothing is running out of its 25-turn budget; failures are failures, not
timeouts.

## The 63 nobody solved

These are the only tasks in the corpus a frontier model cannot do, which makes
them the most valuable part of it — provided they are hard rather than unfair. A
verifier that asserts a path the instruction and environment never reveal is
unsolvable by anything except luck, and the corpus has a lexical audit for
exactly that.

**27% of the 63 have such a path, against 23% across all 861.** The enrichment is
within noise. They are the hard tail, not a pile of broken tasks, and they should
be kept.

## Two costs of running this

**The provider refuses some of the work.** 235 of 4,270 attempts (5.5%) came back
refused by OpenAI's content filter, which reads terminal work — permissions,
process control, binary inspection — as a cybersecurity request. On 33 tasks all
five attempts were refused, so those tasks cannot be evaluated against that
provider at all. Recorded as their own outcome; counting them as failures would
understate every rate above.

**A solver can destroy its own container.** The container lives as long as the
process it was started with, and an agent running `pkill sleep` takes the
environment down with it, losing the attempt. That cost two of the first ten
tasks. With a keeper that traps signals and re-enters its sleep, it is 9 of
4,270 (0.2%), each retried once.

## Reproducing

```bash
python3 scripts/solve_eval.py \
  --tar tasks-00000.tar \
  --ids results/verified_pass_ids.txt \
  --results results/solve_all861.jsonl \
  --attempts 5 --max-turns 25 --workers 6
```

`solution/` is never staged into the container and `tests/` is staged only after
the solver stops, or the verifier is readable by the thing being tested.
