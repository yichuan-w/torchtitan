# Sizing a task's sandbox

Every rollout opens a sandbox, and opening one means naming three numbers up
front: **how many CPU cores, how many GiB of memory, how many GiB of disk.**
Too little and the task cannot finish. The kernel kills it, or the disk fills
and the sandbox platform cannot even create a session inside it. Too much and
the account holds fewer sandboxes at once, which is what caps rollout
throughput.

This doc says how those three numbers are arrived at, so that a number you find
on a task row can be traced to something that was measured. The measurements
live in the dataset (`metadata/measured_resources.csv` on
`andylizf/TerminalWorld-Seeds-Clean`), one row per task with the raw peaks
alongside the sizes derived from them.

> Scope: this is about sizing. For preparing the data see
> [`README_SEED_DATA.md`](./README_SEED_DATA.md); for the training recipe and
> run knobs see [`README.md`](./README.md).

---

## The rule

```
memory = ceil( max(agent_net, oracle_peak, peer) * 1.3 / 1024 )   at least 1 GiB
         if the reference solution did not reach reward 1 -> at least 2 GiB
disk   = ceil( max(agent_net, oracle_peak, peer) * 1.3 / 1024 )   at least 2 GiB
cpu    = max( ceil(agent_peak_cores), ceil(oracle_cpu_seconds / 900), 1 )
each capped at the platform maximum: 4 cores / 8 GiB / 10 GiB
```

`1.3` is headroom and multiplies measurements only. A floor already states a
requirement, and multiplying a number nobody measured only inflates it.

The rule is implemented once, in `derive_sizing.py`. Applying it to the mix,
exporting the published CSV, and re-verifying all read that script's output
rather than recomputing. Three scripts each doing their own arithmetic is three
sets of numbers that disagree.

### Where the three readings come from

| source | what it is |
|---|---|
| **agent_net** | the peak while a model solved the task with its own tooling, minus that tooling's fixed footprint (359.5 MB memory, 357 MB disk) |
| **oracle_peak** | the peak while the task's own `solution/solve.sh` ran, measured with no tooling installed, so nothing is subtracted |
| **peer** | an independent measurement of the same task by another group, where one exists |

`peer` matters for one half of the corpus only. TerminalWorld tasks ship a
runnable reference solution, so they can be measured twice. The TMax half's gold
is a snapshot of the finished state rather than a script, so unpacking it
completes the task without spending anything, and those 400 tasks have the agent
measurement as their only source. `peer` moves 5 of them.

## Why three sources and not one

The first version sized from the agent measurement alone. It broke 16 of 663
tasks: 3 were killed by the kernel, and 4 could not create a sandbox session at
all.

**The reason is that the agent picks its own route.** An expensive step it
happens to skip, `conda create` or `blkar decode`, never enters its peak, while
the reference solution runs that step every time. Adding the reference solution
as a second workload showed the gap is not a tail effect: **141 of 663 tasks
(22%) peak higher under the reference solution, by a median of 2.0x, a 95th
percentile of 10.7x, and a maximum of 24.1x.**

Neither reading describes the policy being trained, which is a third thing. Two
independent workloads is what is available, and one is measurably not enough.

## The CPU bound

Peak core count answers "how many cores at once" and cannot answer "how much
work in total". The second question has an arithmetic answer:

> A solution that burns C CPU-seconds cannot finish inside a B-second budget on
> fewer than C/B cores.

`tw_418406` burns 2785 CPU-seconds. At 2 cores it hit the 900-second deadline;
at 4 it finished in 829. **Its symptom was a timeout, which reads as a task that
is too hard, when the cause was too few cores.** That class of failure is
invisible without this bound.

`B` is fixed at 900 in `derive_sizing.py` while each task declares its own
budget in `task.toml`, which raises the question of whether the fixed value
mis-sizes anything. Measured across the 663 tasks that have a CPU reading:
**no task needs more cores under its own budget, and three need fewer**
(`tw_418406` 4 -> 1, `tw_177860` 2 -> 1, `tw_504009` 2 -> 1). The fixed value
errs only toward over-provisioning, on three tasks.

## The two floors

**Disk, 2 GiB.** Empirical, not chosen. Four tasks whose entire occupancy is
around 600 MB still fail to create a session inside a 1 GiB sandbox. The box
needs room beyond what `du` reports, and no measurement-derived number reaches
it.

**Memory, 2 GiB when the reference solution did not pass.** Such a task rests on
the agent measurement alone, and that is exactly the reading that under-read 16
tasks into failure. Writing it a size below the fleet default would lower a
number nothing can check: all of the downside, none of the upside.

## What is deliberately not a floor

The task's own `req_memory_mb` and `req_cpus`. Across 663 tasks `req_memory_mb`
takes four values (2048 on 388, 4096 on 115, 1024 on 82, absent on 67). **It is
a template field, not a statement about the task.**

