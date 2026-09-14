# TerminalWorld + SWE-Smith mix run (Qwen3.5-27B, Terminus-2)

End-to-end recipe for RL-training Qwen3.5-27B as a terminal agent on a mix of two
oracle-validated Harbor corpora, scored on Terminal-Bench 2.0. This is the concrete
parameter set behind the `rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2` recipe; for the
general recipe internals see [`README.md`](README.md), for the seed-data pipeline see
[`README_SEED_DATA.md`](README_SEED_DATA.md), and for the 9B version of this exact run
see [`README_TERMINALWORLD.md`](README_TERMINALWORLD.md).

**This is the 27B counterpart of the 9B TerminalWorld run.** Everything that is not
model-size-driven -- the data pipeline, the Terminus-2 harness, the GRPO/async shape,
the DPPO loss + fp32-master + salt-KV recipe, and the inline TB-2.0 eval -- is
identical to the 9B run. The `rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2` recipe is derived
from `rl_grpo_qwen3_5_9b_tmax` (NOT the swe_r2e-descended `rl_grpo_qwen3_5_27b_tmax`),
so it keeps the 9B's tuned settings. Only three things change, each forced by the
larger model on 80GB H100s:

1. **Trainer FSDP-32 x TP-2** (world 64 = 8 hosts). Sharding alone does not fit 27B at
   seq 65536: per-rank activation is one packed 65536-token row at any shard degree.
   TP-2 shards the SwiGLU intermediate (w1/w3 colwise) and, via Qwen3.5's SP-carrying
   TP plan, the FullAC layer inputs.
2. **Generator TP-2, DP-4** (4 engines per 8-GPU host). TP-1 does not fit: 27B bf16
   weights are ~54GB and one 65536-token sequence needs ~4GiB of KV; a prior 27B TP-1
   attempt measured "Available KV cache memory: -8.96 GiB" and died at engine init.
   TP-2 halves both (~27GB weights, ~32 KiB/token/GPU), leaving room for ~5.7
   full-length sequences per engine at `gpu_memory_limit=0.8`.
