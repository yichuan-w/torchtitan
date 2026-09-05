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
SWE_SANDBOX_BOOT_ALLOWANCE_SEC=2700
```

`SWE_WRONG_SUBMIT_PENALTY` is **left unset** (code default 0, no penalty).
Setting it to 0.3 subtracts that much when the agent submits and is wrong; it
shapes the reward, so it is one variable too many while throughput is the
question being measured.

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

3. **Row-level fixes go through a published version, never into the live file.**
   The generated mix declared `daytona_disk_gb: 1` on 864 of 1067 rows while the
   corpus documents 10 GiB, and rows provisioned at 1 GiB die at session
   bring-up. Fix the rows in the seed file before `new_root.py --mix` publishes
   it, or publish a corrected version through `layout.write_mix` (the loop's own
   folds use the same path); `data/mix/live.jsonl` is a hardlink to a history
   version and is never edited in place.
   Applied 2026-08-31 on the generated mix: 513 rows raised; 352 rows stayed at
   1 GiB and 83 declared nothing, because only 667 of the 1067 rows appear in
   the measured table at all. Those are the next ones to blow up.

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
SWE_AC=selective      # FullAC was sized for 80GB cards; a B300 rank used <60 of 288 GB
SWE_LOSS_CHUNKS=8     # 32 -> 8: larger lm_head GEMMs, ~4 GiB more per chunk
```

`SWE_LMHEAD_TF32` is **left unset** (code default 0): the fp32 lm_head matmuls
keep full fp32 inputs. Setting it to 1 rounds only the matmul inputs to a 10-bit
mantissa (fp32 accumulation and fp32 logit outputs are kept) and is worth ~9.5s
of a 24s microbatch upstream. Turn it on when trainer step time is the
constraint; the other two knobs above do not touch numerics.

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

## 9c. The rollout record's `exec` list: timing every sandbox command

Every command the agent drives is one entry in the `exec` list on line 1 of the
rollout's record, `runs/<run>/rollouts/<task>/g<group>-r<idx>.jsonl` (format in
[`../LAYOUT.md`](../LAYOUT.md)); `head -qn1 runs/<run>/rollouts/*/*.jsonl | jq -c '.exec[]'`
is the whole capture as one stream:

```json
{"t": 1788074975.512, "secs": 0.92, "exit": 0, "cmd": "tmux send-keys -t terminus-2 -- 'ruby /app/solution.rb\n'"}
```

`SWE_ROLLOUT_RECORDS=0` turns the record off.
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
   something. Fix with the evolution loop, or offline: build a skip list from a
   run's signals (`jq -r .task runs/<run>/signals/*.json | sort -u`) and feed it
   back as `SWE_SKIP_PROMPTS`.
3. **Fix the routing imbalance** -- 5 of 6 engines idle at 8% KV.
4. **The 60% of rollout wall time that is the agent's own commands.** Not an
   infrastructure problem; it needs a shorter path to the answer (fewer turns)
   or tasks with shorter commands. The rollout records under
   `runs/<run>/rollouts/` are the only way to see what is actually being run,
   including the `min_timeout_sec` the model asks for.
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

# trainer speed. SWE_LMHEAD_TF32 deliberately unset -> full fp32 lm_head inputs.
SWE_AC=selective
SWE_LOSS_CHUNKS=8

# checkpoints
SWE_CKPT_INTERVAL=5
SWE_CKPT_KEEP=8

# no evolution loop against this root, no inline validation
SWE_DATA_HOT_RELOAD=0
SWE_VAL_SAMPLES=0
```

The remaining values -- agent, context, generator walls, Daytona, cache
redirects -- live in `/ssd2/k3/yichuan/rl/rltrain.env` and are listed in
sections 3, 4 and 5.

---

## 10b. The 2026-09-02 run: per-run workdir, TerminalWorld only, evolution on

Section 10 is the no-evolution profile. This one runs the online task-evolution
loop beside the trainer, and every path it touches is per-run rather than shared.
Both changes came out of failures measured on this host over 2026-09-01/02; the
numbers below are from those measurements, not from a recipe.

### What made this run different

**A private root, because two loops on one host ate each other's work.**
`evolve_ondella.py` derives everything it reads and writes
([`../LAYOUT.md`](../LAYOUT.md)) from one env var, `TRL_BASE`. Left at its
default, every loop on the host shares all of it: on 2026-09-01 two were running
and handling signals out of the same root. Separately, other tooling rewrote the
shared `data/mix/live.jsonl` nine times during a single 13-hour run (`.bak-audit-*`, `.bak-overcap-*`,
`.bak-busybox-*`, `.bak-evolved-*`), and each rewrite tripped
`SWE_DATA_HOT_RELOAD` and invalidated the prefix cache -- the hit rate cycled
92% -> 43-58% every 15-20 minutes. Pointing `TRL_BASE` at a per-run directory
fixes both, and needs no code change.

Building one is three steps and about 5 MB:

```bash
W=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-<stamp>
mkdir -p $W/data/sources/tw-extract/tasks

# 1. packages: the published shard, filtered by the published id list
curl -sL "https://huggingface.co/datasets/andylizf/TerminalWorld-Seeds-Clean/resolve/main/data/tasks-00000.tar" -o t.tar
tar -xf t.tar -C /tmp/hf                      # 1353 complete packages
while read -r t; do cp -a /tmp/hf/tasks/$t $W/data/sources/tw-extract/tasks/$t; done < train_ready_ids.txt

# 2. pack to rows
TRL_BASE=$W python .../evolution/pack_to_dataset.py \
    --evolved $W/data/sources/tw-extract/tasks --out seed.jsonl

