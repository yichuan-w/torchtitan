# tmax terminal-agent RL (Qwen3.5-9B GDN)

Post-train a Qwen model as a **terminal agent** on the AI2 tmax corpus: each task
boots its own container, the policy drives a single-`bash`-tool agent loop until it
submits, and the task's own verifier script produces a binary reward that feeds
GRPO/DPPO.

This is a faithful port of AI2's open-instruct tmax RL recipe
(`scripts/tmax/RL/qwen35_9b.sh` + `SWERLVanilluxSandboxEnv`) onto TorchTitan's RL
loop. It shares the sandbox / adapter / grading machinery with
[`examples/swe_r2e`](../swe_r2e/README.md) -- read that README first for the
harness architecture; this one covers what tmax changes and how to run it.

The from-zero reproduction guide for the 9B TerminalWorld run, environment lock
included, is in [`runbook/RUNBOOK.md`](runbook/RUNBOOK.md). The collaboration's
branch rules are in [`.claude/CLAUDE.md`](../../../../../.claude/CLAUDE.md).

## How a rollout works

```
Controller (one asyncio loop)
  TMaxRollouter.run_group_rollouts(generate_fn, sample, group_size=32)
    AnthropicAdapter  <- one HTTP server (127.0.0.1:SHIM_PORT) backed by generate_fn
    per sibling (32), spread over SWE_NUM_ROLLOUT_WORKERS CPU processes:
      boot sandbox from the task's public docker image (tests baked in), as root
      run_vanillux_loop(adapter, sandbox)          <- host-side agent brain
         one `bash` tool only; persistent shell (cd/export stick)
         each action -> sb.exec in the sandbox; observation head/tail-truncated
         agent submits by `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
      grade_tmax(...)  upload fixtures -> `bash /tests/test.sh` -> /logs/verifier/reward.txt
  RewardTMax -> advantage (group-centered) -> one packed TITO episode -> DPPO backward
```

Two tmax-specific points:

- **Scaffold fidelity matters.** The 9B is SFT'd under the Vanillux scaffold
  (single `bash` tool, mini-swe-agent v2.2.x prompts, submit marker). Running it
  under the swe_r2e Bash/Read/Write/Edit scaffold puts the policy off-distribution
  and starves the solve rate, so `vanillux_loop.py` / `vanillux_prompts.py` are a
  byte-faithful port -- do not "improve" the prompts.
- **Grade in place, on submit only.** The verifier inspects the container's live
  filesystem, so it runs inside the agent's own sandbox. A rollout that never
  submits scores 0 (same as the reference env).

## Layout

- `prepare_tmax_data.py` -- build the training JSONL from `allenai/tmax-15k-open-instruct`
- `prepare_tb2_data.py` -- build a Terminal-Bench 2.0 eval JSONL (89 tasks) in the same schema
- `prepare_tb2_1_data.py` -- **preferred**: the same for Terminal-Bench **2.1**, upstream's
  verified re-cut of those 89 tasks. Fixes three defects in the 2.0 script (2.1's `tasks/`
  layout, dropped binary grading fixtures, no per-task sandbox sizing) -- see its docstring
- `prepare_rts_data.py` -- build a JSONL from the Recursive-Task-Synthesis corpus
  (ships a Dockerfile per task instead of an image; records `oracle_commands`)
- `data.py` / `env.py` -- `TMaxDataset` (train/holdout split) and the token env
- `vanillux_loop.py`, `vanillux_prompts.py` -- the agent loop and its verbatim prompts
- `rollouter.py` -- sandbox boot, sibling scheduling, grading, reward stamping
- `grading.py`, `rubric.py` -- `bash /tests/test.sh` -> `reward.txt` -> `RewardTMax`
- `config_registry.py` -- the recipes (below)
- `local_smoke.py` -- sandbox boot + grade path only, no training stack
- `eval_external_model.py` -- score tasks with an external brain under the same scaffold
  (tells "task is hard" apart from "our 9B is weak")
- `hf_upload.py` -- convert one DCP checkpoint to HF format and upload it

## Recipes

| Config | What it is |
| --- | --- |
| `rl_grpo_qwen3_5_9b_tmax` | The main recipe: Qwen3.5-9B (GDN hybrid), trainer FSDP-8, vLLM-native GDN generator |
| `rl_grpo_qwen3_5_27b_tmax` | 27B GDN variant (clones the 27B swe_r2e recipe) |
| `rl_grpo_qwen3_4b_tmax` | Numerics control: dense Qwen3-4B in batch-invariant mode, so trainer/generator logprobs are bitwise-identical |
| `rl_grpo_qwen3_5_9b_tmax_tb2_eval` | Eval only: score a checkpoint on all 89 Terminal-Bench 2.0 tasks |

The 9B recipe as shipped: `group_size=32`, `num_groups_per_train_step=8`,
`max_offpolicy_steps=4`, 65536 context, 16384 per-turn tokens, `drop_zero_std=True`
(terminal tasks are sparse binary, so all-fail groups would zero the gradient),
DPPO loss (unclipped `-A*ratio` + TV trust-region mask, delta 0.1) in 32 chunks,
fp32 master params with bf16 FSDP compute, fused AdamW lr 1e-6 / betas (0.9, 0.999)
/ eps 1e-8 / no weight decay, temperature 1.0, constant LR.

## Prerequisites

1. The RL env from [`../../README.md`](../../README.md) (Monarch, TorchStore,
   renderers, vLLM, FA3).
2. GDN kernels for the Qwen3.5 hybrid: `pip install av torchvision flash-linear-attention`.
   The trainer's GDN backward needs a working `fla` build for your CUDA; if the
   tilelang-backed kernels fail to load, generation still works but the backward
   will not.
3. A sandbox provider key exported as `DAYTONA_API_KEY` (`dtn_...`), and
   `pip install daytona`.
4. `HF_TOKEN` for the dataset pulls, and the model's HF weights on disk
   (`--hf_assets_path`).

## Data

```bash
# Training corpus (15K tasks). Writes a 5-task tmax_smoke.jsonl next to --out too.
python -m torchtitan.experiments.rl.examples.tmax.prepare_tmax_data \
    --out /path/to/tmax_train.jsonl