Using it as a floor raises mean memory per sandbox from 1.10 to 2.18 GiB, which
halves how many sandboxes the account can hold at once, in exchange for a number
nobody measured. The measurements already cover every failure observed: all
sixteen tasks the agent-only rule broke come out at 2 GiB or more without it.
`derive_sizing.py --decl-floor` turns it on if a policy ever turns out to need
more than either the reference solution or the measuring agent did.

---

## How the measurement runs

**Three independent attempts per task, each in a fresh container.**

**Every attempt boots at the platform maximum (4 cores / 8 GiB / 10 GiB).** This
is the part that is easy to get wrong. Measuring inside a 2 GiB box can only
ever discover that the task wants 2 GiB, and a truncated reading is
indistinguishable from a real one.

A sampler inside the container reads two cgroup v2 files once a second:

- `/sys/fs/cgroup/memory.peak` for the memory high-water mark
- `/sys/fs/cgroup/cpu.stat` `usage_usec` for cumulative CPU, differenced across
  samples for the rate

Disk is sampled too, with `du -sx /`. Reading it once at the end misses whatever
was written and deleted in between, and `io.stat`'s `wbytes` is not occupancy at
all: it only ever increases, so it cannot produce a peak.

## Measuring a peak does not prove a size works

`verify_provisioning.py` is the step that closes that gap. For each task it
boots a sandbox at exactly the derived numbers, uploads the task's own
`solution/solve.sh`, runs it, reads the cgroup counters, uploads the tests,
grades, and records the result.

Failure here is the useful output. `no space left on device` means the
recommendation is too small and the measurement missed something. Reward below 1
with no infrastructure error means the task itself is at fault, unless the
kernel killed it.

**A task gets its own declared deadline, floored at the run's `--timeout`.** A
single deadline for every task measures the deadline rather than the task: in
the 2026-09-02 re-verification, three of seven failures were this. `tw_17818`
declares 1800 seconds and its reference solution finishes in 954; `tw_418406`
declares 3600 and finishes in 1078. Both were killed at the tool's 900-second
default and recorded as task failures. Declarations across the corpus run
600 / 900 / 1200 / 1800 / 2400 / 2700 / 3600 / 5400 / 7200, so roughly 35% of
tasks declare more than 900.

Note that the declared timeout is calibrated as expert time times three, which
makes it right for the reference solution and wrong for the policy. Backfilling
it onto training rows once dropped 75-85% of rollouts from the launcher's 2400
seconds to the floor's 900 and starved a run; those rows carry no
`agent_timeout_sec` for that reason. The same number is correct here and
incorrect there because the two run different things.

### Result of the last full pass

625 tasks checked at their published sizes: **618 at reward 1, zero killed by
the kernel, zero out of disk.** Disk used a median of 9% of what was allotted
(maximum 79%), memory a median of 5% (maximum 100%). The seven failures were
three tool-deadline artifacts, one harness defect, and three task defects, none
of them a sizing error.

---

## Pitfalls that cost real time

**`memory.peak` includes reclaimable page cache, so it is an upper bound on the
requirement rather than the requirement.** 25 tasks peaked above their 1 GiB
allotment; 15 were really killed and 10 finished anyway, one of them peaking at
2265 MB inside 1 GiB. Sizing from it is deliberately conservative. Do not trim a
task because it "only used 1 GiB of its 2": that is not waste.

**Distinguish a kernel kill from a self-inflicted timeout before concluding
anything.** Both surface as exit 137 and a line reading `Killed`, and elapsed
time cannot separate them because it includes boot and grading. The test is
`/sys/fs/cgroup/memory.events` `oom_kill`, which increments only on a kernel
kill.

**A reading from a failed run is still a lower bound; do not discard it.** "This
run did not finish, so the reading may be low" disqualifies it from *certifying*
a size. It does not disqualify it from *raising* one, because that memory was
really used. `tw_419317` was lost to conflating those: its failing run peaked at
1716 MB, the reading was thrown out for not reaching full marks, the agent's
207 MB sized it at 1 GiB, and the kernel killed it on re-verification. Whether a
reading can certify and whether it can raise are two questions and need two
switches.

**A column where 99% of rows share one value is a floor eating the rule, not a
corpus where every task is alike.** Net memory had a median of 207 MB, so after
1.3x and rounding, 662 of 663 tasks landed on the 1 GiB floor. That column
carried no information and looked exactly like a column of real measurements.

**Every task needs an outer timeout, not just its exec.** Boot, file upload and
grading have no deadline of their own, and when a sandbox disappears underneath
one of them the task waits forever on a call that never returns. It presents as
slowness rather than as a hang: one pass stopped at 659/663 while the account
held no live sandboxes for that label at all.

## Two biases that stay

- The measurement describes **the measuring agent's** behaviour, not the trained
  policy's. The reference solution covers part of that gap and cannot close it,
  because the policy is neither.
- **The max over three attempts sits below the tail of 16 rollouts per task** in
  training.
