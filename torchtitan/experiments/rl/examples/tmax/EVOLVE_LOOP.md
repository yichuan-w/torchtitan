# The evolve loop

A training run and a task pool that move together: the trainer reports which
tasks stopped discriminating, and a loop beside it rewrites those tasks until
they discriminate again. This document is the design and the mechanism, with
the code entry points at the end. `LAYOUT.md` is the on-disk contract every
path here comes from, and `evolution/RUNBOOK.md` is how the loop is started,
restarted and watched.

## What a task is worth

A task in this pool is attempted k times per training step, and GRPO trains on
the spread between those attempts. A group where every attempt passes and a
group where every attempt fails both have zero advantage, so the k rollouts
spent on them move no gradient. The rollouts are the expensive part of the run:
each is a container, an agent, and an episode of terminal work up to the turn
and wall-clock budget.

So the property a task is kept for is not that it is hard. It is that the policy
being trained solves it sometimes. That property is defined against one specific
set of weights, and those weights change every step. A fixed pool therefore
decays in one direction: the tasks the policy outgrows become all-pass, the ones
it cannot reach stay all-fail, and the fraction of the pool still producing
gradient shrinks as training succeeds.

Dropping the dead prompts is the cheap handling, and the pool then only ever
shrinks. The loop is the other handling: move the prompt to where the policy
now is, and keep the pool the size it was.

## Two sides, deliberately asynchronous

Detecting a dead group is arithmetic over rewards already in memory. Acting on
it means rewriting a task's four files and proving the result still builds,
still grades its own reference solution, and still cannot be passed by doing
nothing: minutes of container work per task.

Those cannot share a thread. The trainer observes and writes a small JSON; the
loop reads those files and does the container work on the data side, at its own
pace, and publishes the result as a new version of the mix that the running
trainer picks up. Nothing in the rollout path waits for a rewrite, and a rewrite
that fails leaves the task exactly as it was.

```
trainer (rollouter.py)                    loop (evolution/evolve_ondella.py)
----------------------                    ---------------------------------
group with no spread
  -> runs/<run>/signals/<task>--g<N>.json  -> signals with no ledger line
     (attempts -> runs/<run>/rollouts/…)      harder -> one rewrite session
                                              easier -> one simplify session
                                              revalidate: oracle, null probe, size
                                              accepted -> r<N+1>/
  hot-reloads data/mix/live.jsonl <---------- publish data/mix/history/v<N>--<stamp>
  reads evolution/status.json -> W&B            and relink live.jsonl (replace by
                                                task id, pool size fixed)
```

## The signal

`TMaxRollouter._maybe_emit_evolution_signal` runs beside the zero-std detector,
which already has the group's rewards, the sample and the trajectories. It
writes one JSON per group that asks for a change, naming the task, the row's
revision, the run, the group, `solved` / `total`, and the paths of that group's
rollout records. The transcript is referenced, never copied; the loop hardlinks
those same files into the rewrite's package, so the agent that rewrites a task
reads the bytes the trainer wrote.

Under the sparse reward that trains this pool, both directions are thresholds on
the solved fraction: at or below `SWE_EVOLUTION_EASIER_RATIO` (default 0.0, so
nothing solved) asks for `easier`, at or above `SWE_EVOLUTION_HARDER_RATIO`
(default 1.0, so all-pass) asks for `harder`, and the easier ratio must stay
strictly below the harder one. A run can sit inside those defaults: this root's
runs set the harder ratio to 0.9, so a group one short of all-pass already asks. Under dense rewards the original zero-variance
rule stands, because a partial-credit mean is not a solve rate.

Counting solves rather than testing each reward against zero is what makes the
question independent of what a failure scored. The easier direction used to ask
whether every reward was exactly 0, which `SWE_WRONG_SUBMIT_PENALTY` would
break by scoring a graded-wrong submit negative: the group would then be neither
all-zero nor at the harder ratio, and would return without a signal in either
direction. No run has set that switch, so this was latent rather than observed.

An attempt the harness could not score is out of this decision before it
starts: `infra_failed` puts NaN on its reward and `is_scored` drops it, so a
group where every sibling failed that way has fewer than two scored rewards and
asks for nothing. Whatever reaches the threshold test was scored, and a scored
zero is a verdict on the task. There is no second test of whether the attempt
"really" ran, and in particular no test of the turn count: that asked the same
question `infra_failed` answers, and where the flag is unset the answer is that
the failure counts.

Emitting a signal changes nothing about the step in flight: the group keeps its
advantages, the batch keeps its size.