# Terminal-Bench 2.1 eval set (89 tasks, same schema) -- prefer this over 2.0
python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_1_data \
    --out /path/to/tb2_1_eval.jsonl

# ...or from a local clone of github.com/harbor-framework/terminal-bench-2-1
python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_1_data \
    --tasks-root /path/to/terminal-bench-2-1 --out /path/to/tb2_1_eval.jsonl

# Terminal-Bench 2.0 eval set (the older cut; kept for comparability with past runs)
python -m torchtitan.experiments.rl.examples.tmax.prepare_tb2_data \
    --out /path/to/tb2_eval.jsonl
```

> **Use 2.1 for new numbers.** TB-2.1 is upstream's verified re-cut: same 89 task ids,
> but 10 rebuilt images, 4 timeout corrections and 27 tasks with fixed instructions /
> tests / solutions / environments. The 2.1 builder also ships the 7 binary grading fixtures that
> `prepare_tb2_data.py` silently dropped (those tasks could not score above 0 on the
> old file) and emits per-task `daytona_cpu/mem_gb/disk_gb`. Each row also carries its
> declared `verifier_timeout_sec` (360s to 12000s across the 89) and the rollouter
> grades on it, floored at `TMAX_EVAL_TIMEOUT_SEC`, so nothing needs setting for a
> full-suite eval. Both JSONLs feed the same
> `SWE_TB2_DATA` / `SWE_TB2_VAL_DATA` path; a 2.0 number and a 2.1 number are not
> directly comparable.

Each row is `{prompt, label, metadata{instance_id, image, workdir, tmax{test_sh,
fixtures, reward_path}}}` -- see the module docstrings for the full contract.

### Recursive-Task-Synthesis (RTS)

[`Zhongzhi1228/Recursive-Task-Synthesis`](https://huggingface.co/datasets/Zhongzhi1228/Recursive-Task-Synthesis)
(arXiv:2608.05466) is a 37,484-task synthetic terminal-agent corpus in the same
Harbor layout as TB-2.0, so the same verifier contract and `grading.py` apply. Two
things differ from the tmax corpus and drive the whole pipeline:

1. **It publishes no docker image** -- only 198 of 37,484 `task.toml` carry
   `docker_image`. Each row therefore carries its `dockerfile` text (plus
   `build_context` when the Dockerfile copies local files in) and Daytona builds it
   server-side. Daytona caches the result, so only the first sandbox per distinct
   Dockerfile pays for it: measured ~20-40s cold, ~1-2s warm.
2. **The `difficulty` field is useless** -- it is inherited from the synthesis seed
   and still reads `easy` for round-15 tasks. Use `oracle_commands` (the command
   count of the task's own `solution/solve.sh`), which the adapter records per row.

```bash
# The corpus ships as 8 tars of task trees plus a metadata parquet. Download and
# extract the shards you want (shard index == recursion round == difficulty).
for i in 0 1 2 3 4 5 6 7; do
    curl -sL -O "https://huggingface.co/datasets/Zhongzhi1228/Recursive-Task-Synthesis/resolve/main/data/tasks-0000$i.tar"
    mkdir -p s$i && tar xf tasks-0000$i.tar -C s$i &
done; wait

