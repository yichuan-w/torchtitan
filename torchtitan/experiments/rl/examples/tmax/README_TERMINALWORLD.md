# TerminalWorld + SWE-Smith mix run (Qwen3.5-9B, Terminus-2)

End-to-end recipe for RL-training Qwen3.5-9B as a terminal agent on a mix of two
oracle-validated Harbor corpora, scored on Terminal-Bench 2.0. This is the concrete
parameter set behind the `rl_grpo_qwen3_5_9b_tmax` recipe; for the general recipe
internals see [`README.md`](README.md) and for the seed-data pipeline see
[`README_SEED_DATA.md`](README_SEED_DATA.md).

Everything here runs on the open-source entry point
(`python -m torchtitan.experiments.rl.train`) with a Daytona API key; no other
service is required. Two 8-GPU hosts is the minimum (one trainer + one generator
host); more generator hosts raise rollout throughput.

- Agent: **Terminus-2** (`TMAX_AGENT=terminus`), the tmux-driving scaffold from the
  `harbor` package, NOT the default one-command `vanillux` loop.
- Generator: **`torchtitan_wrapper`** (the unified TorchTitan GDN model run inside
  vLLM), so the generator and trainer run the same model code + weights.
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

Both corpora are public Hugging Face datasets with a Harbor task-tree layout; one
adapter (`prepare_rts_data.py`) reads both. The full pipeline -- download, extract,
adapt, and filter by the datasets' own quality columns -- is documented in
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

The 9B parameter set of the recorded multi-host run (its results are in section
6). It is kept exactly as that run used it, so some values below have since
moved -- the inline notes say which. For the settings the current single-host run uses, see
[`runbook/RUNBOOK.md`](runbook/RUNBOOK.md) and its `rltrain.env`.

The trainer spans the HSDP-32
degrees below; each generator is a separate single-GPU vLLM engine (TP-1),
data-parallel, so more generators means more concurrent decode for the hundreds of
live agents.

```bash
export DAYTONA_API_KEY=dtn_...
export SWE_PROMPT_DATA=/path/to/mix_tw_swe.jsonl

# --- harness: Terminus-2 with the recipe's context / turn budget ---
export TMAX_AGENT=terminus
export TMAX_TERMINUS_MAX_TURNS=120
export SWE_MAX_CONTEXT_LEN=63488
export TMAX_TURN_MAX_TOKENS=32768

# --- generator: unified TorchTitan GDN inside vLLM (narrows gen/trainer logprob gap) ---
export SWE_GEN_BACKEND=torchtitan_wrapper

# --- GRPO/async shape: 32 groups x 16 siblings = 512 rollouts/step, no zero-std drop ---
export SWE_NUM_GROUPS_PER_TRAIN_STEP=32
export SWE_GROUP_SIZE=16
export SWE_DROP_ZERO_STD=0            # keep all-pass/all-fail groups (they add no gradient)
export SWE_OFFPOLICY_STEPS=5          # policy-age cap: a group unused for >5 versions is stale-dropped
export SWE_MAX_ACTIVE_GROUPS=160      # run-ahead buffer (groups). Must clear the per-worker
                                      # work-conserving minimum (~144 at conc 2048 / 16 workers).
                                      # Kept small on purpose: a LARGE buffer (e.g. 512) lets many
                                      # slow/long rollouts finish stale and get dropped, biasing the
                                      # trained batch toward short/easy tasks -- see the buffer note below.
export SWE_TRAIN_STEPS=150

# --- selection + throughput ---
# SWE_SELECTION_WINDOW_GROUPS: leave UNSET (None = take-any over all active groups).
# Recommended default going forward -- see the "Selection window vs buffer size" note
# below. This run originally used =64 (take from the oldest 64 finalized groups); None
# is as-good-or-better and never worse in the comparisons we ran.
export SWE_ROLLOUT_CONCURRENCY=2048   # active sandboxes. High concurrency congests the
                                      # controller<->generator Monarch channels and can trigger
                                      # false "proc dead" crashes (seen at 1280 with defaults);
                                      # running this high is only safe with the stability settings
                                      # below (raised supervision timeout + checkpoint auto-resume)
                                      # and with build-failing tasks removed from the data.
export SWE_NUM_ROLLOUT_WORKERS=16     # CPU agent-orchestration processes, off the controller GIL
export SWE_CKPT_INTERVAL=5            # checkpoint every 5 steps (cheap resume on failure)

# --- stability: what lets the high concurrency above survive slow-sandbox stalls ---
export MONARCH_SUPERVISION_WATCHDOG_TIMEOUT=1000s  # tolerate slow Daytona ops (cold builds up to
                                      # ~900s); the default (~20s) falsely declares a stalled proc
                                      # dead and tears down the whole job. Pair with a launcher that
                                      # auto-resumes from the latest checkpoint on failure.

# --- rollout wall-clock + sandbox tuning (recipe defaults; listed for reproducibility) ---
# (SWE_GDN=1 was exported here historically; no code reads it -- it is inert.
# The recipe itself selects the GDN model path.)
export SWE_DISABLE_CUSTOM_ALL_REDUCE=1  # required for the GDN generator under vLLM
export SWE_TIME_BUDGET_SEC=2400       # per-rollout wall-clock budget (40 min)
export TMAX_EXEC_TIMEOUT_SEC=120      # per-command timeout inside the sandbox
export SWE_MAX_NUM_SEQS=32            # vLLM engine max concurrent sequences
export TT_DAYTONA_CPU=2               # per-sandbox resources (Daytona)
export TT_DAYTONA_MEM_GB=4
export TT_DAYTONA_DISK_GB=10
export TT_DAYTONA_HEARTBEAT_SEC=180   # sandbox keep-alive
export TT_DAYTONA_CREATE_CONCURRENCY=8  # per-worker create parallelism; lower if the provider 429s
# NOTE (2026-08-29): the sandbox values above are what this recorded run used.
# The live run has since moved to a 1 vCPU / 2 GB RAM / 2 GB disk fleet default
# with per-row daytona_cpu/mem_gb/disk_gb overrides (Daytona hard caps: 4 vCPU /
# 8 GB / 10 GB per sandbox), TT_DAYTONA_CREATE_CONCURRENCY=128, and
# SWE_ROLLOUT_CONCURRENCY=1536 (sized against the LLM decode slots). See
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
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 12 \
    --num-eval-generators 1 \
    --hf_assets_path /path/to/Qwen3.5-9B
```

