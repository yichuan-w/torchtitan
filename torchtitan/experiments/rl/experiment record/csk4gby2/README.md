# Qwen3.5 9B eight GPU prompt mean experiment record

This record archives the launch parameters, resolved training configuration, and base data preparation for **csk4gby2**, the prompt_mean run initialized from the base model. Its continuation from step 90 is recorded separately as 2etspv3t. This record was reconstructed from saved artifacts.

W&B run: https://wandb.ai/yichuan_wang-uc-berkeley-electrical-engineering-computer/terminal-agent-rl/runs/csk4gby2

## Run identity and source artifacts

- Run name: `mix-wo-swesmith-prompt-mean-5gen1dp-omp1-tb21-fresh`.
- Run directory: `/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/terminal-rl/runs/tmax-9b--20260928-062907Z`.
- Launch time: 2026-09-28 06:29:07 UTC. Training code commit: `882db3e0`.
- Recorded checkout: `/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/torchtitan`. Recorded profile: `yichuan`. These are historical values; current profile defaults do not replace them, and they do not identify the person who launched the run.
- Base model: `/scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B`. Both `resumed_from` and `checkpoint_step` are null.
- Original complete W&B configuration, relative to the run directory: `trainer/wandb/run-20260928_023100-csk4gby2/files/config.yaml`.

Files in this directory:

| File | Contents |
|---|---|
| [rl.env](rl.env) | All 66 captured launch environment variables, reconstructed as KEY=VALUE |
| [launch.json](launch.json) | Contents of the original launch record |
| [resolved_config.json](resolved_config.json) | Resolved W&B configuration, including defaults absent from the environment; internal _wandb and the large model_spec are omitted |
| [base_data.config.json](base_data.config.json) | Original data build configuration selecting two sources |
| [base_data.provenance.json](base_data.provenance.json) | Prepared release provenance, build commit, input hashes, 38 excluded IDs, and 543 final IDs in run order |

The archived `rl.env` reconstructs the captured environment subset; the original file with that name was not recovered. It contains no API keys. It includes historical run and checkpoint paths, which must be changed before launching a new experiment. The saved config.yaml is authoritative for resolved configuration values.

## GPU allocation and launch parameters

| Setting | Recorded value |
|---|---|
| GPU IDs | 0,1,2,3,4,5,6,7 |
| Trainer | 2 GPU FSDP shard, SWE_DP_SHARD=2 |
| Training generation | 5 generators, each with DP=1 |
| Dedicated evaluation generation | 1 generator, DP=1 |
| Generation backend | vllm_native, prefix cache=1, default vLLM compile=1 |
| GPU memory fraction | 0.92 |
| Generator max_num_seqs | 512 |
| Rollout concurrency | **1000** |
| Rollout workers | 16 |
| Active groups | Initial 64, maximum 160 |
| Router fallback | **roundrobin** |
| CPU threads | OMP_NUM_THREADS=1, MKL_NUM_THREADS=1 |

## Learning objective and sampling

| Setting | Recorded value |
|---|---|
| Loss aggregation | **prompt_mean** |
| Loss | DPPO, Bernoulli TV threshold=0.1, ratio_cap=0 |
| Loss chunks | 8 |
| Optimizer | Fused AdamW; lr=3e-6, betas=(0.9,0.999), eps=1e-8, weight_decay=0 |
| LR schedule | warmup_steps=1, min_lr_factor=1; constant after warmup |
| Gradient clipping | max_norm=1 |
| Precision | float32 master parameters, bfloat16 compute, float32 reduction |
| Activation checkpointing | selective |
| Samples per task | group_size=12 |
| Groups per training step | 32, or 384 trajectories before filtering/skipping |
| Policy age limit | max_offpolicy_steps=4 |
| Zero variance groups | drop_zero_std_reward_groups=false |
| Zero advantage samples | skip_zero_advantage_samples=true |
| Zero advantage denominator setting | zero_advantage_tokens_in_loss_denominator=true |
| Advantage | Group centered, should_std_normalize=false |
| Reward | sparse, length_penalty=false |
| Training sampling | temperature=1, top_p=1, seed=null |
| Controller target steps | 150 |
| Checkpoint saving | Every 5 steps, keep_latest_k=3 |

prompt_mean averages token losses within each group with nonzero advantages, then averages across those groups. The implementation multiplies advantages by D/(G*T_g), where D is the loss denominator, G is the number of effective groups, and T_g is the group's effective token count. Consequently, the denominator setting alone does not imply that zero advantage groups proportionally dilute this prompt_mean gradient. A batch of 32 groups may contain fewer than 32 groups with learning signal.

The saved trainer.training.steps=10000 belongs to the nested trainer configuration; async_loop.num_training_steps=150 is the outer controller target. This target does not establish that the fresh run completed 150 steps. Its stdout records training through step 95; a later run resumed from step 90.

## Agent and evaluation

