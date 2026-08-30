# yichuan's runbook: TerminalWorld 9B RL on one 8x B300 host

Maintained by yichuan for the dedicated 8x B300 host `mi-sky-b300`. Every number
below was measured there; where a value differs from the reference run the
reason is stated inline.

Companion to [`RUNBOOK.md`](RUNBOOK.md), which documents the della-tridao
reference run on 5 of 8 shared GPUs. That runbook states plainly that **"the
8-GPU layout is untested"**; this one covers the single-host 8-GPU box
`mi-sky-b300`, and every value below is either copied from the reference or
justified by a measurement taken here. Read `RUNBOOK.md` first for the harness
architecture, the environment lock, and the task-corpus build.

Status as of 2026-08-29: the loop is validated end to end on this host at
reduced scale (`runs/tw10b`: 2 optimizer steps, finite loss, weight sync). The
production shape below has NOT been run yet.

---

## 1. What is different here, and why

| | reference (della-tridao) | here | why |
|---|---|---|---|
| GPUs | 5 of 8 (shared box) | **8, all ours** | dedicated host |
| split | `SWE_DP_SHARD=2` + `SWE_GEN_DP=3` | `SWE_DP_SHARD=2` + `SWE_GEN_DP=6` | see section 2 |
| data | tw + swe-smith mix, 1909 rows | **TerminalWorld only, 667 rows** | deliberate: isolate one corpus first |
| evolution | on | **off** | first run establishes a baseline |
| `TT_DAYTONA_LABEL` | `new_titan_swe_r2e` | **unset (default)** | see section 5 |
| generator backend | `vllm_native` | `vllm_native` | forced, not chosen -- see section 4 |

Everything not named in this document is the reference value from
`runbook/rltrain.env`.

---

## 2. GPU split: 2 trainer + 6 generator

`SWE_DP_SHARD` + `SWE_GEN_DP` must equal the GPU count. This host runs
`2 + 6 = 8`, keeping the reference trainer width and spending every remaining
GPU on generation.

The measurement that argues the other way, recorded so the next person does not
have to re-take it: during `runs/tw10b` at reduced admission (256 concurrency,
16 active groups) the generators were nearly idle -- ~40 live sandboxes, ~22
in-flight vLLM requests, `Waiting: 0 reqs` on every engine, against 1536 decode
slots. The trainer, at that point, was the whole step. That measurement is at
**one eighth** of the production admission below, so it does not by itself
justify moving GPUs to the trainer; the honest statement is that the split is
unmeasured at production scale in either direction. Check it on the first run:
if `Waiting` stays at 0 and in-flight requests stay far below `6 x 256`, the
generators are oversized and the next run should try `4 + 4`.

---

## 3. The knobs that decide what the run IS

### Batch shape (reference values)

```
SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_GROUP_SIZE=16
SWE_TRAIN_STEPS=150
SWE_LR=3e-6
SWE_MAX_ACTIVE_GROUPS=160        # (max_offpolicy_steps + 1) * 32
SWE_INITIAL_ACTIVE_GROUPS=64     # cold start only; the buffer grows it
SWE_OFFPOLICY_STEPS=4            # code default; not set explicitly
```

`SWE_SELECTION_WINDOW_GROUPS` is **left unset**, which selects the default
take-any batcher order. The reference sets it to 64 (MSL-style sliding-prefix
selection); unset is the path with historical throughput behind it, and adding a
selection policy on top of a first production run is one variable too many.

### Zero-variance groups: keep them (`SWE_DROP_ZERO_STD=0`)

The reference `rltrain.env` says to set this to `1` when running without
evolution. **Do not.** That advice reads as though keeping the groups wastes the
forward pass, and in this code it does not:

```python
# examples/tmax/config_registry.py:638
skip_zero_advantage_samples=not drop_zero_std_reward_groups,
```

With the drop off, the batcher keeps the zero-std group in the batch but filters
its samples out of the forward pass -- a sample whose every trained token has
advantage 0 contributes exactly nothing to `-advantage * ratio`. So `0` buys
faster batch fill (no waiting on replacement groups) at no compute cost.

