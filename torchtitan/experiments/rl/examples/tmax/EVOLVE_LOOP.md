# The evolve loop: what happens to one task

The evolve loop does one thing: take a task that has stopped producing a
learning signal, rewrite it into one the current policy can still learn from,
and put it back at its own row of the mix.

This document covers two things: everything that happens to one task between
being stamped and landing back in the mix, and how the rewriting itself is done.
Whether the loop runs online (beside a live run) or offline (a chosen batch
driven to completion) only changes who produces the signals; the chain below is
the same either way.

`LAYOUT.md` is the on-disk contract every path here comes from, and
`evolution/RUNBOOK.md` is how the loop is started, restarted and watched.

## Why the pool has to move

A task is attempted k times per training step (`SWE_GROUP_SIZE`, 12 in this
root's runs). GRPO trains on the spread between the attempts that solved it and
those that did not. A group where all of them pass and a group where all of them
fail both have zero advantage, so those k rollouts move no gradient, and each
rollout is a container plus an agent driving up to 120 turns of terminal work.

So what a task is kept for is not that it is hard. It is that the current policy
solves it sometimes. That property is defined against one specific set of
weights, and those weights change every step, so a fixed pool decays toward both
ends: what the policy outgrows becomes all-pass, what it cannot reach stays
all-fail, and the fraction still producing gradient shrinks.

Dropping those tasks is the cheap handling, and the pool then only shrinks,
shedding exactly the prompts the policy has just learned or is one step from.
The loop is the other handling: keep the task, move it to where the policy is,
and keep the pool the size it was.

## What one task goes through

### 1. The trainer stamps it

`rollouter.py::_maybe_emit_evolution_signal` sits beside the zero-std detector,
which already holds the group's rewards, the sample and every trajectory.

It takes the group's solved fraction and compares it against two thresholds. At
or above `SWE_EVOLUTION_HARDER_RATIO` (default 1.0 for all-pass; 0.9 in this
root's runs, so one short of all-pass already asks) it wants `harder`. At or
below `SWE_EVOLUTION_EASIER_RATIO` (default 0.0, nothing solved) it wants
`easier`. Between the two it does nothing, because the group is already
producing signal. Counting solves rather than testing each reward against zero
keeps the question independent of what a failure scored, which
`SWE_WRONG_SUBMIT_PENALTY` would otherwise change by making a graded-wrong
submit negative. Under dense rewards the original zero-variance rule stands,
because a partial-credit mean is not a solve rate.

An attempt the harness could not score never reaches this: `infra_failed` puts
NaN on its reward and `is_scored` drops it, so a group whose siblings all failed
that way has fewer than two scored rewards and asks for nothing. Whatever
reaches the threshold was scored, and a scored zero is a verdict on the task.

The signal is one small JSON at `runs/<run>/signals/<task>--g<group>.json`: the
task, the row's revision, the run, the group, `solved` / `total`, and the path of
each rollout record in the group. The transcripts are referenced, never copied.

Then training carries on without waiting for anything. The group keeps its
advantages and the batch keeps its size.

### 2. The loop picks it up

The loop is `evolution/evolve_ondella.py`. A round decides what to handle:

1. Every signal under `runs/*/signals/` with no line in
   `evolution/ledger.jsonl`. The ledger is the loop's only memory, so anything
   unrecorded is outstanding work and a crashed loop resumes by restarting.
2. One signal per task per round: the newest whose revision is the task's
   current one. A signal about a revision the task has moved past measured a
   version that no longer exists, and gets a `superseded` line. Two signals
   about the same revision would race for the same `r<N+1>`, so only one runs.
3. Unchanged feedback (same task, revision, direction, `solved` and `total`)
   reuses the decision from the completed rewrite instead of redoing it. A
   different run id, timestamp or record path is not new feedback.

The revision check also secures the invariant the loop rests on: a task's
revision cannot advance twice without training in between. The next rewrite
needs a signal whose rev equals the new revision, and only a training group on
that revision produces one. Measured on the online root: of 361 tasks the loop
worked on, 346 landed at least one rewrite and 152 advanced two revisions or
more, which is that alternation running for several rounds.

A signal asking `easier` while `SWE_EVOLVE_SIMPLIFY` is off gets a `deferred`
line, replayed if the switch is turned on.

### 3. It gets a working directory

Everything after this happens inside that directory. Nothing already on disk is
touched.

On a task's first signal, the seed package is copied whole from
`data/sources/<corpus>/tasks/<task>` to `evolution/tasks/<task>/r0/`, its
revision 0. The copy lands in `r0.incoming` and is renamed, so a crash mid-copy
cannot leave half a package for a later round to evolve.

Then `evolution/tasks/<task>/rewrites/<stamp>--<job>/` is created, holding:

- `package/`, the working copy of `r<rev>`, which is where the agent works
- `package/traces/attempt-NN.jsonl`, hardlinks to the group's rollout records,
  so the agent reads the bytes the trainer wrote rather than a copy
- `pretest.json`, a snapshot of the row's pin hook (`pre_test_sh`, run before
  grading) and its protected lists. Both live on the mix row and not in the
  package, and without the snapshot the rewrite would be validated against one
  set of lists and folded with another
- `rewrite.json`, this rewrite's record, written up to the last step

The task's resources are resolved at the same time: the `daytona_*` the row
declares, filled out with the fleet default. The agent works in a container that
size, and the reference solution is measured at that size.

### 4. The author agent rewrites it

One Codex session, cwd is `package/`. Three more things go into the package for
it: `AGENTS.md` (its role and rules, copied from
`evolution/agents/task_evolution.md` at every session, so editing that file
needs no restart), `sandbox` (the container tool), and a few declarations under
`run/` (the seed's size, the resources, the names the seed's verifier depends on,
the pin hook).

How it rewrites is the next section. When the session ends, the harness checks
that the agent actually ran its own check; a session that did not fails, and the
task stays as it was.

### 5. A second agent writes the verifier blind

On by default (`SWE_VERIFIER_AUTHOR` unset, so `blind`), for both directions.
Why, below.

The author's package is laid out again under this session's own directory with
`solution/`, `traces/`, `AGENTS.md` and `sandbox` removed, and `AGENTS.md`
replaced by `evolution/agents/verifier_author.md`. This session sees the
instruction, the environment and the seed's verifier, and not the solution.

What it writes is checked against independent probes, then copied back into the
author's package, replacing `tests/`.

Then comes the only meeting of the two sessions: the harness runs the hidden
reference solution against the blind verifier. A disagreement means one of two
things, either the verifier demands something the instruction never promised or
the solution does not do what the instruction says. The verifier's author gets
the failure and one chance to decide which and fix it. A second disagreement
discards the rewrite and the task goes back to training unchanged.

### 6. Revalidation

Whether the rewritten task is any good is settled by running it
(`evolution/feedback_loop.py::revalidate`).

There is no Docker on the training host, so this runs on Daytona: the same
provider and grading contract as the training rollouts, in a container the size
the row will actually be provisioned at. That last part was learned the hard
way: a task verified at the harness default and then starved in a 1-CPU row
comes back as a timeout, which reads as "too hard".

| gate | rejects |
|---|---|
| oracle | the rewritten reference solution does not earn full marks from the rewritten verifier |
| null probe | the verifier passes on an untouched workspace |
| step size | the reference solution's growth is outside the bound for this direction |
| unnamed paths | the verifier demands a path or name the instruction and environment never reveal. Recorded, not enforced |

The null probe replaced asking a model to guess a cheat command. Over one week
that approach rejected 148 rewrites: 29 deserved it, green before anything was
done, which the probe catches for free; the other 100 were a model that had read
both the solution and the verifier printf'ing the expected artifact into place,
an answer no policy could write. Those 100 were trainable tasks, thrown away at
a model call and a sandbox each.

The two unnamed-name audits ride along as advice rather than as gates. Measured
over 464 rewrites, the literals audit flagged 130 names without its seed
baseline and none with it, and all 130 were false positives; the paths audit
rejected nothing across 621 signals. A gate with no measured precision does not
get to discard a session.

One fast path: when only the instruction changed, the build, the verifier and
the reference solution are untouched, so the expensive rebuild is skipped. What
an instruction edit can still introduce is drift against the verifier, judged
before and after.

A structural rewrite that fails its oracle gets one repair round with the thing
it never saw, the real exit code and output tail, fed back to the session that
wrote the files. About 60% of TerminalWorld hardening rewrites die at this seam,
where the instruction, the solution and the verifier have to agree.

### 7. It is folded back into the mix

An accepted rewrite folds immediately rather than at the end of the round. A
round of many tasks runs for hours, and folding on acceptance means a loop
stopped mid-round loses only what was still in flight.

- The harness's own files come out (`AGENTS.md`, `sandbox`, `run/`, `traces/`)
- `package/` is renamed `r<N+1>/`, the task's new revision
- It is rebuilt into a mix row indistinguishable from a freshly prepared one
- It replaces the task at its own row, published as a new version under
  `data/mix/history/`, with `live.jsonl` relinked

Three things live on the row rather than in the package, and none of them is
simply copied across:

- The resources grow: `max(what the row declared, what the reference solution
  just measured)`. A hardened task's reference solution runs heavier, so the
  measurement rises and the folded row opens a bigger container. Only without a
  measurement does the declared value carry across unchanged. A row that lost
  them falls back to `TT_DAYTONA_*`, which on this corpus is 1 CPU against a
  measured 2, and starves
- The pin hook's script is carried, and may stop taking effect. What travels is
  `pre_test_sh` plus the environment identity the pins were captured against,
  while the row builder re-derives the new package's identity from its
  Dockerfile. Grading runs the check only when the two match: a rewrite that
  kept the environment keeps the check, and one that rebuilt it is skipped,
  which is what that guard is for, since pins captured against the old
  environment would refuse an honest attempt in the new one
- The protected lists are the author agent's to change. They default to the
  row's, and a `tests/protected_paths.json` in the package overrides them (two
  empty lists clear them). `agents/task_evolution.md` tells the agent to write
  one when the instruction requires a file or a command's output to stay put,
  so this is part of the rewrite rather than metadata being moved