# One JSONL from every shard (~1 min). 36,130 of 37,484 tasks survive the filters.
python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
    $(for i in 0 1 2 3 4 5 6 7; do echo -n " --tasks-root s$i/tasks"; done) \
    --out /path/to/rts_train.jsonl
```

Rejected tasks, all measured rather than guessed: `needs_privileged` (692 -- an
init system as PID 1, the docker socket, or `--privileged`), `copy_source_missing`
(549 -- the corpus references a file it does not ship), `build_context_too_large`
(112 -- over 1 MiB of COPY sources), `verifier_writes_no_reward` (1).

**Pick the pool to match the turn budget.** A rollout capped at `SWE_MAX_TURNS`
turns cannot solve a task whose oracle needs more commands than that, and such
tasks are dead weight: they fail every sibling, so `drop_zero_std` discards the
group and the rollouts are spent for nothing. On the full corpus 47% of tasks need
more than 128 commands, which showed up as `group_all_failed_frac` pinned at 0.81.

```bash
# Only tasks whose oracle fits a 128-turn budget with 2x exploration headroom.
python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
    --tasks-root s0/tasks ... --max-oracle-commands 96 \
    --out /path/to/rts_oc96.jsonl
```

| `--max-oracle-commands` | tasks | share of corpus |
| --- | --- | --- |
| (none) | 36,130 | 96% |
| 128 | 19,107 | 53% |
| 96 | 14,593 | 40% |
| 64 | 9,453 | 26% |

**Optional: pre-warm the build cache.** Without it the first sandbox per distinct
Dockerfile pays a ~25s build, and with `group_size` siblings starting at once a
cold step is slow. Touching each task once beforehand (create + delete) populates
Daytona's cache; measured ~6900 tasks/hour at 32-way concurrency. Warm in the
order the dataset will consume (`random.Random(seed).shuffle` over the training
slice) so the warmed prefix is the one used first.

Sanity-check the sandbox and grading path before touching GPUs (no training stack
needed):

```bash
DAYTONA_API_KEY=dtn_... python torchtitan/experiments/rl/examples/tmax/local_smoke.py \
    --data torchtitan/experiments/rl/examples/tmax/tmax_smoke.jsonl --limit 2
```

## Run

The trainer takes one 8-GPU host (FSDP-8). Each generator is a separate vLLM
engine of `tensor_parallel_degree` GPUs, so the 9B recipe (generator TP-1) wants
`--num-generators 8` on a second 8-GPU host -- data-parallel engines, since one
engine's decode cannot keep hundreds of concurrent agents fed. Launching across
hosts is your own job scheduler's problem; the process to start is:

```bash
export DAYTONA_API_KEY=dtn_...
export SWE_PROMPT_DATA=/path/to/tmax_train.jsonl
export SWE_MAX_CONTEXT_LEN=65536      # match the recipe context; default 32768 truncates
export SWE_ROLLOUT_CONCURRENCY=512    # concurrently-active sandboxes (see limits below)
export SWE_NUM_ROLLOUT_WORKERS=8      # CPU processes for agent orchestration, off the controller GIL

python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 8 \
    --hf_assets_path /path/to/Qwen3.5-9B
```

Inline Terminal-Bench 2.0 as the periodic validation (instead of a tmax holdout
slice), on its own generator so training does not stall:

```bash
export SWE_TB2_VAL_DATA=/path/to/tb2_eval.jsonl   # k=5, temperature 0.7, top_p 0.95
export SWE_NUM_EVAL_GENERATORS=1                 # also flips validation to run_async
export SWE_VAL_INTERVAL=25
```

Eval an existing checkpoint only:

```bash
SWE_TB2_DATA=/path/to/tb2_eval.jsonl SWE_TB2_CKPT=/path/to/dcp_checkpoint \
python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax_tb2_eval \
    --hf_assets_path /path/to/Qwen3.5-9B