- Scaffold: TMAX_AGENT=terminus.
- Training sequence length: 65536. Agent context limit: 63488. Per-turn generation limit: 16384. Maximum turns: 120.
- Context warning fraction: 0.5. Execution timeout: 120 seconds.
- time_budget_sec=2400 and agent_budget_floor_sec=7200 are both configured; 2400 is not a universal effective deadline.
- eval_timeout_sec=600; verifier and agent budgets also depend on row-level settings.
- TB2.1 dataset: `/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/evalsets/tb2_1_tmux_f1640f428d48.jsonl`.
- Evaluation: 89 tasks, 5 trials per task, temperature=0.7, top_p=0.95, asynchronous, interval=20. Interpret evaluation curves using validation/policy_version.
- Training dataset: shuffle=true, seed=42, holdout_n=0. Evaluation dataset: shuffle=false, seed=99.
- Evolution signals=1, data hot reload=1, harder_ratio=0.9. These settings establish configuration only; they do not prove an evolution worker consumed signals or changed the dataset.

## Base data preparation

### Pinned sources and build version

Upstream dataset: `Fzz1/tb-training-mix-agentpick`, pinned to revision:

`6e3e6c8a01d84f06b9f18f61782170f4801d5330`

The original [base_data.config.json](base_data.config.json) uses seed=20260912 and selects all rows (count=null) from two clean sources. The repair and calibforge_repair splits are excluded.

| Source | Metadata | Task archive | Prepared rows |
|---|---|---|---:|
| agentpick-clean | data/clean.parquet | data/tasks-clean.tar | 341 |
| agentpick-calibforge-clean | data/calibforge_clean.parquet | data/tasks-calibforge_clean.tar | 240 |
| Total | | | 581 |

The builder is `examples/tmax/evolution/data_release.py build`, using the tmax_reaudit adapter. The prepared manifest records build code commit `9403e73c271c356acb0855b18caf574b71e7e432`.

The builder converts task packages to `{prompt,label,metadata}` rows, injects the agent runtime, and carries test scripts, fixtures, reward_path, resource requirements, and upstream provenance. Terminus requires tmux in the image. Rows are ordered by a deterministic hash of seed, corpus, and label. Data preparation and training used different code commits, both recorded here.

The following command form is reconstructed from the CLI and saved configuration; it is not a preserved shell command:

```bash
python torchtitan/experiments/rl/examples/tmax/evolution/data_release.py build \
  --config '/path/to/base_data.config.json' \
  --out '/path/to/new-prepared-output'
```

Historical prepared release:

`a33395a2b8b0af87a02a62848f4dc7f67ff99856d7a4450e4242e04e98ea0c0e`

The provenance JSON records the original location and input SHA256 hashes. Build code, upstream image tags, and package repositories can change; the configuration alone does not guarantee a byte-identical rebuild.

### Excluding SWE Smith and one TerminalWorld task

Original task sources are identified by the upstream parquet's `corpus` field. The training row's `metadata.corpus` has already been renamed to agentpick-clean and cannot identify SWE-Smith membership.

The following rows were removed from the 581 prepared rows:

1. **37 rows** with upstream `corpus == "swe_smith"`.
2. **One row** with task_id `tw_14495`.

The result contains **543 rows and 543 unique IDs**, preserving the prepared mix's relative order. Parsed row-by-row comparison confirms that the temporary mix_noswesmith.jsonl and this run's inputs/mix.jsonl have identical contents and ordering. The experiment description explicitly records “also drops tw_14495”; the surviving evidence does not explain why that task was excluded.

Counts by original corpus:

| Original source | Final tasks |
|---|---:|
| tmax | 173 |
| swe_rebench | 59 |
| terminalworld | 49 |
| rst | 22 |
| calibforge | 240 |
| Total | 543 |

The training metadata.corpus counts are agentpick-clean=303 and agentpick-calibforge-clean=240. The name wo-swesmith refers specifically to removing SWE-Smith; 59 swe_rebench tasks remain.

### Stored inputs and verification

Training data entry point:

`/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/terminal-rl/data/mix/live.jsonl`

Historical input snapshot:

`/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/terminal-rl/runs/tmax-9b--20260928-062907Z/inputs/mix.jsonl`

Mix version: 1. Rows: 543. SHA256:

`66f87fdae036ed855fe5524942a432c6f4847691c482a03078c1a99fd5bd593d`

The temporary filtered seed has SHA256 `ad7927dcc391cfbd1712d3d3382a2903b84a24039205f494cc76051295ce2e10`. Its parsed JSON rows and their order match the run snapshot, but its byte hash differs because the serialization differs. The seed hash must not be substituted for the run snapshot hash.

Use this run's inputs/mix.jsonl for exact historical inputs, rather than today's live.jsonl. This directory archives all IDs, their order, and hashes; the large task payloads remain in the original snapshot.

### Sandbox resources