Replace, never delete and append: appending lands at the end of the file, which
is the held-out slice, and rotates it; and a task no longer in the mix was taken
out deliberately and is not re-added. The pool size and the batch stay fixed
while the difficulty follows the policy.

Online, the trainer hot-reloads the new version (`SWE_DATA_HOT_RELOAD=1`). This
is also why evolving pairs with `SWE_DROP_ZERO_STD=0`: dropping zero-std groups
sheds exactly the prompts being re-tuned.

### 8. What it leaves behind

- `rewrite.json`: which signal, which input revision, the status (accepted,
  rejected, blocked, failed or kept), the stage it stopped at, every
  revalidation verdict, the resources, and the resulting revision
- `sessions/<stamp>--<kind>/`: one directory per Codex invocation, holding the
  prompt, stdout, stderr, `session.json`, and the CLI's own session jsonl
- `lineage.jsonl`: this task's rewrite and fold events
- one line in `ledger.jsonl`: `handled`, `deferred`, `reused`, `superseded` or
  `junk`
- `evolution/status.json`, rebuilt at the end of every round from the ledger and
  the tasks' lineage; the trainer puts its counters on W&B beside the training
  curves

Nothing under a run directory is ever moved or deleted.

## How the rewrite itself is done

Step 4 said where the agent works. This is what it is handed, what it can do,
and what it must declare.