> **Data hygiene matters for stability.** A task whose Docker image fails to build
> (e.g. `apt-get` on an EOL Debian base) makes every rollout on it retry sandbox
> creation ~6x; a single such task sampled repeatedly floods the rollout workers with
> Daytona create storms that saturate the controller host and trigger the false
> "proc dead" crash above. Filter build-failing tasks out of the training JSONL (watch
> the logs for repeated `BUILD_FAILED` on one `instance_id`).

> **Buffer size and the stale-drop bias (measured).** `SWE_MAX_ACTIVE_GROUPS` is the
> run-ahead pool of in-flight groups, and it is the dominant lever on the pinned
> training reward. A large buffer keeps many rollouts racing at once; the slow/long
> ones tend to finish *after* their `SWE_OFFPOLICY_STEPS` window and are
> `stale_dropped` (visible in the log as `[buffer] complete ... -> RELEASE(stale_dropped)`),
> so the trained batch is skimmed from the FAST/short finishers. Measured full-run
> stale-drop rate rose steeply with the in-flight pool (~13% at buffer 512 / concurrency
> 768, ~41% at buffer 512 / concurrency 2048) and the pinned `rollout_reward/_mean`
> tracked it upward -- i.e. the "high reward" of a big buffer is partly a short-task
> selection artifact, and it wastes compute (dropped rollouts, including trainable
> partial-solves). We therefore keep the buffer SMALL (160): ~3-5% stale, a more
> representative batch, at the cost of a lower (but more honest) pinned reward. Judge
> progress on the TB-2.0 curve, not the training reward.

> **Selection window vs buffer size (measured).** `SWE_SELECTION_WINDOW_GROUPS` only
> affects the training-reward composition when the off-policy buffer
> (`SWE_MAX_ACTIVE_GROUPS`) is large enough to hold many finalized candidates. In
> controlled single-variable comparisons (everything else fixed): at a large buffer
> (`=512`), widening the window from 20 to 64 groups raised the batch-mean
> `rollout_reward/_mean` from ~0.58 to ~0.69 over matched steps; at a small buffer
> (`=160`), changing the window (20 vs None) stayed within run-to-run noise (~0.60 vs
> ~0.56). A wider window can only "cherry-pick" when the candidate pool is large, so
> its effect is conditional on the buffer. Since a wider window is as-good-or-better and
> never worse, **prefer leaving `SWE_SELECTION_WINDOW_GROUPS` unset (None = take-any)**
> going forward. Caveat: this rests on short single runs without a variance estimate,
> and a higher pinned reward partly reflects a shorter-rollout selection bias, not
> necessarily better held-out generalization -- read the TB-2.0 curve, not just the
> training reward.

### Host topology

For the parameter set above on 8-GPU hosts, the run spans **~18 hosts**: 1 controller
+ 4 trainer hosts (the HSDP-32 trainer, `SWE_DP_REPLICATE=4`) + 12 generator hosts (one
per `--num-generators`, each a TP-1 vLLM engine) + 1 eval-generator host. Scale generator
hosts to trade cost for rollout throughput; the trainer host count follows the parallelism
degrees. Launching across hosts is your own job scheduler's problem.

### Multi-host trainer (optional)

On a fresh multi-host trainer, a naive `dp_shard=N` can hang on the first cross-host
all-gather. Use HSDP instead -- `dp_replicate x dp_shard=8` keeps each all-gather
within a host -- via `SWE_DP_REPLICATE`: `=2` gives HSDP-16 (2 hosts, ~2x faster
fwd_bwd vs FSDP-8), `=4` gives HSDP-32 (4 hosts). Note the run is usually
generation-bound (steps paced by rollout production), so more trainer DP helps only
while `batch-wait` is low; if `fwd_bwd` drops but `batch-wait` rises, add generators
instead.

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