Row-level daytona_cpu/daytona_mem_gb/daytona_disk_gb take precedence over rl.env defaults. All 543 rows declare resources: 403 use 1 CPU / 1 GiB RAM / 1 GiB disk, 49 use 1/1/4, 32 use 1/1/2, 22 use 1/2/6, and the remaining 37 use other sizes.

The rl.env values CPU=1, RAM=2, disk=2 are fallback settings and cannot be used to calculate the entire sandbox fleet's resource consumption.

## Differences from the continuation run

| Setting | This record: csk4gby2 | Continuation: 2etspv3t |
|---|---|---|
| Initialization | Fresh from base model | Resumed from this experiment's step 90 checkpoint |
| Rollout concurrency | 1000 | 900 |
| Fallback router | roundrobin | leastloaded |
| Launch profile | yichuan | andy |
| Launch commit | 882db3e0 | 5a85348b |
| Loss aggregation | prompt_mean | prompt_mean |

All rl.env values below come from csk4gby2.

## Complete rl.env

```bash
# Historical launch.env reconstructed from csk4gby2 launch.json.
# Archive only: paths identify the original run; do not source to launch a new run unchanged.
# This is the captured env subset, not a recovered original rl.env file.
CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
MKL_NUM_THREADS=1
OMP_NUM_THREADS=1
RL_GPUS=0,1,2,3,4,5,6,7
RL_OBSERVE_REWARDS=1
SWE_AC=selective
SWE_AGENT_TIMEOUT_FLOOR_SEC=7200
SWE_CKPT_FOLDER=/scratch/al9080/terminal-rl/ckpt/tmax-9b--20260928-062907Z
SWE_CKPT_INTERVAL=5
SWE_CKPT_KEEP=3
SWE_DATA_HOT_RELOAD=1
SWE_DISABLE_CUSTOM_ALL_REDUCE=1
SWE_DP_FALLBACK_ROUTER=roundrobin
SWE_DP_SHARD=2
SWE_DROP_ZERO_STD=0
SWE_EVAL_GEN_DP=1
SWE_EVOLUTION_HARDER_RATIO=0.9
SWE_EVOLUTION_SIGNALS=1
SWE_GEN_BACKEND=vllm_native
SWE_GEN_DP=1
SWE_GEN_PREFIX_CACHE=1
SWE_GEN_VLLM_DEFAULT_COMPILE=1
SWE_GPU_MEM_LIMIT=0.92
SWE_GROUP_SIZE=12
SWE_INITIAL_ACTIVE_GROUPS=64
SWE_LMHEAD_TF32=1
SWE_LOSS_AGG=prompt_mean
SWE_LOSS_CHUNKS=8
SWE_LR=3e-6
SWE_MAX_ACTIVE_GROUPS=160
SWE_MAX_CONTEXT_LEN=63488
SWE_MAX_NUM_SEQS=512
SWE_NUM_EVAL_GENERATORS=1
SWE_NUM_GENERATORS=5
SWE_NUM_GROUPS_PER_TRAIN_STEP=32
SWE_NUM_ROLLOUT_WORKERS=16
SWE_PROMPT_DATA=/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/terminal-rl/data/mix/live.jsonl
SWE_ROLLOUT_CONCURRENCY=1000
SWE_ROLLOUT_RECORDS=1
SWE_TB2_VAL_DATA=/scratch/gpfs/TRIDAO/al9080/terminal-rl/data/evalsets/tb2_1_tmux_f1640f428d48.jsonl
SWE_TIME_BUDGET_SEC=2400
SWE_TRAIN_STEPS=150
SWE_VAL_INTERVAL=20
SWE_VAL_SAMPLES=89
TMAX_AGENT=terminus
TMAX_CONTEXT_WARN_FRAC=0.5
TMAX_EXEC_TIMEOUT_SEC=120
TMAX_TERMINUS_MAX_TURNS=120
TMAX_TURN_MAX_TOKENS=16384
TRL_BASE=/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/terminal-rl
TRL_MODEL=/scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B
TRL_PROFILE=yichuan
TRL_RUN_DIR=/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/terminal-rl/runs/tmax-9b--20260928-062907Z
TRL_TT=/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/torchtitan
TT_DAYTONA_AUTO_DELETE_MIN=15
TT_DAYTONA_CPU=1
TT_DAYTONA_CREATE_CONCURRENCY=128
TT_DAYTONA_CREATE_RETRIES=8
TT_DAYTONA_DISK_GB=2
TT_DAYTONA_EPHEMERAL=1
TT_DAYTONA_HEARTBEAT_SEC=180
TT_DAYTONA_LABEL=new_titan_swe_r2e
TT_DAYTONA_MAX_MEM_GB=8
TT_DAYTONA_MEM_GB=2
TT_DAYTONA_TTL_MIN=300
WANDB_PROJECT=terminal-agent-rl
```