This matters here more than it did on the reference host. Measured on
`runs/tw10b` at `SWE_GROUP_SIZE=8`: **~70% of completed groups were
zero-variance** (mostly `full_solve` 8/8), so with the drop on, filling one step
needed roughly three times as many completed groups as it consumed.

Consequence to be aware of, not a defect: the loss denominator
(`num_global_valid_tokens`) is computed BEFORE the skip filter, so a batch that
is mostly zero-advantage produces a proportionally smaller gradient. That is the
open-instruct convention and it is what the reference run used with the same
`SWE_LR=3e-6`, so the two are comparable. Watch `grad_norm` on the first steps.

### Agent and context (reference values)

```
TMAX_AGENT=terminus
TMAX_TERMINUS_MAX_TURNS=120
TMAX_TURN_MAX_TOKENS=32768
TMAX_EXEC_TIMEOUT_SEC=120
SWE_MAX_CONTEXT_LEN=63488
SWE_TIME_BUDGET_SEC=2400
SWE_AGENT_TIMEOUT_FLOOR_SEC=900
SWE_WRONG_SUBMIT_PENALTY=0.3
SWE_SANDBOX_BOOT_ALLOWANCE_SEC=2700
```

One thing to watch that the reference could not have seen: with the renderer
fixed (section 6), prior-turn reasoning is retained, so prompts grow about 4x
faster per turn and the 63488-token budget is reached sooner. Measured here,
average turns per rollout dropped from 23.1 to 12.2 after the fix. If
`finish_reason` shows episodes ending on context rather than on `submit`,
lowering `TMAX_TURN_MAX_TOKENS` to 16384 buys turns back.

---

## 4. Environment walls specific to this host

All four are forced, not preferences. The full derivation is in
[`handoff/2026-08-29-b300-single-host-bringup.md`](../handoff/2026-08-29-b300-single-host-bringup.md).

```
SWE_GEN_BACKEND=vllm_native        # torchtitan_wrapper cannot run head_dim=256 on SM100
SWE_GEN_VLLM_ATTENTION=FLASH_ATTN  # vLLM picks FlashInfer, which JITs; no usable nvcc here
VLLM_USE_FLASHINFER_SAMPLER=0      # same JIT wall, at the first sampled token
VLLM_ALLREDUCE_USE_FLASHINFER=0    # generator.py's import stub aborts engine init otherwise
```

Anything that JIT-compiles must also be kept off the root filesystem, which has
~17 GiB free:

```
FLASHINFER_CACHE_DIR=/ssd2/k3/yichuan/rl/.flashinfer-cache
TRITON_CACHE_DIR=/ssd2/k3/yichuan/rl/.triton-cache
HF_HOME=/ssd2/k3/yichuan/hf
```

Boot sanity check: `Available KV cache memory: 205.04 GiB` (reference: 204.61).
A materially different number means the generator memory math changed.

---

## 5. Daytona: a SHARED account

The account also carries the reference run. Measured 2026-08-29: **1024
sandboxes** labelled `new_titan_swe_r2e`, which are not ours.

Two consequences:

1. **Never set `TT_DAYTONA_LABEL`.** Leave it at the default `titan_swe_r2e`.
   `daytona_cleanup.py` selects by owner label; pointing ours at their label
   makes the cleanup script delete their live run.