# 3. strip the canary from what the model reads; size from the measurements
#    (see below); then the rows become the root's version 1
python .../tmax/new_root.py --base $W --mix seed.jsonl \
    --bin /scratch/gpfs/TRIDAO/al9080/terminal-rl/bin --purpose "TW only, evolution on"
```

**TerminalWorld only, 663 rows.** `metadata/train_ready_ids.txt` is the
dataset's own list: the 766 tasks with a non-zero pass@5, minus 174 fragile
builds, 28 the solver's content filter refuses, and 5 that ask for more memory
than an 8 GiB sandbox allows. Take the list rather than deriving one -- it moves.
The 2026-09-02 re-verification dropped three real defects (`tw_158378` ships a
reference solution that is partial by construction, `tw_364770` clones a git host
that no longer answers, `tw_262649` fills any disk it is given) and brought two
back after their Dockerfiles were repaired.

Do not build the packages from a local pool without diffing it first: of the 663
here, 659 differed from the published tar and 8 carried Dockerfiles the dataset
had since repaired.

**Two data fixes that are not optional.**

- *Canary.* All 1,353 `instruction.md` files carry the harbor canary, on purpose
  -- it is how a model trained on this corpus is detected. Strip it from `prompt`
  and `metadata.problem_statement`, which the model reads, and leave it in the
  build and grading files, which it never sees.
- *Resources.* Rows packed from the tar carry no `daytona_*` fields, and the
  fleet defaults do not apply once a row declares its own. Fill them from
  `metadata/measured_resources.csv` columns `provision_cpu`, `provision_mem_gb`,
  `provision_disk_gb` -- measurements with 1.3x headroom, not the `est_disk_mb`
  estimate. 663/663 are covered. Under-provisioned disk is what
  `session_disk_exhausted` measures: it is deterministic, `max_attempts` never
  helps, and the whole group of 16 is lost.

### Concurrency: what the numbers have to satisfy

Three knobs are coupled, and setting one alone stops the run at startup.

```
config_registry.py:567   SWE_MAX_ACTIVE_GROUPS >= sum over workers of
                         (worker_concurrency // SWE_GROUP_SIZE + 1)
controller.py:376        1 <= SWE_INITIAL_ACTIVE_GROUPS <= SWE_MAX_ACTIVE_GROUPS
```

`SWE_ROLLOUT_CONCURRENCY` is split across `SWE_NUM_ROLLOUT_WORKERS` and then
again capped at `SWE_MAX_ACTIVE_GROUPS` workers, so lowering the group cap
without lowering the concurrency *raises* the lower bound it has to clear.
Dropping `SWE_MAX_ACTIVE_GROUPS` 160 -> 48 while leaving concurrency at 1508
demanded 96 and the launch died in 2 minutes with a `ValueError`. Check a
candidate offline before spending a cold start on it:

```python
from ...config_registry import _split_rollout_concurrency as split
wc = split(CONC, NUM_WORKERS, max_num_workers=MAX_GROUPS)
assert sum(c // GROUP_SIZE + 1 for c in wc) <= MAX_GROUPS
```

The reason to lower it at all: the engines hold `generators x SWE_MAX_NUM_SEQS`
sequences (here 3 x 256 = 768). At concurrency 1508 the mean was fine -- Running
sat at 65-230 for hours -- but the run collapsed periodically, not once. Sampled
in 15-minute buckets from the first completed group:

```
minutes after first group   Running  Waiting  prefix hit
       0-15                    178       0      91.8%
      15-30                    165       0      95.4%
      30-45                    248      52      63.4%
      45-60                    195      92       4.4%
      60-75                    254     197       7.7%
      75-90                    251     136       9.5%
      90-105                   124       0      95.5%
```

Running pins at the 256 per-engine cap, the queue backs up, and a group's 16
siblings stop being admitted together -- which is where the prefix sharing comes
from, so the hit rate follows the queue down. KV was never the constraint (60%
used at the worst point). Rollout median time tripled, 266s -> 1374s, while the
turn count stayed flat at 13-17: the tasks did not get harder, every turn queued.

Sizing rule: keep `SWE_ROLLOUT_CONCURRENCY` near the engines' total sequence
slots, then raise `SWE_MAX_ACTIVE_GROUPS` until the assertion above passes.

### Evolution: the arm matters more than the corpus

`SWE_RETUNE_AGENT` defaults to `chat`, which reaches the k/k (harder) direction
without the trajectory: `ev.evolve` and `llm.synthesize` have no `trajectory`
parameter, so the model sees the instruction, Dockerfile and reference solution
but never how the agent solved it. Only the `codex` arm passes
`trajectory=format_trace(attempts)`. The 0/k (easier) direction always gets the
trace, on both arms.

Measured on this host on 2026-09-01/02, same corpus, same day:

```
                    k/k attempts   accepted
chat    TerminalWorld     145         47%
chat    tmax               85          2%
codex   TerminalWorld     113         63%
codex   tmax               62         60%
```

The corpus gap is an artefact of the arm. Under `chat`, 51 of tmax's 85 died at
the shortcut probe -- the rewrite could be passed by printing the expected answer
into the artifact its verifier compares -- and under `codex` that share falls to
22 of 62. Do not conclude a corpus cannot be evolved from a `chat`-arm run.

`SWE_EVOLVE_SIMPLIFY` defaults to on. The ratchet it drives turns one way: a
simplify only has to leave `solve.sh` passing, while an evolve has to survive a
rebuilt verifier and a cheat probe. Watch `rollout_reward/_mean` against the
fixed holdout -- the on-mix rate climbing while the fixed eval stays flat is the
signature of a mix getting easier rather than a policy getting better. Turning
the branch off does not discard the backlog: 0/k signals get a `deferred` line
in `evolution/ledger.jsonl` and replay when it is turned back on.

### Placing the meshes

`RL_GPUS` is positional -- trainer takes the first `SWE_DP_SHARD` entries, then
one generator per entry. Generators hold ~235 GiB of KV each and the trainer
~170, so give the generators the roomiest cards. Free memory on this host on
2026-09-02, with two other tenants resident:

```
GPU     6      1      4      2      0  |     3      7      5
free  268796 268796 266993 262376 260919 | 249262 249056 239737   MiB
```

Trainer on 2,0 and generators on 6,1,4 keeps every mesh off the three cards
holding another user's ~19.6 GiB blocks. DCGM at the same moment put SMACT at
0.001-0.072 on all eight: the co-tenants were memory-resident and idle, so this
is a memory decision and not a contention one. Re-measure before each launch --
tenancy moved twice in one evening.

### The env this run used

Only the lines that differ from section 10 or that the evolution loop needs. The
rest is unchanged.

```
# per-run root: runs/, evolution/ and data/mix/ all hang off this (LAYOUT.md)
TRL_BASE=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-20260902-tw

# TerminalWorld only, 663 rows, canary stripped, sized from measured_resources;
# the signals go under each run, nothing to point anywhere
SWE_PROMPT_DATA=$TRL_BASE/data/mix/live.jsonl
SWE_DATA_HOT_RELOAD=1

# placement: trainer 2,0 | generators 6,1,4
RL_GPUS=2,0,6,1,4
SWE_DP_SHARD=2
SWE_GEN_DP=3

# batch shape
SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_GROUP_SIZE=16
SWE_DROP_ZERO_STD=0

# concurrency -- these three move together, see above
SWE_ROLLOUT_CONCURRENCY=1024
SWE_NUM_ROLLOUT_WORKERS=16
SWE_MAX_ACTIVE_GROUPS=96
SWE_INITIAL_ACTIVE_GROUPS=32
SWE_MAX_NUM_SEQS=256
SWE_GPU_MEM_LIMIT=0.85

# agent
TMAX_AGENT=terminus
TMAX_TERMINUS_MAX_TURNS=120
TMAX_TURN_MAX_TOKENS=32768
TMAX_EXEC_TIMEOUT_SEC=120
SWE_MAX_CONTEXT_LEN=63488
SWE_AGENT_TIMEOUT_FLOOR_SEC=900

# rollout budget: timed-out rollouts ran a median 2446s and p90 2577s against
# the old 2400s cap, i.e. cut off rather than stuck (104 execs against 54 for a
# normal rollout, 23.5s vs 17.9s per turn -- turn count dominates, not inference)
SWE_TIME_BUDGET_SEC=3600

# trainer
SWE_AC=full
SWE_LMHEAD_TF32X3=1
SWE_LOSS_CHUNKS=8
SWE_LR=3e-6
SWE_PROFILE_MICROBATCHES=0
```

The evolution loop runs as its own process against the same root, so restarting
it costs no rollouts:

```bash
TRL_BASE=$W TRL_TT=... PYTHONPATH=... \
  python .../evolution/evolve_ondella.py --interval 120 --workers 16   # logs to $W/evolution/loop.log
```

### Not established

Whether concurrency 1024 holds. The collapse above first appears 30-45 minutes
after the first completed group, and this run had not reached that window when
these numbers were written. Zero queue depth during a cold start proves nothing:
the previous run looked identical for its first 30 minutes.

---

## 10c. Run tag `wd-20260902b`, launched 2026-09-02 04:18

The profile 10b describes, with four things changed after a night of measuring on
this host. Every value below is read back from the live process, not from the file
it was written in.

### What moved from 10b, and what measured it

**Both TF32 paths off (`SWE_LMHEAD_TF32=0`, `SWE_LMHEAD_TF32X3=0`).**
Start here, because an earlier note in this file was wrong in a way worth
recording. tmax does *not* lack `LMHeadCastConverter`: it inherits its model_spec
from `rl_grpo_qwen3_5_9b_swe_r2e`, which builds it through
`_qwen3_5_rl_model_registry(..., converters=[LMHeadCastConverter.Config()])`. The
lm_head has always been a `CastLinear`. Grepping only `tmax/config_registry.py`
missed the inheritance.

What is true is that `SWE_LMHEAD_TF32X3=1` does not cover backward.
`_linear_tf32x3` sets `float32_matmul_precision("high")` inside
`CastLinear.forward` and restores it in a `finally`, so autograd runs the two
backward matmuls outside that context at the default precision. The 2026-08-31
trace shows exactly that shape: 48 `tensorop_tf32gemm` launches (3 matmuls x 8
loss chunks x 2 profiled microbatches -- the 3xTF32 forward) alongside 32
`simt_sgemm` launches (one forward and one backward per chunk-instance, IEEE
fp32 on CUDA cores). Backward is the larger half: 4,400 ms against 3,923 ms.

`SWE_LMHEAD_TF32=1` (`trainer.py:234`) is the switch that does cover backward --
it sets `torch.backends.cuda.matmul.fp32_precision = "tf32"` process-wide for the
trainer. It costs accuracy: measured on this host at the 9B lm_head shape, plain
TF32 has a median relative error of 2.9e-4 against 9.6e-6 for the 3xTF32 split.
Those logits feed logprob and the DPPO importance ratio `exp(new - old)`, and
`old` comes from the generator, a separate process this trainer-scoped switch
does not touch -- so the error lands in trainer/generator divergence, which is
what `CastLinear` exists to prevent. Both are off here to get a clean fp32
baseline; the cost is roughly 12 s of a 42 s microbatch. Turn one back on only
while watching `bit_wise/*` and `loss/ratio_mean`.

**`SWE_MAX_ACTIVE_GROUPS` 96 -> 160, `SWE_INITIAL_ACTIVE_GROUPS` 32 -> 64.**
An active slot is charged when a group starts and freed only when the trainer
*trains* it, so completed-but-unbatched groups hold slots too. Measured at 96 on
2026-09-02 03:58: 159 groups issued, 64 released by two train steps, so 95 of 96
slots taken -- of which **81 were finished groups queued for training and only 14
were live rollouts**. The engines sat at 6% KV with `Waiting: 0`. Starving the
rollouts of slots looks nothing like an engine problem, and the fix is not in the
engine: size this knob for the training queue depth, not for the rollouts.

**`SWE_ROLLOUT_CONCURRENCY` 1024 -> 1508, `SWE_MAX_NUM_SEQS` 256 -> 512.**
Neither was binding at 96 groups. 1508 over 16 workers needs 96 groups by the
`config_registry.py:567` constraint, comfortably under 160. 256 was where the
previous run pinned before its prefix hit rate collapsed; KV is not the limit
here, so raise the ceiling rather than let a queue form against it.

**Evolution on the codex arm, simplify off.**
`SWE_RETUNE_AGENT=codex` because the default `chat` arm reaches the k/k (harder)
direction without the trajectory -- `ev.evolve` and `llm.synthesize` take no
`trajectory` argument. Same corpus, same day: chat gave TerminalWorld 47% and
tmax 2%; codex gave 63% and 60%. `SWE_EVOLVE_SIMPLIFY=0` because the ratchet only
turns one way and this run wants to measure the harder direction alone; 0/k
signals get a `deferred` line in `evolution/ledger.jsonl` and replay if it is turned back
on. The arm needs `$TRL_BASE/bin/codex` (258 MB), with `jq`
beside it for reading the rollout records, and falls back to the chat operator on any exception, silently -- check for
`arm=agent_harder` in the evolve log to confirm which one actually ran.

**Both records on.** `SWE_ROLLOUT_RECORDS=1` (the default) for the full decoded
trajectory per rollout under `runs/<run>/rollouts/`, `SWE_PROFILE_MICROBATCHES=2`
for a fresh trace under a known env.

### The env

```
TRL_BASE=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-20260902b
SWE_PROMPT_DATA=$TRL_BASE/data/mix/live.jsonl        # 663 TW rows
SWE_ROLLOUT_RECORDS=1                                # runs/<run>/rollouts/, the default
SWE_EVOLUTION_SIGNALS=1                              # runs/<run>/signals/, the default
SWE_DATA_HOT_RELOAD=1

RL_GPUS=2,0,6,1,4          # trainer 2,0 | generators 6,1,4
SWE_DP_SHARD=2
SWE_GEN_DP=3

SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_GROUP_SIZE=16
SWE_DROP_ZERO_STD=0

SWE_MAX_ACTIVE_GROUPS=160
SWE_INITIAL_ACTIVE_GROUPS=64
SWE_ROLLOUT_CONCURRENCY=1508
SWE_NUM_ROLLOUT_WORKERS=16
SWE_MAX_NUM_SEQS=512
SWE_GPU_MEM_LIMIT=0.85

SWE_LMHEAD_TF32=0
SWE_LMHEAD_TF32X3=0
SWE_AC=full
SWE_LOSS_CHUNKS=8
SWE_LR=3e-6
SWE_PROFILE_MICROBATCHES=2
SWE_PROFILE_SKIP=3

TMAX_AGENT=terminus
TMAX_TERMINUS_MAX_TURNS=120
TMAX_TURN_MAX_TOKENS=32768
TMAX_EXEC_TIMEOUT_SEC=120
SWE_MAX_CONTEXT_LEN=63488
SWE_AGENT_TIMEOUT_FLOOR_SEC=900
SWE_TIME_BUDGET_SEC=3600
```

Evolution runs as its own process against the same root:

```
TRL_BASE=$W SWE_RETUNE_AGENT=codex SWE_EVOLVE_SIMPLIFY=0 \
  python .../evolution/evolve_ondella.py --interval 120 --workers 16   # logs to $W/evolution/loop.log
```

### Building the mix, verified

663 rows from `metadata/train_ready_ids.txt`, packages from the published
`data/tasks-00000.tar` (LFS oid `8d0a11e0...`; note `shard_manifest.jsonl` still
carries the pre-repair sha256 and disagrees). Canary stripped from `prompt` and
`metadata.problem_statement`, left in the build and grading files. cpu, memory and
disk from `measured_resources.csv` `provision_*`. Checks that ran after the build:
canary remaining 0, `daytona_*` null on 0 rows, id set equal to
`train_ready_ids.txt`, prefixes 100% `tw_`.

### What to watch, and what it would mean

| signal | healthy | what a miss means |
|---|---|---|
| `Waiting` on the engines | 0 | slots or concurrency are binding again |
| prefix hit rate | >90% | a group's 16 siblings stopped being admitted together |
| in-flight groups vs 160 | headroom left | the training queue is eating the budget again |
| `arm=agent_harder` in evolve log | present | codex fell back to chat silently |
| `bit_wise/*`, `loss/ratio_mean` | stable | trainer/generator divergence (relevant if TF32 is turned on) |
| step interval | vs 16.6 min on the 09-01 run | the fp32 baseline costs ~12 s of a 42 s microbatch |

---

## 10d. Run tag `wd-20260904a`, launched 2026-09-04 04:07:32

A clean restart from the 663-row base corpus with the one-rung `step_size` gate
(PR #39) in place from step 0. Every value below is read back from
`/proc/<pid>/environ` of the live trainer, not from the file it was written in.

### Why it was restarted, and the one thing that had to change

The 2026-09-03 run and its evolve loop were both SIGKILLed at 03:29:35, within
three seconds of each other. There was no kernel OOM (`dmesg` carries no
oom-kill), no CUDA OOM in the log, and the watchdog's own log was empty: both
processes had been started with `nohup ... &` from a CLI session and went away
with its process group. `nohup` blocks SIGHUP, not a process-group SIGKILL.

So this run is launched by **systemd user units**, not by a shell:

```
~/.config/systemd/user/tmax-trainer.service   ExecStart=$W/bin/svc_train.sh
~/.config/systemd/user/tmax-evolve.service    ExecStart=$W/bin/svc_evolve.sh
Restart=always  RestartSec=60  StartLimitIntervalSec=0
```

`loginctl show-user` reports `Linger=yes`, so the units survive logout. Both are
`enable`d, so they come back after a reboot. Measured: 15 h uptime with
`NRestarts=0` on the trainer across two CLI sessions ending, where the previous
launch method lost the run at the first one.

`svc_train.sh` pins `RL_RESUME_DUMP` to whatever `launch_9b.sh` stamped the first
time. Without that pin a restart stamps a *new* dump directory, finds no `step-*`
under it, and silently begins again from the HF weights -- which is invisible in
the metrics. The launch line is the check: `[launch] resuming from .../step-N`
against `[launch] fresh start: initial weights from ...`.

### Placement: 2+3 on the five least-contended cards

Measured 2026-09-04 04:00 with every one of our own processes stopped, so the
numbers below are other people's:

| GPU | foreign MiB | SMACT (30 samples) | |
|---|---|---|---|
| 0 | 270867 | 0.0078 | two `serve_policy`, unusable |
| 1 | 1822 | 0.0002 | -> generator |
| 2 | 1766 | 0.0002 | -> generator |
| 3 | 21036 | 0.0010 | left to its tenants |
| 4 | 19555 | 0.0000 | left to its tenant |
| 5 | 3417 | 0.0000 | -> generator (two pollers) |
| 6 | 5 | 0.0000 | -> trainer |
| 7 | 7 | 0.0000 | -> trainer |

6 and 7 are the only two cards with nothing on them, so FSDP takes them: it is
synchronous, and a straggler shard costs the whole step. The three generators are
independent, so they take 1, 2 and 5. Card 5's two pollers report 92-100% in
nvidia-smi's `utilization.gpu` and 0.000 in DCGM SMACT -- duty cycle, not
occupancy; do not size against the former.

`SWE_DP_SHARD=2` rather than 3: at dp_shard=3 a step measured ~9.6 min against
~14.7 min to fill 32 groups, so the trainer idled a third of the time. At 2 the
step is 11-14 min, matched to generation, with a card moved to where the
throughput is.

### The env

```
TRL_PROFILE=yichuan
TRL_TT=/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/torchtitan          # 6d7277f9
TRL_BASE=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-20260904a
TRL_MODEL=/scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B
SWE_PROMPT_DATA=$TRL_BASE/data/mix/mix_live.jsonl                 # 663 TW rows, base
SWE_TASK_EVOLUTION_DIR=$TRL_BASE/evolution/signals
SWE_ROLLOUT_DUMP_DIR=$TRL_BASE/rollout-dumps
TMAX_EXEC_TRACE_DIR=$TRL_BASE/exec-traces
SWE_CKPT_FOLDER=/scratch/al9080/terminal-rl/ckpt/tmax-9b-20260904-040732

RL_GPUS=6,7,1,2,5            CUDA_VISIBLE_DEVICES=6,7,1,2,5
SWE_DP_SHARD=2               SWE_GEN_DP=3
SWE_AC=full
SWE_GEN_BACKEND=vllm_native  SWE_GEN_VLLM_DEFAULT_COMPILE=1
SWE_GEN_PREFIX_CACHE=1       SWE_GPU_MEM_LIMIT=0.85
SWE_MAX_NUM_SEQS=256         SWE_MAX_CONTEXT_LEN=63488
SWE_DISABLE_CUSTOM_ALL_REDUCE=1

SWE_GROUP_SIZE=16            SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_MAX_ACTIVE_GROUPS=160    SWE_INITIAL_ACTIVE_GROUPS=64
SWE_ROLLOUT_CONCURRENCY=1536 SWE_NUM_ROLLOUT_WORKERS=16
SWE_TIME_BUDGET_SEC=3600     SWE_AGENT_TIMEOUT_FLOOR_SEC=900
TMAX_AGENT=terminus          TMAX_TERMINUS_MAX_TURNS=120
TMAX_EXEC_TIMEOUT_SEC=120

SWE_LR=3e-6                  SWE_LOSS_CHUNKS=8
SWE_TRAIN_STEPS=150          SWE_DROP_ZERO_STD=0
SWE_CKPT_INTERVAL=3          SWE_CKPT_KEEP=3
SWE_DATA_HOT_RELOAD=1
SWE_VAL_SAMPLES=0            SWE_VAL_INTERVAL=20   SWE_NUM_EVAL_GENERATORS=0
SWE_PROFILE_MICROBATCHES=3   SWE_PROFILE_SKIP=5

SWE_LMHEAD_TF32 and SWE_LMHEAD_TF32X3 are both unset (= 0). See below.

TT_DAYTONA_CPU=1  TT_DAYTONA_MEM_GB=2  TT_DAYTONA_DISK_GB=2  TT_DAYTONA_MAX_MEM_GB=8
TT_DAYTONA_CREATE_CONCURRENCY=128  TT_DAYTONA_CREATE_RETRIES=8
TT_DAYTONA_EPHEMERAL=1  TT_DAYTONA_AUTO_DELETE_MIN=15  TT_DAYTONA_HEARTBEAT_SEC=180
TT_DAYTONA_LABEL=new_titan_swe_r2e
WANDB_PROJECT=terminal-agent-rl                       # run zjd05wdj
```

Evolution: `SWE_RETUNE_AGENT=codex`, `SWE_EVOLVE_SIMPLIFY=0`,
`evolve_ondella.py --interval 120 --workers 16`.

### The corpus is the base one, verified three ways

`mix_live.jsonl` was rebuilt rather than inherited, because the 2026-09-03 run's
copy carried 195 already-hardened rows. Checks against the untouched base:

```
663 rows                                     = base row count
0 rows differ in metadata.tmax               = no hardened verifier/fixtures
195 rows differ from wd-20260903d            = none of its hardening carried over
```

`metadata.tmax` is the only thing evolution rewrites, so 0 there is the check that
matters. Two rows the predecessor had hardened (`tw_100135`, `tw_197232`) were
reverted. A 605-row difference against an older base file is a leading newline in
`prompt`, not evolution.

### The profile, and the one lever that is left

`SWE_PROFILE_MICROBATCHES=3` wrote a trace at step 1. Read on 2026-09-04, at
12-13 s per microbatch (the uncontended rate), 3 microbatches, 34.33 s of kernels:

| | lm_head fp32 SIMT | model GEMM (TC) | elementwise | other | NCCL | reduce | copy | total |
|---|---|---|---|---|---|---|---|---|
| `rl_loss_fn` | **17.30s** | 0.00s | 0.20s | 0.01s | 0.06s | 0.24s | 0.01s | 17.82s |
| `rl_model_backward` | 0.00s | 5.32s | 4.29s | 1.46s | 0.86s | 0.40s | 0.12s | 12.45s |
| `rl_model_forward` | 0.00s | 1.65s | 1.73s | 0.29s | 0.14s | 0.11s | 0.11s | 4.02s |

The matrix is block-diagonal: `rl_loss_fn` is 97% one kernel, and that kernel
appears nowhere else. The GPU has kernels resident 99.4% of the window, so this is
not launch overhead, not the input pipeline, and not communication (NCCL is 3.2%).

`cutlass3x_sm100_simt_sgemm_f32_f32_f32_f32_f32`, 72 launches (8 loss chunks x
{forward, dgrad, wgrad} x 3 microbatches), ~240 ms each. SIMT = CUDA cores. Per
microbatch the lm_head is 2 x 65536 x 4096 x 248320 x 3 = 400 TFLOP in 5.94 s =
67 TFLOP/s, about 84% of the B300's non-tensor-core fp32 ceiling -- the kernel is
near the hardware limit; the hardware path is the cost. Of 3744 GEMM launches in
the step only these 72 are fp32; everything else is bf16 on tensor cores.

So an operator carrying ~11% of the step's FLOPs takes ~50% of its time.

**Re-measured 2026-09-04 on this host (`evolution/bench_lmhead_tf32.py --iters 5`,
[8192,4096] x [4096,248320], forward plus both backward matmuls):**

```
                      fwd+bwd        out      grad_x     grad_w
ieee_fp32            3673.6 ms   0.0e+00    0.0e+00    0.0e+00
plain_tf32            312.6 ms   7.3e-06    6.2e-04    2.1e-04
old_tf32x3_fwd_only  2547.8 ms   7.3e-06    0.0e+00    0.0e+00
new_tf32x3_fwd_bwd    535.9 ms   7.3e-06    1.3e-05    9.6e-06
```

This corrects the reasoning in 10c on one point. **The forward error is 7.3e-6 on
all three TF32 paths, plain TF32 included.** Both lm_head operands are upcast from
bf16 (the decoder-norm output, the FSDP-all-gathered weight), and bf16's 8-bit
significand is exactly representable in TF32's 11, so truncating them is a no-op;
the 7.3e-6 is accumulation order against the CUDA-core kernel, not lost precision.
The logits -- and therefore the DPPO importance ratio, whose `old` term comes from
the generator -- are affected identically by both paths. That argument does not
separate them.

What separates them is backward, where `grad_out` is genuine fp32: plain TF32
truncates it (6.2e-4 / 2.1e-4), the split path reconstructs it (1.3e-5 / 9.6e-6).

At the step level, with lm_head at 50.4%, Amdahl caps both: TF32x3 gives ~1.68x,
plain TF32 ~1.91x. Plain buys 14% more step time for 20-50x the gradient error and
does it through a process-wide `torch.backends.cuda.matmul.fp32_precision` that
would silently catch any fp32 matmul added later; `_LinearTF32` is scoped to
`CastLinear`. Neither is on in this run, which is the fp32 baseline. Turn on
TF32x3 first, and only while watching `bit_wise/logprob_diff/abs_mean` (1.6-1.8e-2
here), `debug/vllm_local_kl_k3_mean` (1.0-1.1e-3), `loss/ratio_mean` (1.00000x)
and `grad_norm`.

### What it measured, 15 h in

```
step 53, 2098 groups, epoch ~3.0, no restarts, 0 CUDA OOM, 0 SupervisionError
microbatch 12.0-12.8 s     step 11-14 min (50-65 microbatches)
group classes              full 28% / partial 61% / not_solve 11%
stale_dropped              313 of 2098 (15%), flat for hours once warm
prefix hit rate            92-97%
evolution                  542 folds, 535 hardened, 210 deferred, 7 agent_failed (1.0%)
```

Hardened tasks, redrawn after the fold (205 samples, group id greater than the
signal's trigger group):

```
partial_solve  84%      not_solve 16%      full_solve 0%
```

Against the 2026-09-03 run without the `step_size` gate, whose hardened tasks came
back `not_solve` 46% of the time. No hardened task has returned 16/16 -- across
310 tasks the pre-hardening draw was 16/16 in 71.1% of 655 draws, and 0.0% of 213
draws after.

### Contention is invisible in SMACT and in memory

Between roughly 11:49 and 16:40 the step rate doubled, 12.5 -> 25 s per microbatch,
and recovered on its own. Two tenants had arrived on the trainer's cards
(`zl3193`, 120 GiB on GPU6, 14:42-16:40; another on 6 and 7 before it). Neither
was visible in the two things being watched: DCGM SMACT on a shared card *includes
the other tenant's* work, so GPU6 read 0.78-0.89 throughout, and our own
allocator's cached segments hid the memory (both cards read 258 GiB whether the
121 GiB belonged to us or to someone else -- our real footprint is 137 GiB).

The only direct signal is the step rate itself. Sample the microbatch counter over
a fixed window and compare against the 12-13 s baseline; list the non-self PIDs on
the trainer's cards in the same breath. Five hours of half-speed training cost
about 2.5 h before the shape of it was recognised.

---

## 10e. Run `exp-tw-20260905`, launched 2026-09-05 05:47:43 EDT

The first run on the layout/v2 tree (PR #62), and the first with a TF32 lm_head.
Root `/scratch/gpfs/TRIDAO/al9080/terminal-rl/exp-tw-20260905`, run directory
`runs/tmax-9b--20260905-094743Z` (also reachable as `runs/latest`). Launched by
the same two systemd user units as `wd-20260904a`; measured 9 h with
`NRestarts=0` on both.

Placement is **2 trainer + 4 generator** on `RL_GPUS=6,7,1,2,5,4`, with
`SWE_DP_SHARD=2` and `SWE_GEN_DP=4`. The four generators were there from the
launch line, not added later.

### What a step actually costs

Read off `[trainer_loop] step N: <phase>` timestamps, steps 30-39. Median of the
last eight complete steps:

| segment | median | what it is |
|---|---|---|
| `awaiting training batch` -> microbatch 1 | 145 s (0 s on the last four steps) | waiting for the generators to fill a batch |
| microbatch 1 -> `forward_backward done` | 411 s | the training compute |
| `forward_backward done` -> `weights pushed` | 1-2 s | optimizer |
| `weights pushed` -> `weights pulled` | **84 s** | four engines pulling 9B weights out of torchstore |
| whole step (`begin` -> next `begin`) | **686 s** | |

Two things follow.

First, **do not read single-step wall-clock as a speed signal.** The batch size
swings between 16 and 104 microbatches depending on how many groups
`drop_zero_std` keeps, so the same machine produced a 231 s step and a 2193 s
step on the same afternoon. The stable number is s/microbatch: 8.7-10.9 s here.

Second, once the generators keep up (`awaiting` -> 0), **weight sync is the
largest remaining fixed cost**: 84 s of every 686 s step, 12%, and it does not
shrink with batch size. `optim done` -> `weights pushed` is 1-2 s, so essentially
all of it is the pull side. Overlapping it with the next step's forward would be
the next real win and is untouched -- it means changing the trainer's main loop,
which is not something to do to a run that is up and stable.

Throughput: 38 steps in 8 h 48 m = **4.3 steps/h** including the early
batch-starved steps, **5.2 steps/h** once `awaiting` reached 0.

### Hardening works, and the aggregate metric hides it

`group_zero_std_frac` sat around 0.40-0.44 all run and looked flat. It is the
wrong lens: it mixes tasks evolution has already hardened with tasks it has not
touched. Pairing each task against itself, keyed on the `rev` field in
`runs/*/rollouts/<task>/g<N>-r<M>.jsonl`:

```
                 16/16 solved   0/16 solved   has gradient
before (r0)           86%            0%            13%      (n=143)
after  (r>=1)         36%            3%            60%      (n=120)
```

100 tasks paired. Hardening converts roughly half the free wins into
mixed-outcome groups, and buys that for 3% all-failed. That is the effect the
aggregate number was averaging away, and it is the reason to keep the evolve
loop running rather than treat the flat `zero_std` as evidence it does nothing.

### One rung is not enough for a third of the corpus

Same data, split by how many times a task has been hardened (all groups, not
just the paired subset):

```
rev    draws   16/16   0/16   has gradient
r0      1099     28%    12%       59%
r1       223     34%     4%       61%
r2        28     53%     3%       42%
```

**36-38% of post-hardening draws are still 16/16.** The r2 row says the obvious
follow-up does not fix it either: the tasks that need a second rewrite are the
ones the first rewrite failed to bite, and the second fares no better.

The tunable is `evolution/task_size.py`:

```python
MIN_ADDED = 3         # at least 3 lines more than the seed
MAX_ADDED = 8         # at most 8 -- "one requirement"
MAX_ADDED_ASSERTS = 5
```

The verifier bounces any rewrite past `seed + MAX_ADDED`, and the comment there
justifies the narrow band with "in this corpus the 0/16 share doubles once a task
grows beyond one rung". The measurement above does not reproduce that yet: r1's
all-failed share is 4%, *lower* than r0's 12%, so the band still has headroom on
the hard side. Widening it (8 -> 12, asserts 5 -> 7) is the lever if the r2
population grows and its 16/16 share stays near 50%. It was deliberately not
changed mid-run: the constant is global, so moving it while the loop is folding
makes the r0 and r1 populations incomparable afterwards.

Corpus hardening coverage is not in the mix rows -- a row carries only
`prompt`, `label`, `metadata`. Read it off the directory tree instead, taking the
highest `rN` under `evolution/tasks/<id>/`:

```
r0 383   r1 213   r2 55   r3 12      => 280/663 = 42% hardened
```

### Generator load is deliberately uneven

Engine 0 ran at `Running: 255-256` -- exactly `max_num_seqs` -- with 30-51
queued, while the other three sat at 120-150 with an empty queue, for stretches
of tens of minutes. This is not a routing bug. `routing/intra_generator_router.py`
defaults to `StickySessionRoutingStrategy`: every request of one rollout session
is pinned to the DP rank that served its first request, so the prefix cache is
reused, and only new sessions consult the `LeastLoadedRoutingStrategy` fallback.
Load is counted in requests at `reserve` time, so a session that lasts 58 turns
and 3189 s keeps landing on the same rank however busy it becomes.

The payoff is the prefix hit rate: 93-96% on all four engines. The cost is the
skew, and the skew is harmless as long as `awaiting training batch` stays at 0 --
the queue delays those rollouts, not the trainer. Switching to
`LeastLoadedRoutingStrategy` would trade away exactly the thing the placement was
chosen to protect. Watch `awaiting`, not `Waiting`.

### Daytona loss rate

124 sandbox losses in 8 h 48 m (`RuntimeError: daytona sandbox <id> is no longer
available`, and `DaytonaTimeoutError`), arriving in bursts of 4-5 per ten-minute
bucket separated by quiet hours. Over any trailing 300 rollouts `status=error`
ran 0-3%. That is the platform's own flakiness and needs no action at this rate;
a retry in `boot_agent_sandbox` is the response if it reaches ~10%.

### Four monitoring readings that were wrong

Every one of these produced a confident false statement before it was caught, so
they are recorded as traps rather than as trivia.

- **`find -name stderr.txt -newermt '-5 minutes'` never matched anything.** This
  GNU find rejects the relative form, prints the accepted formats and exits; with
  `2>/dev/null` on the pipe the count is silently 0 forever. The evolve watchdog
  built on it would have declared "all sessions silent" on a healthy loop -- the
  same false-hang call that once cost a batch of in-flight rewrites. Use
  `-mmin -5`.
- **`ledger.jsonl` has no `status` field** (624 rows, all `None`). Counting
  pending from it returns 0. The counts live in `evolution/status.json`.
- **`grep -c 'status=completed'` counts rollouts, not groups** -- 23055 against a
  true 1398 groups, a 16x overstatement.
- **Mix rows carry no `rev`/`generation` key**, so a hardening-coverage figure
  computed from them reads 0%.

### Two earlier claims in this runbook's lineage that do not hold

- Adding a fourth generator was described as a live fix that took the prefix hit
  rate from 4.1% back to 93-96%. Checking every log of `wd-20260904a`: that run
  was `gen_dp=3` from start to finish and no fourth engine was ever added. The
  4.1% was measured on the three-generator run and the 93-96% on this one, which
  also changed the lm_head precision, `SWE_AC`, and the corpus. It is a
  between-run difference with several variables moving, not an A/B.
- "About 10 steps/hour" was quoted for this configuration. Measured: 4.3-5.2.
  The high figure came from timing `forward_backward` and forgetting that
  `awaiting training batch` is part of the step.

---

## 10a. Open questions for the team

Two values in the shared docs disagree with what this host measured. Neither is
resolved; both change how a run is sized.

**1. `TT_DAYTONA_DISK_GB=2` never applies to the TerminalWorld corpus.**
`runbook/rltrain.env` carries the live fleet sizing as 1 vCPU / 2 GB RAM / 2 GB
disk. The vCPU and memory halves match what the rows declare, but every one of
the 667 rows in `tw_live.jsonl` declares `daytona_disk_gb=10` -- including the 13
that declare no cpu or memory -- and a row's own value wins over the env
fallback (`daytona.py`: `self.disk_gb if self.disk_gb is not None else
_getenv(...)`). So the 2 GB default is inert for this corpus, and budgeting
storage from it under-counts by 5x. The storage arithmetic in section 5 uses
10 GB per sandbox, which is what the rows actually request.

**2. `TT_DAYTONA_CREATE_CONCURRENCY` is per-WORKER, and the two docs read as
different units.** The semaphore is a module-global `asyncio.Semaphore` and the
rollout workers are separate processes, so the limit applies once per worker.
`README_TERMINALWORLD.md` says exactly that -- `8 # per-worker create
parallelism` -- while `RUNBOOK.md` now gives 128 without the qualifier. The two
numbers differ by 16x, which is the worker count (`SWE_NUM_ROLLOUT_WORKERS=16`),
so they look like one value written in two units. At 128 per worker the account
sees 2048 concurrent creates. This host ran 32 (i.e. 512) through tw-prod-1 and
8 (128) through tw-prod-2; neither was measured against create failures.

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