## One rewrite

The loop's round is: discover signals with no ledger line, choose one per task
(the newest one whose revision is the task's current one), handle them
concurrently, and fold each accepted rewrite the moment it is accepted rather
than at the end of the round. A round of many rewrites runs for hours, and
folding on acceptance means a loop stopped mid-round loses only what was still
in flight.

Handling one signal copies the input revision into a rewrite directory of its
own, hardlinks the group's rollout records under `traces/`, and gives that
directory to an agent (`evolution/evolve_codex.py`) with the task's own
container attached as a tool. The agent reads the instruction, the environment,
the reference solution, the verifier and the real attempts; it can run commands
in the container, run the reference solution, grade it, and rebuild the
container from scratch. Its own pass is not the gate: it is what stops the
agent from finishing on a rewrite it never executed.

### Harder

The default is student-guided. The agent reads the attempts that solved the task
and finds the step that was free: the guidance the instruction handed over, the
sub-problem the policy never had to work out. It then adds one requirement that
removes it. A fixed menu of 40 rewrite operators in 5 families,
transcribed from RST's Table 7, is still available behind
`EVOLVE_HARDER_OPERATORS=1`, scored for family balance and against repeats; it
is the comparison arm. Either way the change is one rung, and the reference
solution's growth is bounded so that "harder" cannot be met by making the
solution longer.

### Easier

The agent reads the failing trajectories and applies one simplification
operator, recording which one and what skill the task retains.
How much guidance it may write into the instruction is a knob
(`SWE_SIMPLIFY_HINT`, default `vague`): at the `specific` level it bakes
where-to-look hints into hundreds of instructions, and a holdout experiment
showed the policy learning hint-following that does not transfer to unhinted
tasks.

## The verifier is written blind

A verifier written next to the reference solution inherits the solution's
private vocabulary: the key names of the report it happens to emit, the label
its regex anchors on, the filename it chose for an artifact. An agent that does
every bit of the work and names one of those differently scores zero. That
task then reads as too hard, when it was unfair. Of eight hardened tasks
reviewed that a policy failed 16 times out of 16, five failed on exactly this,
three of them with all the work done.

So the verifier is written by a second session that is shown the instruction,
the environment and the seed's verifier, and not the solution. The two sessions
never see each other's file; the first time they meet is when the harness runs
the hidden solution against the blind verifier. A disagreement there means
either the verifier asks for something the instruction never promised or the
solution does not do what the instruction says, and the verifier's author gets
one bounded repair round to decide which. A second failure discards the rewrite
and the task goes back into training unchanged, which is the safe outcome for a
pair that cannot agree.

This costs a second session and one more container check per rewrite. On the
first paired round, six all-pass signals each way, the median per-task time
went from 430 s to 974 s, with the same six of six folded and the hidden
solution passing the blind verifier at first meeting on all six. The verifiers
it wrote recompute the expected result from the container instead of asserting
the solution's strings, which is what the split was for.

## What a rewrite has to survive

Claims about a rewritten task are settled by running it, not by reading it
(`evolution/feedback_loop.py::revalidate`). On the training host there is no
Docker, so the checks run on the same sandbox provider and grading contract as
the training rollouts, in the box the row will actually be provisioned at. A
task verified at the harness default and then starved in a 1-CPU row comes back
as a timeout, and reads as "too hard".

| check | rejects |
|---|---|
| oracle | the rewritten reference solution no longer earns a passing grade from the rewritten verifier |
| null probe | the verifier passes on an untouched workspace: nothing done, reward collected |
| step size | the reference solution grew outside its bound for this direction |
| dark paths / literals | the verifier demands a path or a name that the instruction and the environment never reveal |

The null probe replaced an LLM-guessed shortcut check. Over one week it rejected
148 rewrites; 29 of those were green before anything had been done, which the
probe catches for free, and 100 were the expected artifact `printf`'d into place
by a model that had read both the solution and the verifier: an answer no
policy could write, so those were tasks fit to train on, discarded at a model
call and a sandbox each. The hackability question moved into the agent's own
session, where it has the container and can try for itself.

The two dark-name audits ride along in the record as advice rather than as
gates. Measured over 464 rewrites, the literals audit flagged 130 names without
its baseline and none with it, and all 130 were false positives; the paths audit
rejected nothing across 621 signals. A gate with no measured precision does not
get to discard a session.

One fast path: when the only file the retune touched is the instruction, nothing
that affects the build, the verifier or the reference solution moved, so the
expensive rebuild is skipped. What an instruction edit can still introduce is
drift against the verifier, judged before and after, and that is what gets
checked.

A structural rewrite that fails its oracle gets one repair round with the
failure it never saw, the real exit code and output tail, fed back into the
session that wrote the files. About 60% of TerminalWorld hardening rewrites die
at this seam, where the instruction, the solution and the verifier have to
agree.

## Folding back

An accepted rewrite becomes `r<N+1>/` of that task and is rebuilt into a mix row
indistinguishable from a freshly prepared one, then published as a new version
under `data/mix/history/` with `live.jsonl` relinked to it. The trainer
hot-reloads it.

It replaces the task at its own row. Not a delete and append: appending rotates
the held-out slice at the end of the file, and a task no longer in the mix was
taken out deliberately and is not re-added. The pool size and the batch stay
fixed while the prompts move to the policy. This is why evolving pairs with
*not* dropping zero-std groups: dropping would shed exactly the prompts being
re-tuned.

Two things live on the row and not in the package, and would otherwise be lost
on a fold: the per-sandbox resources (a folded row falling back to the fleet
default gets 1 CPU against a measured 2, and starves) and the row's pin hook.
Both carry across, the resources at the maximum of what the seed declared and
what the reference solution measured.

## Defaults, and the one that is off

`SWE_EVOLVE_SIMPLIFY` defaults to 0: all-fail signals are ledgered as `deferred`
rather than acted on. The reason is that the ratchet only turns one way. A
simplification is accepted almost every time: revalidation asks whether the
reference solution still passes, and rewriting an instruction cannot break the
reference solution, while a hardening has to survive a rebuilt verifier.
Measured on this corpus: 693 accepted simplifications against 335 accepted
hardenings in one week, and 814 against 26 in an earlier window, with the solve
rate on the mix climbing while a fixed evaluation set stayed flat. That is a
pool getting softer, reported as progress. With the arm off, the too-hard tail
freezes instead, which is the failure that can be read off a chart. Deferred
signals are replayed if the arm is turned on.

Worker count is not a throughput knob. The loop is signal-starved: 89% of
rounds carry 8 signals or fewer, and more workers only drain the rare bursts
faster.

## What is recorded

Every signal the loop sees gets one line in `evolution/ledger.jsonl`: `handled`,
`deferred`, `reused`, `superseded` or `junk`. Every rewrite directory keeps the
revision it started from, the rollout records it read, each agent session's
prompt and both streams, and the revalidation verdict. `evolution/status.json`
is rebuilt from the ledger and the tasks' lineage at the end of every round, and
the trainer puts its counters on W&B beside the training curves, so pending
signals, accepted and rejected totals and the live mix version are readable on
the same time axis as the loss.

Nothing under a run directory is ever moved or deleted, and the ledger is the
loop's only memory: a signal with no ledger line is handled again, which is what
makes the loop resumable after a crash.

## Where the code is

| file | what to read it for |
|---|---|
| [`rollouter.py`](rollouter.py) `_maybe_emit_evolution_signal` | the trainer's whole involvement: when a signal is written, when it is suppressed |
| [`TASK_EVOLUTION.md`](TASK_EVOLUTION.md) | why the emit site sits in the rollouter, and the switches that turn the loop on |
| [`evolution/evolve_ondella.py`](evolution/evolve_ondella.py) | the loop: discover, choose, handle, fold, and the ledger |
| [`evolution/feedback_loop.py`](evolution/feedback_loop.py) | one signal end to end: `process_one`, and `revalidate` for the gates |
| [`evolution/evolve_codex.py`](evolution/evolve_codex.py) | the agentic rewrite, the blind verifier split, and the session plumbing |
| [`evolution/agents/`](evolution/agents/) | the roles the agents are given, as prompts: task author, verifier author, probe checker |
| [`evolution/synth_operators.py`](evolution/synth_operators.py) | the 40-operator taxonomy and its selection formula |
| [`evolution/simplify_operators.py`](evolution/simplify_operators.py) | the simplification operators and their hint levels |
| [`evolution/agent_sandbox.py`](evolution/agent_sandbox.py) | the container the rewriting agent drives: up, run, grade, rebuild |
| [`LAYOUT.md`](LAYOUT.md) | every path and every record format named above |
| [`evolution/RUNBOOK.md`](evolution/RUNBOOK.md) | running it: the unit, the environment, replaying a signal dry |
| [`evolution/synth_loop.py`](evolution/synth_loop.py) | the offline sibling that builds a pool from seeds, before any policy exists |