2. **Budget against their usage, and budget on DISK.** The per-sandbox
   figures that matter are not the `TT_DAYTONA_*` fallbacks: each data row
   declares its own, and the env value applies only where a row is silent.
   Measured over the 667-row TerminalWorld mix, the data-weighted averages are
   **1.16 vCPU, 2.61 GiB memory, 10.0 GiB disk** -- 474 rows (71%) declare
   `1 vCPU / 2 GiB`, well under the `TT_DAYTONA_CPU=2` / `MEM_GB=4` fallbacks.

   Storage is the tightest of the three axes, because every row in this corpus
   declares 10 GiB and `TT_DAYTONA_DISK_GB` cannot lower it. At full admission
   alongside their 1024 sandboxes, against the account limits (20000 vCPU,
   80000 GiB memory, 80000 GiB storage, recorded 2026-08-29):

   | | ours @ 1408 | + theirs | limit | used |
   |---|---|---|---|---|
   | vCPU | 1633 | 2048 | 20000 | 18% |
   | memory GiB | 3675 | 4096 | 80000 | 10% |
   | disk GiB | 14080 | 10240 | 80000 | 30% |

   **Daytona capacity does not bound this run.** The storage ceiling alongside
   their 1024 sandboxes is `(80000 - 10240) / 10 = 6976` concurrent sandboxes,
   far above anything the generator side can serve. What bounds
   `SWE_ROLLOUT_CONCURRENCY` is the LLM: 6 engines x `SWE_MAX_NUM_SEQS=256` =
   1536 decode slots. We run **1408** rather than 1536 only because it keeps the
   admission arithmetic coherent -- 1408 / 16 = 88 groups in flight = 2.75 steps
   of run-ahead, under the `max_offpolicy_steps=4` cap -- and the reference
   run's own note is that oversubscribing admission past what the engines serve
   buys queueing, not throughput. Their count moves; re-measure before blaming
   the code:

```bash
source /ssd1/k3/yichuan/rltrain.secrets.env
python - <<'PY'
import asyncio, collections
from daytona import AsyncDaytona
async def main():
    async with AsyncDaytona() as d:
        c = collections.Counter()
        async for s in d.list():
            c[(getattr(s, "labels", None) or {}).get("owner", "-")] += 1
        print(dict(c), "total:", sum(c.values()))
asyncio.run(main())
PY
```

Sandbox settings:

```
TT_DAYTONA_EPHEMERAL=1             # this region rejects every non-ephemeral create
TT_DAYTONA_CREATE_CONCURRENCY=32
TT_DAYTONA_CREATE_RETRIES=8
TT_DAYTONA_CPU=2
TT_DAYTONA_MEM_GB=4
TT_DAYTONA_MAX_MEM_GB=8
TT_DAYTONA_DISK_GB=10
TT_DAYTONA_HEARTBEAT_SEC=180
TT_DAYTONA_AUTO_DELETE_MIN=15
```

Always clean up after a run -- edit `CUTOFF` in
`/ssd2/k3/yichuan/rl/daytona_cleanup.py` to the run's start time (UTC) first,
and never widen the owner filter.

---

## 6. Two fixes this host found, and what they are worth

Both are on the canonical branch; a checkout without them will reproduce the
symptoms.

**Renderer knob silently dropped.** `RendererConfig.build` filtered forwarded
knobs by name, so the recipe's `preserve_all_thinking=True` never reached the
renderer (startup logged `with args {}`). Retention stayed at `tool_cycle`,
terminus feeds terminal screens back as user-role messages, and every turn
therefore looked like a new user query: `bridge_to_next_turn` returned `None` on
100% of turns and each turn became its own full-prefix training sample.
Recipes now set `thinking_retention="all"` directly.

Measured, same recipe before and after:

| | before | after |
|---|---|---|
| step 1 | 87 microbatches, 19 min | **7 microbatches, 93 s** |
| step 2 | 211 microbatches, ~44 min | **15 microbatches, 172 s** |
| valid tokens (step 1) | 380795 | 361404 |
| fraction of packed tokens carrying gradient | 3.3% | **39.4%** |
| `mid-trajectory re-render` | 15062 (100% of turns) | **0** |

**Dockerfile comments inside a continuation.** The harness flattens
backslash-continuations before the Daytona SDK sees a task's Dockerfile, but
Docker strips whole-line comments FIRST. A comment between two continued lines
ended the join early and the build failed server-side with
`unknown instruction: <first word>` after the create retries were spent -- and
the rollout landed in its group as a reward-0 infra failure. 6 of the 667
TerminalWorld rows carry one. Fixed in `2d525700`.

**Regression checks for both, on any new run:**

```bash
grep -c "mid-trajectory re-render" train.log     # expect 0
grep -c "unknown instruction"      train.log     # expect 0
```

---

## 7. Trainer speed knobs

Added upstream on 2026-08-29 after span profiling; unset in the runs recorded
above, so their effect has not been measured on this host.