### Optional: score a single checkpoint offline

To score one saved checkpoint without a training run (e.g. re-checking a step), use the
eval-only recipe -- same harness, `num_training_steps=0`, one validation pass:

```bash
SWE_TB2_DATA=/path/to/tb2_eval.jsonl SWE_TB2_CKPT=/path/to/run/checkpoint/step-N \
TMAX_AGENT=terminus SWE_MAX_CONTEXT_LEN=63488 SWE_GEN_BACKEND=torchtitan_wrapper \
python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --num-generators 1 --hf_assets_path /path/to/Qwen3.5-9B
# Leave SWE_TB2_CKPT unset to score the BASE model (the step-0 baseline).
```

## 4. Parameters at a glance

| Variable | This run | Meaning |
| --- | --- | --- |
| `SWE_PROMPT_DATA` | `mix_tw_swe.jsonl` | TerminalWorld pass + SWE-Smith main pool |
| `TMAX_AGENT` | `terminus` | Terminus-2 scaffold (default `terminus`) |
| `TMAX_TERMINUS_MAX_TURNS` | 120 | Max agent turns per episode |
| `SWE_MAX_CONTEXT_LEN` | 63488 | Agent context budget |
| `TMAX_TURN_MAX_TOKENS` | 32768 | Per-turn generation cap |
| `SWE_GEN_BACKEND` | `torchtitan_wrapper` | Unified GDN model in vLLM |
| `SWE_NUM_GROUPS_PER_TRAIN_STEP` | 32 | GRPO prompt groups per step |
| `SWE_GROUP_SIZE` | 16 | Siblings per group |
| `SWE_DROP_ZERO_STD` | 0 | Keep zero-variance groups (no oversampling) |
| `SWE_OFFPOLICY_STEPS` | 5 | Policy-age cap; group unused for >5 versions is stale-dropped |
| `SWE_MAX_ACTIVE_GROUPS` | 160 | Run-ahead buffer (groups); small on purpose -- see buffer note |
| `SWE_SELECTION_WINDOW_GROUPS` | None (run used 64) | Sliding-prefix selection window; None=take-any. Prefer None -- see note above |
| `SWE_TRAIN_STEPS` | 150 | Optimizer steps |
| `SWE_ROLLOUT_CONCURRENCY` | 2048 | Active sandboxes (needs the raised supervision timeout below) |
| `SWE_NUM_ROLLOUT_WORKERS` | 16 | Agent-orchestration CPU processes |
| `SWE_NUM_GENERATORS` (`--num-generators`) | 12 | vLLM engines (each 1 GPU) |
| `SWE_CKPT_INTERVAL` | 5 | Checkpoint cadence (steps) |
| `SWE_DP_REPLICATE` | 4 | HSDP replicate degree (4 = HSDP-32, 4 trainer hosts; 2 = HSDP-16) |
| `MONARCH_SUPERVISION_WATCHDOG_TIMEOUT` | 1000s | Tolerate slow-sandbox stalls at high concurrency |
| `SWE_TB2_VAL_DATA` | `tb2_eval.jsonl` | Inline TB-2.0 eval set (enables eval) |
| `SWE_NUM_EVAL_GENERATORS` (`--num-eval-generators`) | 1 | Dedicated async eval-generator host |
| `SWE_VAL_INTERVAL` / `SWE_VAL_SAMPLES` | 20 / 89 | Eval every 20 steps, all 89 tasks |

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

## 6. Results (this recipe)

A 150-step run of the recipe above (window unset / buffer 160 / off 5 / concurrency
2048 / HSDP-32) produced the first clearly positive TB-2.0 transfer we have seen on
this setup. Inline TB-2.0 (89 tasks, k=5), by `validation/policy_version`:

| policy_version | avg@5 | pass@5 |
| --- | --- | --- |
| 0 (base) | 0.178 | 0.303 |
| 40 | 0.218 | 0.326 |
| 80 | 0.200 | 0.337 |
| 100 | 0.207 | 0.303 |
| 140 | **0.227** | **0.348** |

All four post-training points beat the base-model baseline on avg@5 (mean ~0.21,
best +0.049 at the end); pass@5 ends at its maximum (+0.045, ~4 tasks). The run
finished all 150 steps with zero restarts and no supervision crash.

Note the eval lags training (a pass scores an older `policy_version`) and lands
roughly every ~40 steps, because a ~20-step pass is skipped whenever the previous
one is still running -- so a 150-step run yields ~5 eval points, below the ~7 the
noise caveat above wants. Treat the trend as encouraging-but-not-definitive and, for
a cleaner curve, raise eval capacity (more `--num-eval-generators`) or
`SWE_VAL_INTERVAL` so passes do not overlap.

The likely reason this recipe transfers where larger-buffer configs did not: the
small buffer keeps the buffer-level stale-drop rate low (~5-6% of completed rollouts
vs ~40% at buffer 512 / concurrency 2048), so the trained batch is not skimmed toward
short/easy tasks -- see the buffer note in section 2.
