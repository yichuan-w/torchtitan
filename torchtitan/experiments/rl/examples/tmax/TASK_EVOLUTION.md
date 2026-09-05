# Online task evolution: re-tune every no-signal group (0/k and k/k)

## The problem this closes

A GRPO group whose siblings **all pass** or **all fail** has zero reward
variance and trains nothing. One handling is to drop such prompts: a run points
`TMaxDataset.skip_ids_path` (`SWE_SKIP_PROMPTS`) at a list of task ids built
from an earlier run's signals, and they are never sampled again. The pool only
ever shrinks, and the prompts it sheds are exactly the ones the current policy
has outgrown (all-pass) or cannot yet touch (all-fail).

`drop` and `evolve` are two handlings of the **same** decision point. Instead of
shedding a no-signal prompt, move it to where the policy now is:

- **all fail** -> make it easier: a hint drawn from a failing trajectory
- **all pass** -> make it harder: one more constraint or stage

The prompt stays in the pool, re-tuned to the policy. This is the same
recursive-difficulty idea the seed pipeline uses offline; here it runs on the
training model's own rollouts.

## Why this lives in the rollouter, beside the zero-std detector

`_maybe_emit_evolution_signal` sits next to the zero-std detector, which is the
right layer:

- it is the **existing no-signal detector**: it already computes `pstdev == 0`
  and has the `sample` (`instance_id`) and the `rollouts` (the trajectories);
- the **write mechanics** are already solved there: one write-once file per
  rollout and one per group, each written beside its name and renamed into
  place, the pattern that survives the oilfs FUSE mount and the pooled
  RolloutWorker processes (appending to a shared file does not);
- putting it here keeps the trainer doing **only observation**. Adjusting a task
  means rewriting its four files and re-validating them in a container (build,
  oracle, shortcut check), minutes of Docker work that must not block a rollout,
  so it belongs on the data side, asynchronously. The trainer emits the signal;
  it never runs the factory.

## What the trainer writes

Two switches, both on by default, and both write under the run directory the
launcher exports as `TRL_RUN_DIR`. `SWE_ROLLOUT_RECORDS=1` writes every rollout
once, as `rollouts/<task>/g<group>-r<idx>.jsonl`: the rollout on line 1, then
one line per turn. `SWE_EVOLUTION_SIGNALS=1` writes one signal per
zero-variance group as `signals/<task>--g<group>.json`, in **both** directions,
always: all-fail (0/k) is `easier`, all-pass (k/k) is `harder`, and a group with
any reward variance is already producing signal and is left alone. The signal
carries the task, the row's `rev`, the run, the group, `solved` / `total` and
the paths of the group's rollout records relative to the run directory; the
transcript is referenced, never copied. An all-fail group in which no attempt
took a turn is not a signal but an `infra_quarantine` advisory. The formats are
in [`LAYOUT.md`](LAYOUT.md).

Dropping and evolving are independent: dropping sheds a prompt, evolving
re-tunes it, and a run enables either or both. With `SWE_EVOLUTION_SIGNALS=0`
the trainer writes no signals and the loop has nothing to read.

## The loop it completes

```
 trainer                                      loop (evolution/evolve_ondella.py)
 -------                                      ---------------------------------
 zero-std group (all-pass / all-fail)
   -> runs/<run>/signals/<task>--g<group>.json  --> pending = signals with no ledger line
      (attempts -> runs/<run>/rollouts/...)         easier -> simplify (hint from trajectory)
                                                    harder -> evolve  (one operator, one rung)
                                                    revalidate: build, oracle, no-shortcut
                                                    -> evolution/tasks/<task>/rewrites/<stamp>--<job>/
                                                       accepted: package/ becomes r<N+1>/
   hot-reloads data/mix/live.jsonl  <-------------- fold: publish data/mix/history/v<N>--<stamp>.jsonl
   reads evolution/status.json                          and relink live.jsonl (replace by task id,
     -> W&B evolution/* gauges                           pool size fixed)
```

The signal references the run's rollout records instead of carrying its own
copy of the transcript, and a rewrite hardlinks those records into its package
as `traces/attempt-NN.jsonl`, so the agent that rewrites a task reads the same
file the trainer wrote, in the one rollout record format.

Every signal the loop sees gets one line in `evolution/ledger.jsonl` (`handled`,
`deferred`, `junk`); `evolution/status.json` is rebuilt from the ledger and the
tasks' lineage at the end of every round, and the trainer reads it to put
`evolution/pending_signals`, `evolution/handled_total`,
`evolution/accepted_total`, `evolution/rejected_total`,
`evolution/blocked_total`, `evolution/kept_total` and `evolution/mix_version` on
W&B beside the training curves.

## Enabling it, and keeping batch size fixed

Evolving is meant to replace a prompt in place, not remove it, so it pairs with
**not** dropping:

```bash
export SWE_DROP_ZERO_STD=0        # keep zero-std groups in the batch (advantage 0,
                                  # no gradient, but the batch size per step is
                                  # unchanged)
export SWE_DATA_HOT_RELOAD=1      # re-read live.jsonl when a new version is published
# SWE_EVOLUTION_SIGNALS and SWE_ROLLOUT_RECORDS default to 1
# leave SWE_SKIP_PROMPTS unset   # do not also drop what is being re-tuned
```

`SWE_DROP_ZERO_STD=0` is the existing switch (`drop_zero_std_reward_groups=False`,
`skip_zero_advantage_samples=True`): the zero-std group stays in the batch and
simply contributes no gradient, so every step sees the same number of prompts.
Dropping would shrink the batch by exactly the prompts we mean to keep and re-tune.

The loop folds each accepted rewrite back **at the same row** of the mix, a
replace and not a delete, so the pool size, and the batch, stay fixed while the
prompts move to the policy. How the loop is started, restarted and watched is in
[`evolution/RUNBOOK.md`](evolution/RUNBOOK.md).

Keeping `SWE_SKIP_PROMPTS` set as well is possible (drop as a fallback), but then
a prompt is both dropped and evolved; leave it unset while evolving.