```
SWE_LMHEAD_TF32=1     # TF32 for the fp32 lm_head matmuls; ~9.5s of a 24s microbatch upstream
SWE_AC=selective      # FullAC was sized for 80GB cards; a B300 rank used <60 of 288 GB
SWE_LOSS_CHUNKS=8     # 32 -> 8: larger lm_head GEMMs, ~4 GiB more per chunk
```

`SWE_AC=selective` trades recompute for memory. Peak trainer memory measured
under FullAC on this host was 129 GiB of 268 GiB per rank at
`SWE_GROUP_SIZE=8`; the production batch is larger, so watch the first step for
OOM before walking away.

---

## 8. Checkpoints

One 9B checkpoint is ~98 GiB. `SWE_CKPT_INTERVAL=5` with `SWE_CKPT_KEEP=8` is
~0.8 TiB; `/ssd2` had 5.2 TiB free.

`keep_latest_k=1` is **rejected** by config validation ("We need to maintain at
least 2 checkpoint replicas"), and the failure is a clean exit 2 about 20 s into
boot. `SWE_CKPT_KEEP` is read by the recipe, so do not also pass
`--trainer.checkpoint.keep_latest_k` on the command line.

---

## 9. Running it

Long runs go through `systemd --user`, never a shell started from an agent
session: a session teardown SIGTERMs anything started from a Bash tool,
`setsid nohup` included, and the resulting death leaves an empty log.

```bash
# per-run overrides; systemd exports these before launch_9b.sh, so they win
# over rltrain.env
vim /ssd2/k3/yichuan/rl/service.env
systemctl --user restart rltrain.service
bash /ssd2/k3/yichuan/rl/status.sh
journalctl --user -u rltrain.service -f
```

`RUN_DIR` decides resume-vs-fresh: an existing checkpoint inside it resumes,
a fresh directory starts from base weights. The launcher prints which one it
chose -- confirm before walking away.

Milestones, measured on `runs/tw-prod-1` at the settings in section 10:

| milestone | log line | measured |
|---|---|---|
| engines up | `Available KV cache memory: 205.04 GiB` | ~3 min |
| loop entered | `[trainer_loop] step 1: begin` | ~3.5 min |
| batch ready | `step 1: got batch, 7 microbatch(es), 1237719 valid tokens` | 21 min (cold buffer) |
| trained | `forward_backward done, loss=-0.0019` | **74 s** |
| closed loop | `weights pulled (step done)` | -- |

The 21 minutes is a cold-buffer cost paid once: rollouts average 477 s, so the
first 32 groups cannot exist sooner. After that the buffer is warm and the step
is bounded by how fast 512 rollouts retire, not by the trainer.

---

## 9a. What this host actually measures

All from `runs/tw-prod-1` step 1 unless noted. Record these again after any
change to the split, the corpus or the concurrency -- they are the numbers every
tuning decision below rests on.

**Rollouts.** Measure these LATE, not from the first batch. The first 32 groups
to complete are by construction the fastest ones, and reading a mean off them
under-reports by 2x -- an early sample here said 477 s where the steady-state
value is 944 s.

| | early sample (biased) | steady state (n=300, later) |
|---|---|---|
| wall time per rollout | mean 477 s | **mean 944 s**, p50 846, p90 1908, max 2155 |
| turns per rollout | 10.8 | **21.3** |

Against `SWE_TIME_BUDGET_SEC=2400` nothing dies on the deadline, but p90 is
within 20% of it. `oom_suspect` was 0 of 1200. Solve rate 85% (1015/1200).

**Where a rollout's wall time goes.** The training loop records only the total,
so this comes from a standalone probe (`boot_agent_sandbox` against one real
task row) plus the duty-cycle measurement:

| phase | measured | share of a 21-turn rollout |
|---|---|---|
| model generation (11.8 tok/s per seq) | ~438 s | 35% |
| sandbox create + boot | 31 s | 2.5% |
| grading script | 12 s | 1% |
| exec round-trip to Daytona (0.39 s each) | ~25 s | 2% |
| **the agent's own commands running** | **~746 s** | **60%** |

**Infrastructure is 5.5% of a rollout.** harbor's terminus_2 does not poll: a
blocking command sends keys and then waits on one `tmux wait done` exec, and a
non-blocking one sleeps for a `min_timeout_sec` the MODEL chooses. So the 60% is
the task's own work plus whatever wait the policy asks for -- tuning
`TMAX_EXEC_TIMEOUT_SEC`, the sandbox backend or the network moves the 5.5%, not
the 60%.

**Batch** (32 groups x 16), step 1:

| | value |
|---|---|
| microbatches | 7 at step 1, then 19, 68, 54, 88, 44 -- steady range **44-88** |
| `padding_frac` | 0.083 |
| `policy_age` | 0 |
| global valid tokens | 1237719 |
| **zero-advantage tokens skipped** | **~32%** |
| zero-variance groups (`all_pass_group_frac`) | **0.688** |

Three quarters of the GROUPS carry no gradient but only a third of the TOKENS
do, because a task everyone solves ends in fewer turns. The loss denominator
counts the skipped tokens, so the effective gradient is ~68% of what the same
content would give under `SWE_DROP_ZERO_STD=1` -- mild, not the 25% a group-count
estimate suggests.

Every other outcome metric is clean: `finish_submit_frac` 1.00,
`finish_hit_context_limit_frac` 0.00, `finish_hit_time_budget_frac` 0.00,
`infra_failed_frac` 0.00, and `branches_per_rollout` 1.00 (the cleanest proof
the renderer fix holds -- one trajectory, one branch).

**Trainer speed knobs** (section 7), first measurement here: 7 microbatches took
93 s without them and **74 s with**; steady-state is ~5.7 s per microbatch.

**Checkpoint**: step 5 wrote **102 GB** in two DCP shards (one per FSDP rank) and
settled in ~280 s. The save is ASYNC -- step 6 began and took its batch while it
was still writing.

---

## 9c. `TMAX_EXEC_TRACE_DIR`: timing every sandbox command

Set it to a directory and every command the agent drives is appended to
`<group=N_rollout=M>.jsonl` as one record:

```json
{"t": 1788074975.512, "secs": 0.92, "exit": 0, "cmd": "tmux send-keys -t terminus-2 -- 'ruby /app/solution.rb\n'"}
```

Unset, it costs nothing (the writer returns before touching the filesystem).
Every sandbox command passes through the same `exec`, so the trace captures the
`tmux send-keys` that carries the agent's own command text, the `capture-pane`
that reads the screen back, and the `has-session` liveness probe. `t` is the
start, so the GAPS between consecutive records are the time spent OUTSIDE exec,
which is where a rollout actually goes.

**What the first 1392-rollout capture showed**, and it overturns the earlier
reading in 9a:

| | share of rollout wall time |
|---|---|
| model generating the next action | **~85%** |
| the wait the model itself asks for (`min_timeout_sec`) | ~9% |
| exec round-trips (56222 of them, 0.65 s each) | ~6% |
| **the commands themselves** | **~0** |

No command is slow. All 56222 execs sit at p50 0.41 s / p90 1.20 s / p99 1.95 s,
which is the network round-trip to Daytona; the 23 execs over 5 s (0.04%) are all
tmux's own three calls, not the agent's. terminus never takes harbor's blocking
path -- there is not a single `tmux wait done` in the capture -- so a command's
runtime is never charged to the exec at all.

The gap analysis separates the two kinds of waiting: a gap after `send-keys` is
the model's requested sleep (mean 3.0 s), and a gap after `capture-pane` /
`has-session` is the model deciding its next action (mean 13.5 s, p90 39.6 s,
max 567 s). The second is 91% of the non-exec time.

Per-command, the contrast is the point:

| command | times sent | wait it asks for | time the model spent DECIDING it |
|---|---|---|---|
| `cat` | 3012 | 0.1 s | **35.8 s** (p90 82.5) |
| `ls` | 2027 | 0.0 s | 24.7 s |
| `mkdir` | 328 | 0.3 s | **41.8 s** (p90 88.7) |
| `python3` | 1114 | 3.7 s | 37.0 s |
| `make` | 182 | 27.0 s | 26.0 s |
| `apt-get` | 235 | 16.9 s | 27.8 s |

The model spends 36 s composing a `cat` and 42 s composing a `mkdir`, both of
which cost nothing to run. Only `make` / `apt-get` / `wget` get a wait, and there
it is asking correctly. `cat`, `ls` and `which` are a third of everything sent --
the policy re-reading its environment.

**So sandbox-side tuning is not where the time is.** Zeroing every exec would buy
6%. The levers are `TMAX_TURN_MAX_TOKENS` (32768 today), per-sequence decode
speed (12-19 tok/s, and the engines are at 5-15% KV with `Waiting: 0`, so this is
not a capacity problem), and turn count -- which is what the training is meant to
improve.

Caveat on the capture above: it is 1392 rollouts from one partial step, skewed
toward fast finishers. The conclusion is structural rather than distributional,
but re-measure on a full run before quoting the percentages as final.

---

## 9b. Where the remaining headroom is

**Read the capacity metrics before theorising.** Two things here are easy to get
wrong from the config alone, and both were:

- `SWE_INITIAL_ACTIVE_GROUPS` is a COLD-START value, not a cap. The buffer calls
  `grow_effective_capacity()` once per trainable group taken, so
  `rollout_buffer/effective_active_group_capacity` climbs on its own -- measured
  102 -> 131 -> 174 -> 226 -> 248 within six steps, against
  `SWE_MAX_ACTIVE_GROUPS`, which tw-prod-1 had at 512. Raising the initial
  value only shortens the ramp.
- Admission read below the cap during that ramp (750-890 live sandboxes) and
  looked like a limit. It was not. Once the window passed it,
  **live sandboxes sat at exactly 1408** (1404 STARTED + 4 building) -- the
  `SWE_ROLLOUT_CONCURRENCY` value.

So concurrency is the one binding constraint, and everything else has slack:

| constraint | ceiling | in use |
|---|---|---|
| **`SWE_ROLLOUT_CONCURRENCY`** | **1408** | **1408 -- saturated** |
| active-group window | 512 groups = 8192 rollouts | 248 groups = 3968 |
| generator engines | see below | ~490 in flight of 1536 slots |
| Daytona | ~7000 sandboxes | 1408 |

**The engines have ~4x headroom, and the load imbalance proves it.** Routing
concentrated one engine at 4x the others, which accidentally ran the experiment:

| engine | batch | decode tok/s | per-seq | KV |
|---|---|---|---|---|
| 0 | 215 | 2542 | **11.81** | 47.2% |
| 1-5 | ~54 | ~640 | **11.67-11.96** | ~8% |

Per-sequence decode is FLAT from batch 54 to 215; aggregate throughput and KV
both scale linearly. The engines are nowhere near saturated -- 5 of 6 sit at 8%
KV of 205 GiB. Raising concurrency does not make a rollout faster (per-sequence
speed is unchanged), it raises how many retire per minute, which is exactly what
bounds the step.

That imbalance is itself an open bug: `IntraGeneratorRouter` routes new sessions
by `LeastLoadedRoutingStrategy` over `reserved_load`, and sessions are per-rollout
(`group=N/rollout=M`), so nothing in the design explains one engine holding 4x.
The router's `reserved_load` and vLLM's `Running` disagree; instrument both
before trusting either.

**Ranked:**

1. **Raise `SWE_ROLLOUT_CONCURRENCY`** -- 1408 was the only saturated
   constraint, with 4x engine headroom demonstrated above. **Settled for the
   next run: 2048, with `SWE_MAX_ACTIVE_GROUPS=160`.** 2048/16 = 128 groups in
   flight against a 160-group buffer, and 160 is `(max_offpolicy_steps + 1) *
   num_groups_per_train_step` -- so the buffer is coupled to the staleness the
   recipe tolerates instead of the 512 that let the window drift to 248 and
   stop meaning anything. Watch `policy_age` (0 at 1408) and per-sequence
   decode (11.8 tok/s).
2. **Stop paying for tasks the model has learned.** At an 85% solve rate, 69% of
   groups produce no gradient. Their tokens are skipped, but the ROLLOUTS are
   not: 512 sandboxes are booted and graded per step so ~130 can teach
   something. Fix with the evolution loop, or offline: run with
   `SWE_ZERO_STD_DIR` set, then feed that directory back as `SWE_SKIP_PROMPTS`.
3. **Fix the routing imbalance** -- 5 of 6 engines idle at 8% KV.
4. **The 60% of rollout wall time that is the agent's own commands.** Not an
   infrastructure problem; it needs a shorter path to the answer (fewer turns)
   or tasks with shorter commands. `SWE_ROLLOUT_DUMP_DIR` is the only way to see
   what is actually being run, including the `min_timeout_sec` the model asks
   for.
5. **GPU split.** The trainer is 5-8 min of a 12-15 min step now, so moving GPUs
   to it is no longer negligible -- but item 1 changes the balance first.

---

## 10. Full `service.env` for this run

```
PROFILE=full
RUN_DIR=/ssd2/k3/yichuan/rl/runs/<name>

# data: TerminalWorld only
SWE_PROMPT_DATA=/ssd2/k3/yichuan/rl/data/mix/tw_live.jsonl

# GPU split (must sum to 8)
SWE_DP_SHARD=2
SWE_GEN_DP=6

# batch shape
SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_GROUP_SIZE=16
SWE_TRAIN_STEPS=150
SWE_LR=3e-6
# 160 = (max_offpolicy_steps + 1) * num_groups_per_train_step, the
# staleness-coupled size. tw-prod-1 ran 512, which let the window grow to 248
# groups and stop mattering; 160 keeps the buffer coupled to the staleness the
# recipe actually tolerates.
SWE_MAX_ACTIVE_GROUPS=160
# Cold-start value only: the buffer grows this on its own, one slot per
# trainable group taken (measured 102 -> 248 within six steps). Raising it
# shortens the ramp and nothing else.
SWE_INITIAL_ACTIVE_GROUPS=64
SWE_DROP_ZERO_STD=0
# SWE_SELECTION_WINDOW_GROUPS deliberately unset -> take-any

# capacity
# 2048. tw-prod-1 ran 1408 and saturated it exactly (1404 STARTED + 4 building),
# while 5 of 6 engines sat at 8% KV and per-sequence decode stayed flat from batch
# 54 to 215 -- the engines have roughly 4x headroom. 2048/16 = 128 groups in
# flight, under the 160-group buffer above.
SWE_ROLLOUT_CONCURRENCY=2048
SWE_NUM_ROLLOUT_WORKERS=16
SWE_MAX_NUM_SEQS=256

# trainer speed
SWE_LMHEAD_TF32=1
SWE_AC=selective
SWE_LOSS_CHUNKS=8

# checkpoints
SWE_CKPT_INTERVAL=5
SWE_CKPT_KEEP=8

# no evolution, no inline validation
# SWE_TASK_EVOLUTION_DIR unset
SWE_DATA_HOT_RELOAD=0
SWE_VAL_SAMPLES=0
```

The remaining values -- agent, context, generator walls, Daytona, cache
redirects -- live in `/ssd2/k3/yichuan/rl/rltrain.env` and are listed in
sections 3, 4 and 5.

---

## 11. What is NOT established

- **The production shape has never been run on this host.** Two steps at
  `8 groups x 8` is the whole basis; `32 x 16` at 1408 concurrency is
  extrapolation.
- **The 2+6 split is unmeasured at production admission**, in either direction
  (section 2).
- **No validation set.** `SWE_VAL_SAMPLES=0` because no `tb2_eval.jsonl` has
  been built here, so there is no external yardstick -- only training reward,
  which `SWE_DROP_ZERO_STD=0` makes a poor signal.
- **The trainer speed knobs in section 7 are unmeasured here.**
- **No run has completed a checkpoint save on this host.** Both recorded runs
  were stopped before step 5.
- **`runs/tw10` reward numbers are not a valid baseline**: that run had the
  renderer bug, so the policy never saw its own prior reasoning.
