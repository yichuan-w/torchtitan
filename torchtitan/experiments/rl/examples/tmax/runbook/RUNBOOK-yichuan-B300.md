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

3. **TODO: keep `mix_live.jsonl` in sync with the upstream measured disk.**
   The claim above that "every row in this corpus declares 10 GiB" does not hold
   for `data/mix/mix_live.jsonl` as generated: 864 of its 1067 rows declared
   `daytona_disk_gb: 1`. Rows provisioned at 1 GiB die at session create with
   `no space left on device` (`mkdir /root/.daytona/sessions/<id>`), which is
   deterministic -- `max_attempts: 6` never reaches attempt 2, and the whole
   group of 16 is lost. Measured over a 15k-rollout run: 65 such events across
   30 tasks, two of which (`tw_177860`, `tw_680933`) lost all 16 rollouts each.

   The fix is not to guess sizes and not to push anything upstream. The Clean
   dataset already carries them: `andylizf/TerminalWorld-Seeds-Clean`, file
   `metadata/measured_disk.csv`, from its 2026-08-30 "measured real-block disk
   usage for 759 tasks (full Daytona build campaign)" commit. Column
   `recommend_daytona_gb` is the number to take. Against it, 513 of the 667
   overlapping rows in our local copy were UNDER-provisioned (510 of them
   1 GiB where the measurement says 2 GiB) -- our copy predates that campaign.

   Sync on every data rebuild, raising only (the measurement covers build-time
   occupancy; a task that downloads at runtime can need more):

```python
import json, pandas as pd
from huggingface_hub import hf_hub_download
md = pd.read_csv(hf_hub_download(
    "andylizf/TerminalWorld-Seeds-Clean", "metadata/measured_disk.csv",
    repo_type="dataset"))
rec = {str(k): int(v) for k, v in zip(md.task_id, md.recommend_daytona_gb)
       if pd.notna(v)}
out = []
for line in open("data/mix/mix_live.jsonl"):
    if not line.strip():
        continue
    row = json.loads(line)
    meta = row.get("metadata") or {}
    want, cur = rec.get(str(meta.get("instance_id"))), meta.get("daytona_disk_gb")
    if want is not None and cur is not None and cur < want:
        meta["daytona_disk_gb"] = want
    out.append(json.dumps(row, ensure_ascii=False))
open("data/mix/mix_live.jsonl", "w").write("\n".join(out) + "\n")
```

   Applied 2026-08-31: 513 rows raised. Still unresolved -- 352 rows remain at
   1 GiB and 83 declare nothing, because only 667 of the 1067 rows appear in the
   measured table at all. Those are the next ones to blow up.


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

# trainer speed. SWE_LMHEAD_TF32 deliberately unset -> full fp32 lm_head inputs.
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

## 10b. The 2026-09-02 run: per-run workdir, TerminalWorld only, evolution on

Section 10 is the no-evolution profile. This one runs the online task-evolution
loop beside the trainer, and every path it touches is per-run rather than shared.
Both changes came out of failures measured on this host over 2026-09-01/02; the
numbers below are from those measurements, not from a recipe.

### What made this run different

**A private workdir, because two loops on one host ate each other's work.**
`evolve_ondella.py` derives `signals/`, `consumed/`, `retuned/`, `junk/`,
`deferred_easier/`, the lineage git, `POOL_ROOTS` and the default mix from one
env var, `TRL_BASE`. Left at its default, every loop on the host shares all of
them: on 2026-09-01 two were running and consuming signals out of the same
directory. Separately, other tooling rewrote the shared `data/mix/mix_live.jsonl`
nine times during a single 13-hour run (`.bak-audit-*`, `.bak-overcap-*`,
`.bak-busybox-*`, `.bak-evolved-*`), and each rewrite tripped
`SWE_DATA_HOT_RELOAD` and invalidated the prefix cache -- the hit rate cycled
92% -> 43-58% every 15-20 minutes. Pointing `TRL_BASE` at a per-run directory
fixes both, and needs no code change.

Building one is three steps and about 5 MB:

```bash
W=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-<stamp>
mkdir -p $W/data/tw-extract/tasks $W/data/mix $W/evolution/signals $W/logs $W/runs $W/meta

# 1. packages: the published shard, filtered by the published id list
curl -sL "https://huggingface.co/datasets/andylizf/TerminalWorld-Seeds-Clean/resolve/main/data/tasks-00000.tar" -o t.tar
tar -xf t.tar -C /tmp/hf                      # 1353 complete packages
while read -r t; do cp -a /tmp/hf/tasks/$t $W/data/tw-extract/tasks/$t; done < train_ready_ids.txt

# 2. pack to rows
TRL_BASE=$W python .../evolution/pack_to_dataset.py \
    --evolved $W/data/tw-extract/tasks --out $W/data/mix/mix_live.jsonl

# 3. strip the canary from what the model reads; size from the measurements
#    (see below)
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
the branch off does not discard the backlog: 0/k signals move to
`evolution/deferred_easier/` and replay when it is turned back on.

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
# per-run root: signals/, consumed/, retuned/, junk/, deferred_easier/, the
# lineage git, POOL_ROOTS and runs/ all hang off this
TRL_BASE=/scratch/gpfs/TRIDAO/al9080/terminal-rl/workdirs/wd-20260902-tw

# TerminalWorld only, 663 rows, canary stripped, sized from measured_resources
SWE_PROMPT_DATA=$TRL_BASE/data/mix/mix_live.jsonl
SWE_TASK_EVOLUTION_DIR=$TRL_BASE/evolution/signals
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

# per-sandbox exec timings, per run (a stale value here silently interleaves
# two runs' traces under one directory: group ids collide and the later write wins)
TMAX_EXEC_TRACE_DIR=$TRL_BASE/exec-traces
```

The evolution loop runs as its own process against the same root, so restarting
it costs no rollouts:

```bash
TRL_BASE=$W TRL_TT=... PYTHONPATH=... \
  python .../evolution/evolve_ondella.py --interval 120 --workers 16 \
         --log $W/logs/evolve.log
```

### Not established

Whether concurrency 1024 holds. The collapse above first appears 30-45 minutes
after the first completed group, and this run had not reached that window when
these numbers were written. Zero queue depth during a cold start proves nothing:
the previous run looked identical for its first 30 minutes.

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