### What the agent has

The instruction, the Dockerfile, the reference solution, the verifier, the rest
of the real package (entrypoints, fixtures, helpers, `task.toml`), and every
attempt in the group as a full record under `traces/`.

The rule: this package and these traces are the evidence. No reading sibling
tasks, prior experiment outputs or other sessions. Public tool documentation
stays available.

It may create files, and what it creates travels with the package, so an axis
that needs a fixture or a config file is a normal thing to do rather than
something to work around. The package becomes one line of JSON, so COPY sources
together and files under `tests/` each stay under 1 MiB, and a binary under
`tests/` is refused.

### The container is its tool

```
./sandbox up             build the image and boot a container (minutes), at the
                         size training gives this task
./sandbox exec 'CMD'     run CMD inside it as root; --timeout N (default 120 s)
./sandbox oracle         copy solution/ in, run solve.sh, grade it; prints what
                         the run cost (memory peak, cpu seconds, disk)
./sandbox grade          grade the current state as it is
./sandbox reset          a fresh container from the current Dockerfile
./sandbox check          the whole verdict, on a freshly booted container: grade
                         the untouched workspace (must fail), run the oracle
                         (must pass), compare the size against the seed
                         (hardening only; the easier direction has no size
                         gate); then audit the names the verifier depends on,
                         which is printed and recorded but does not decide the
                         verdict.
                         Prints VERDICT: pass|fail and the oracle's measured cost
./sandbox down           delete it
```