```

## Knobs worth knowing

Everything below is read from the environment in `config_registry.py` /
`rollouter.py`, so it lands in the W&B run config.

| Variable | Default | Why you'd change it |
| --- | --- | --- |
| `SWE_PROMPT_DATA` | (required) | Training JSONL |
| `SWE_MAX_CONTEXT_LEN` | 32768 | Adapter context budget; set 65536 for the full recipe |
| `SWE_ROLLOUT_CONCURRENCY` | 16 | Active sandboxes. Above `(off+1) * groups * group_size = 1280` there is no schedulable work left |
| `SWE_NUM_ROLLOUT_WORKERS` | 8 | 0 keeps everything in-process (GIL-bound) |
| `TT_DAYTONA_CREATE_CONCURRENCY` | 16 | Per-worker sandbox-create parallelism; lower it if the provider rate-limits (429) |
| `SWE_TRAIN_STEPS` | 100 | Optimizer steps |
| `SWE_GROUP_SIZE` / `SWE_NUM_GROUPS_PER_TRAIN_STEP` / `SWE_OFFPOLICY_STEPS` | 32 / 8 / 4 | The async/GRPO shape |
| `SWE_LOSS` | `dppo` | `dapo` or `grpo` for an A/B |
| `SWE_DPPO_RATIO_CAP` | 0 (off) | Truncated-IS cap; 2 tames a residual GDN train/infer logprob tail |
| `TMAX_CALL_LIMIT` | 64 | Max bash actions per episode (the reference run's `--max_steps`) |
| `TMAX_EXEC_TIMEOUT_SEC` | 120 | Per-command timeout; a foreground server can otherwise burn the budget |
| `TMAX_FORMAT_ERROR_FEEDBACK` | 0 | 0 = break on the first turn with no `bash` call (reference behavior) |
| `SWE_VAL_SAMPLES` / `SWE_VAL_INTERVAL` | 32 / 20 | Held-out validation size and cadence; `SWE_VAL_SAMPLES=0` turns it off |
| `SWE_ROLLOUT_DUMP_DIR` | unset | Per-rollout decoded completions + reward (what the model actually trained on). Any value turns it on; `launch_9b.sh` writes the files under the run's own `rollout-dumps/` |
| `SWE_ZERO_STD_DIR` | unset | Log all-pass / all-fail prompts, to feed back as `SWE_SKIP_PROMPTS` |
| `TT_ROLLOUT_LOG_LEVEL` | INFO | `DEBUG` adds one line per agent turn (prompt len, max_tokens, finish reason) |

## Reading the metrics

- **`rollout_reward/avg_train_reward` is the learning curve.** With
  `drop_zero_std=True` the trained batch is filtered to mixed-outcome groups, so
  `rollout_reward/mean` (the one on stdout) is pinned near ~0.5 by construction and
  is *not* a learning signal. This has burned us before.
- `validation/reward/mean` is avg@k and `validation/pass_at_k` is pass@k over the
  validation set; with `SWE_TB2_VAL_DATA` these are directly comparable to
  published Terminal-Bench 2.0 numbers.
- `bit_wise/logprob_diff/{abs_mean,max}` is generator-vs-trainer logprob drift.
  It is exactly 0 on prefill but not on GDN decode (chunk-parallel training vs
  recurrent decoding); a large `max` is what `SWE_DPPO_RATIO_CAP` guards against.

## Gotchas

- **Sandbox provider limits are the real throughput ceiling**, not GPUs. Creation
  is rate-limited (order 10/s), and `SWE_NUM_ROLLOUT_WORKERS *
  TT_DAYTONA_CREATE_CONCURRENCY` concurrent creates plus retries will trip it.
  Also budget vCPU/memory/disk per sandbox against your account quota.
- **Staleness kills rollouts at high concurrency.** A rollout whose policy version
  falls more than `SWE_OFFPOLICY_STEPS` behind is dropped; long terminal episodes
  plus create-throttling makes this the usual cause of a run stalling out.
- **The generator needs the GDN-specific settings** the recipe already sets:
  `gdn_prefill_backend=triton`, fp32 mamba SSM cache, and cudagraph
  `FULL_DECODE_ONLY`. Full cudagraph capture over mixed prefill/decode batches
  corrupts GDN output (gibberish, reward 0).
- **Context is two knobs.** The generator's `max_model_len` and the trainer
  batcher's packing width both move to 65536 together; raise one only and episodes
  get truncated or dropped at packing time.
- **The turn cap binds long before the context does.** At the shipped 64 turns,
  21% of RTS rollouts died on the cap with reward *exactly* 0, while mean context
  use was 25% of the budget and `truncation_rate` was 0. Raising it to 128 cut
  cap-deaths to ~4% and lifted the submit rate from 77% to 91%. Size the cap from
  the measured tokens/turn (~450 here: 128 x 450 = 57K, under the 63488 session
  budget) -- not from the context limit, and not from the paper's turn counts,
  which come from a different scaffold (see below).
- **Our scaffold is one bash command per turn.** The Vanillux prompts require
  "EXACTLY ONE bash command" per response, so turns track the reference solution's
  command count -- measured 52 mean turns where the RTS paper's Terminus-2 harness
  reports 19-20. Do not read the paper's turn numbers, or its per-task difficulty,
  as directly comparable.
- **A single eval-generator RPC failure used to cost the whole eval curve.** An
  eval generator idle between validation passes answers its next call with a gloo
  "connection closed by peer"; the guard now retries and only disables validation
  after repeated failures. Keep `validation.interval` small enough that the eval
  mesh is not idle past the Monarch mesh idle timeouts.