3. **Loss re-chunked to 64** (from the 9B's 32) so the per-chunk fp32 lm-head logits
   stay in the validated ~1GiB envelope at 27B's larger hidden size.

- Agent: **Terminus-2** (`TMAX_AGENT=terminus`), the tmux-driving scaffold from the
  `harbor` package, NOT the default one-command `vanillux` loop.
- Generator: **`torchtitan_wrapper`** (the unified TorchTitan GDN model run inside
  vLLM) at TP-2, so the generator and trainer run the same model code + weights --
  same as the 9B run (the wrapper now supports TP>1).
- Data: **TerminalWorld-Seeds-Clean** (general terminal tasks) mixed with
  **SWE-Smith-Seeds-Clean** (Python repo bug-fix), both graded by the identical
  contract (`bash /tests/test.sh` -> `/logs/verifier/reward.txt` 0/1).
- Eval: **Terminal-Bench 2.0** (89 tasks), k=5, decoupled from training (below).

## 0. Prerequisites

```bash
pip install -r requirements.txt          # torchtitan + the RL experiment deps
pip install harbor                        # provides Terminus-2 and TB-2.0 tasks
export DAYTONA_API_KEY=dtn_...            # sandbox provider
```

## 1. Prepare the data

Identical to the 9B run -- same two corpora, same adapter, same TB-2.0 eval set. Both
corpora are public Hugging Face datasets with a Harbor task-tree layout; one adapter
(`prepare_rts_data.py`) reads both. The full pipeline -- download, extract, adapt, and
filter by the datasets' own quality columns -- is documented in
[`README_SEED_DATA.md`](README_SEED_DATA.md). The short version:

1. Download + extract each dataset's `data/*.tar` shards and `metadata/tasks.parquet`
   (`huggingface_hub`), giving a local `tasks/` dir per corpus.
2. Adapt each to a JSONL. `--inject-agent-runtime` bakes `tmux` into every image at
   build time -- REQUIRED for Terminus-2, whose runtime self-install fails on the
   SWE-Smith base images (without it the whole SWE-Smith half scores reward 0):

   ```bash
   python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
       --tasks-root /path/to/terminalworld/tasks --inject-agent-runtime \
       --out terminalworld_all.jsonl
   python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
       --tasks-root /path/to/swesmith/tasks --inject-agent-runtime \
       --out swesmith_all.jsonl
   ```

3. Filter each JSONL by joining `metadata/tasks.parquet` on `task_id` (see
   `README_SEED_DATA.md` for the exact pandas snippet):
   - TerminalWorld: keep the ids in `metadata/train_ready_ids.txt` (669) -- the
     oracle-passed tasks minus the fragile-build, policy-blocked and
     oversized-memory lists (see `README_SEED_DATA.md`). `reward_verdict ==
     "pass"` alone (~859) also keeps tasks whose builds are fragile on the
     sandbox platform; the fail/unknown 36% have a reference solution that
     cannot even earn reward 1.
   - SWE-Smith: keep `in_main_pool and not network_required` (~1,408 tasks) -- the
     authors' stratified, repo-balanced pool; the grader runs `--network none`.

4. Concatenate into one training JSONL (labels are namespaced, no collision), and
   build the TB-2.0 eval set:

   ```bash
   cat terminalworld_pass.jsonl swesmith_main.jsonl > mix_tw_swe.jsonl
   python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_data --out tb2_eval.jsonl
   ```

Validate the sandbox + grading path before touching GPUs (no training stack needed):

```bash
DAYTONA_API_KEY=dtn_... python torchtitan/experiments/rl/examples/tmax/local_smoke.py \
    --data mix_tw_swe.jsonl --limit 2
```

## 2. Launch training

The full parameter set used for this run. The trainer takes **8 8-GPU hosts**
(FSDP-32 x TP-2); each generator is a separate 8-GPU host running **4 TP-2 vLLM
engines** (DP-4 x TP-2), data-parallel, so more generator hosts means more concurrent
decode for the hundreds of live agents.

```bash
export DAYTONA_API_KEY=dtn_...
export SWE_PROMPT_DATA=/path/to/mix_tw_swe.jsonl

# --- harness: Terminus-2 with the recipe's context / turn budget ---
export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768

# --- generator: unified TorchTitan GDN inside vLLM at TP-2 (narrows gen/trainer logprob gap) ---
# Same as the 9B run; the unified wrapper generator now supports TP>1, so the 27B's
# forced TP-2 stays on the wrapper path (gen and trainer run the same model code +
# weights). No SWE_DPPO_RATIO_CAP needed -- the wrapper's gen/train logprob gap is
# small enough for the recipe-faithful uncapped DPPO loss.
export SWE_GEN_BACKEND=torchtitan_wrapper
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1  # required for the GDN generator under vLLM at TP>1

# --- GRPO/async shape: 32 groups x 16 siblings = 512 rollouts/step, no zero-std drop ---
export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0            # keep all-pass/all-fail groups (they add no gradient)
export SWE_MAX_ACTIVE_GROUPS=512      # buffer capacity >= (off+1) * groups
export SWE_TRAIN_STEPS=150

# --- selection + throughput ---
export SWE_SELECTION_WINDOW_GROUPS=64 # take from the oldest 64 finalized groups. 64 = 2x
                                      # the 32-group batch: enough to fill a step without
                                      # head-of-line blocking, bounded (more on-policy +
                                      # less stale-drop waste) than None=take-any.
export SWE_ROLLOUT_CONCURRENCY=768    # active sandboxes. Fills a 512-rollout step with
                                      # headroom; most terminal episodes are far shorter
                                      # than 65536 so the ~273 full-length KV seats across
                                      # the 48 TP-2 engines are rarely the binding limit.
                                      # Higher congests the controller<->generator Monarch
                                      # channels -> false "proc dead" crashes.
export SWE_NUM_ROLLOUT_WORKERS=16     # CPU agent-orchestration processes, off the controller GIL
export SWE_CKPT_INTERVAL=20           # checkpoint every 20 steps. 27B DCP is ~330GB/snapshot
                                      # (~3x the 9B's 109GB), so interval 20 over 150 steps
                                      # writes ~2.3TB; interval 5 (the 9B default) would be ~10TB.

# --- rollout wall-clock + sandbox tuning (recipe defaults; listed for reproducibility) ---
# (SWE_GDN=1 was exported here historically; no code reads it -- it is inert.
# The recipe itself selects the GDN model path.)
export SWE_TIME_BUDGET_SEC=2400       # per-rollout wall-clock budget (40 min)
export TMAX_EXEC_TIMEOUT_SEC=120      # per-command timeout inside the sandbox
export SWE_MAX_NUM_SEQS=32            # vLLM engine max concurrent sequences
export TT_DAYTONA_CPU=2               # per-sandbox resources (Daytona)
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_HEARTBEAT_SEC=180   # sandbox keep-alive
export TT_DAYTONA_CREATE_CONCURRENCY=8  # per-worker create parallelism; lower if the provider 429s
# NOTE (2026-08-29): the sandbox values above mirror the recorded 9B run. The
# live setup has since moved to a 1 vCPU / 2 GB RAM / 2 GB disk fleet default
# with per-row daytona_cpu/mem_gb/disk_gb overrides (Daytona hard caps: 4 vCPU /
# 8 GB / 10 GB per sandbox) and TT_DAYTONA_CREATE_CONCURRENCY=128. See
# runbook/RUNBOOK.md.

# --- INLINE Terminal-Bench 2.0 eval (see section 3): scored on a dedicated async eval
#     generator every SWE_VAL_INTERVAL steps, logged to the training W&B run. The step-0
#     pre-training pass is the baseline. ---
export SWE_TB2_VAL_DATA=/path/to/tb2_eval.jsonl  # the 89-task TB-2.0 JSONL (prepare_tb2_data)
export SWE_NUM_EVAL_GENERATORS=1      # one dedicated eval-generator host (async, off the training pool)
export SWE_VAL_INTERVAL=20            # eval every 20 optimizer steps
export SWE_VAL_SAMPLES=89             # all 89 TB-2.0 tasks per pass (k=5)

python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2 \
    --num-generators 12 \
    --num-eval-generators 1 \
    --hf_assets_path /path/to/Qwen3.5-27B
```

> **Data hygiene matters for stability.** A task whose Docker image fails to build
> (e.g. `apt-get` on an EOL Debian base) makes every rollout on it retry sandbox
> creation ~6x; a single such task sampled repeatedly floods the rollout workers with
> Daytona create storms that saturate the controller host and trigger the false
> "proc dead" crash above. Filter build-failing tasks out of the training JSONL (watch
> the logs for repeated `BUILD_FAILED` on one `instance_id`).

### Host topology

For the parameter set above on 8-GPU hosts, the run spans **~22 hosts**: 1 controller
+ 8 trainer hosts (FSDP-32 x TP-2 = world 64) + 12 generator hosts (one per
`--num-generators`, each DP-4 x TP-2 = 8 GPUs running 4 engines) + 1 eval-generator
host. Scale generator hosts to trade cost for rollout throughput; the trainer host
count is fixed by the FSDP-32 x TP-2 degrees. Launching across hosts is your own job
scheduler's problem.

The trainer topology is not optional here (unlike the 9B run, which starts at FSDP-8
on one host and offers HSDP-16/32 as a speed knob). 27B at seq 65536 does not fit
below FSDP-32 x TP-2; the config hardcodes it, so `SWE_DP_SHARD` / `SWE_DP_REPLICATE`
have no effect on this recipe. The run is usually generation-bound (steps paced by
rollout production, and 27B decode is slower per token than 9B with fewer KV seats per
engine), so if `fwd_bwd` is low while `batch-wait` is high, add generator hosts rather
than trainer hosts.

## 3. Inline TB-2.0 eval (built into the run)

The launch above already turns eval on via `SWE_TB2_VAL_DATA` + `--num-eval-generators 1`:
a validation pass scores all 89 TB-2.0 tasks (k=5, temperature 0.7, top_p 0.95) on a
**dedicated eval-generator host, asynchronously**, every `SWE_VAL_INTERVAL` steps. A pass
still in flight when the next interval arrives is **skipped, not queued**. Results land
in the **training W&B run**:

- `validation/reward/mean` = **avg@5**
- `validation/pass_at_k` = **pass@5**
- `validation/policy_version` = the policy version scored (the async pass lags training).

The **step-0 pre-training pass is the base-model baseline** (compare later versions to it).
Because the eval runs on its own generator host, training does not block on it.

Each pass also writes a browsable report at
`<dump>/validation_traces/step-<policy_version>/{INDEX.md,summary.json,traces/}`.

> **Offline single-checkpoint scoring is 9B-only today.** The eval-only recipe
> (`rl_grpo_qwen3_5_9b_tmax_tb2_eval`, `SWE_TB2_CKPT=...`) is hardcoded to the 9B
> model. For 27B, read the run's TB-2.0 curve from the inline eval above; to re-score
> a single 27B checkpoint offline you would need a 27B eval-only recipe (clone the 9B
> one with `_qwen3_5_rl_model_registry("27B")` + FSDP-32 x TP-2). Not wired yet.

## 4. Parameters at a glance

| Variable | This run | Meaning | Same as 9B? |
| --- | --- | --- | --- |
| `--config` | `rl_grpo_qwen3_5_27b_tmax_fsdp32_tp2` | 27B recipe (derived from the 9B tmax recipe) | no (27B) |
| `SWE_PROMPT_DATA` | `mix_tw_swe.jsonl` | TerminalWorld pass + SWE-Smith main pool | yes |
| `TMAX_AGENT` | `terminus` | Terminus-2 scaffold (default `terminus`) | yes |
| `TMAX_TERMINUS_MAX_TURNS` | 120 | Max agent turns per episode | yes |
| `SWE_MAX_CONTEXT_LEN` | 63488 | Agent context budget | yes |
| `TMAX_TURN_MAX_TOKENS` | 32768 | Per-turn generation cap | yes |
| `SWE_GEN_BACKEND` | `torchtitan_wrapper` | Unified GDN model in vLLM (wrapper now supports TP>1) | yes |
| `SWE_NUM_GROUPS_PER_TRAIN_STEP` | 32 | GRPO prompt groups per step | yes |
| `SWE_GROUP_SIZE` | 16 | Siblings per group | yes |
| `SWE_DROP_ZERO_STD` | 0 | Keep zero-variance groups (no oversampling) | yes |
| `SWE_MAX_ACTIVE_GROUPS` | 512 | Off-policy buffer capacity (groups) | yes |
| `SWE_SELECTION_WINDOW_GROUPS` | 64 | Sliding-prefix selection window (>= batch; None=take-any) | yes |
| `SWE_TRAIN_STEPS` | 150 | Optimizer steps | yes |
| `SWE_ROLLOUT_CONCURRENCY` | 768 | Active sandboxes | yes |
| `SWE_NUM_ROLLOUT_WORKERS` | 16 | Agent-orchestration CPU processes | yes |
| `SWE_NUM_GENERATORS` (`--num-generators`) | 12 | Generator hosts (each DP-4 x TP-2 = 4 engines) | count yes, TP no |
| `SWE_CKPT_INTERVAL` | 20 | Checkpoint cadence (steps); ~330GB/snapshot | no (9B: 5) |
| Trainer parallelism | FSDP-32 x TP-2 (8 hosts) | Fixed by the recipe (not env-overridable) | no (9B: FSDP-8/HSDP) |
| Generator parallelism | DP-4 x TP-2, `gpu_memory_limit=0.8` | 4 engines/host; TP-2 fits KV at seq 65536 | no (9B: DP-8 x TP-1) |
| Loss chunks | 64 | fp32 lm-head chunk envelope at 27B hidden size | no (9B: 32) |
| `SWE_TB2_VAL_DATA` | `tb2_eval.jsonl` | Inline TB-2.0 eval set (enables eval) | yes |
| `SWE_NUM_EVAL_GENERATORS` (`--num-eval-generators`) | 1 | Dedicated async eval-generator host | yes |
| `SWE_VAL_INTERVAL` / `SWE_VAL_SAMPLES` | 20 / 89 | Eval every 20 steps, all 89 tasks | yes |

## 5. Reading the metrics

- **`rollout_reward/_mean`** with `drop_zero_std=0` is the real batch-mean reward
  (not pinned), so it is a usable training-progress signal (though noisy: the batch
  composition varies pass to pass).
- **`validation/reward/mean`** (avg@5) and **`validation/pass_at_k`** (pass@5),
  keyed by `validation/policy_version`, are the inline TB-2.0 curve. Compare later
  versions to the step-0 base-model baseline. Do not read a trend from fewer than
  ~7 points -- a single TB-2.0 pass has real run-to-run noise.
- The SWE-Smith half's improvement shows only in the training reward; the TB-2.0
  eval set is terminal-only.
- Watch **`bit_wise/logprob_diff/{abs_mean,max}`**: this run uses the unified
  `torchtitan_wrapper` GDN generator (same as 9B), so the gen/train logprob gap should
  stay small. If it drifts and the reward curve gets unstable, that tail is the first
  thing to check (a truncated-IS `SWE_DPPO_RATIO_CAP=2` is the escape hatch, off by
  default here to stay recipe-faithful).