This is the task's own environment, built, sized and graded the way training
does it, so what runs out of memory or time here does so there too. `oracle` and
`grade` re-read `solution/` and `tests/` every time, so an edit is judged as soon
as it is saved; a Dockerfile edit takes effect on `reset`.

`./sandbox check` is the agent's mirror, not the judge: the caller re-runs the
same checks afterwards from files the agent cannot reach. Having the agent run
it is what stops it finishing on a rewrite it never executed. It also has a side
effect that matters downstream: the memory, cpu and disk the oracle run measures
are what size the box the caller's own revalidation opens.

### What it must declare

Editing the files is not the whole job. The evidence goes under `run/`:

- `run/hardening.md` (harder): the strategy observed, the trace evidence, the
  change proposed, and the new decision the change requires. Two candidate
  changes must be compared there first, each citing attempt filenames and
  concrete actions, and only one implemented
- `run/simplify.json` (easier): which simplification operator, what skill the
  task retains, what changed, what was restored, and the trace evidence
- `run/verdict.txt`: only when it stops without finishing, saying why

A missing or unreadable declaration fails the session rather than defaulting to
a first choice.

### Hardening

Pick one change from the student's actual attempts. Identify the successful
strategy and a judgment it currently bypasses: a decision the task ought to
require, which right now it does not. The two common shapes are the instruction
handing that step over, and the step having only one possible answer under the
present conditions, so the model never works it out. Adding a requirement means
changing the conditions so the decision has to be made. What does not count:
a change the old strategy plus a routine post-processing step would satisfy,
which adds work rather than a decision. Read the failures too, separating a
missing skill from unclear requirements and from infrastructure trouble.

Which files, in a fixed order under blind mode, because each is written against
the one before it:

1. `solution/solve.sh`, adapting the existing solution to the changed
   condition and keeping the parts that still apply
2. `instruction.md`. Everything the new requirement needs checked has to be
   discoverable from it and from the files the image ships, because that is all
   the verifier's author will see. A name the solution invents and the
   instruction never states will not be checked, so it goes in the instruction
   or the result is made checkable by value
3. `environment/Dockerfile`, the environment the other two assume. New
   fixtures, configs and data files are welcome, but every COPY source has to
   exist in the package: a line referring to a file nobody wrote is the
   commonest way a rewrite is thrown away, and it fails long after the session
   ended
4. `tests/` is left exactly as it is, because the verifier for this rung is
   written by the blind session

`./sandbox check` here grades the new solution with the *seed's* verifier: it
must still pass, since the seed's checks are the floor, and the untouched
workspace must still fail. The seed's verifier cannot see the new requirement;
the blind session will, from the instruction alone.

The method constraints:

- One rung, not a new task. Everything the seed asked for stays; what is added
  is one requirement the agent that solved it never had to meet
- The reference solution's growth is bounded, so "harder" cannot be met by
  making the solution longer
- A strategy not exercised in these attempts is not evidence the student cannot
  use it, and cannot be cited as difficulty
- For each strategy predicted to fail: a concrete input, the correct observable
  result, and what that strategy would produce. If those agree, the case does
  not support the prediction
- A change the old strategy plus a routine post-processing step would satisfy
  does not count, and the declaration must say why it would be insufficient

### Simplification

Read the failing trajectories, apply one simplification operator, and record
which one and what skill the task retains.

How much guidance may go into the instruction is a knob (`SWE_SIMPLIFY_HINT`,
default `vague`). At `specific` it bakes where-to-look hints into hundreds of
instructions, and a holdout experiment showed the policy learning hint-following
that does not transfer to unhinted tasks.

Operators have scopes: `add_scaffold` may change only the instruction and
`provide_initial_state` must preserve the verifier files. A change outside its
scope is rejected at `simplify_scope`.

Worth knowing what this direction is missing: the size rule does not apply to
it on either side. `_step_audit` returns empty when it cannot read
`run/seed_size.json`, and that file is deliberately deleted for an easier job;
the caller's own check is likewise `_direction != "easier"`. So what can
actually stop a simplification is the oracle, the null probe, and the operator
scopes above, and the oracle asks whether the reference solution still scores,
which editing an instruction cannot break. That is why `SWE_EVOLVE_SIMPLIFY` is
off by default, and what to settle before raising
`SWE_EVOLUTION_EASIER_RATIO`.

## Why the verifier is written blind

This is the least obvious piece of the design, and it comes from a failure.

A verifier written by the same session as the solution inherits the solution's
private vocabulary: the key names of the report it happens to emit, the label
its regex anchors on, the filename it chose for an artifact. None of that is in
the instruction. So an agent does every bit of the work, names one of them
differently, and scores zero.

Such a task reads as too hard in the data, a whole group failing, when it was
unfair. Of eight hardened tasks reviewed that a policy failed every attempt,
five failed on exactly this, three of them with all the work done.

Split across two sessions, the one writing the verifier cannot see the solution
and so cannot write that check; it has to recompute the expected result from the
container instead. The cost is a second session and one more container check per
rewrite: on the first paired round, six all-pass signals each way, the median
per-task time went from 430 s to 974 s, with the same six of six folded and the
hidden solution passing the blind verifier at first meeting on all six.

## Online and offline

The chain above is identical either way. What differs is who produces the
signals and who consumes the resulting mix.

Online: a run is training, the loop handles signals as they appear, and the
trainer hot-reloads each new mix version. This is the intent, and the shape
`TASK_EVOLUTION.md` describes.

Offline: take a batch of signals from a finished run, stage them into a root of
their own, drive rounds until every task has a verdict, and publish the result
as a named seed mix for the next training. This is what runs today. The
`exp-tmax-offline-20260913` root took the 139 TMax tasks whose first training
group in `tmax-9b--20260911-000700Z` was all-solved and outside the holdout, and
hardened all of them; the 2026-09-14 round accepted 136 of the 139, at $2,796 on
Claude Opus 5. `evolution/RUNBOOK.md` has the staging and publishing commands
under "Offline evolution of a signal subset".

## Where the code is

| file | what to read it for |
|---|---|
| [`rollouter.py`](rollouter.py) `_maybe_emit_evolution_signal` | the trainer's whole involvement: when a signal is written and what it carries |
| [`evolution/evolve_ondella.py`](evolution/evolve_ondella.py) | the loop: discover, choose, open the working directory, fold, write the ledger |
| [`evolution/feedback_loop.py`](evolution/feedback_loop.py) | one task end to end in `process_one`; the gates in `revalidate` |
| [`evolution/evolve_codex.py`](evolution/evolve_codex.py) | how the author session is started, how the blind-verifier split works, how sessions are recorded |
| [`evolution/agents/task_evolution.md`](evolution/agents/task_evolution.md) | the author agent's role and rules, as the prompt it reads |
| [`evolution/agents/verifier_author.md`](evolution/agents/verifier_author.md) | the same for the blind verifier |
| [`evolution/agent_sandbox.py`](evolution/agent_sandbox.py) | the container commands in the table above |
| [`evolution/simplify_operators.py`](evolution/simplify_operators.py) | the simplification operators and their hint levels |
| [`LAYOUT.md`](LAYOUT.md) | the exact format of every path and record named here |
| [`evolution/RUNBOOK.md`](evolution/RUNBOOK.md) | running the loop, restarting it, replaying one signal dry, and the offline chain |
| [`TASK_EVOLUTION.md`](TASK_EVOLUTION.md) | why the emit site sits in the rollouter |
