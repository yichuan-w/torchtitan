# TMax 9B RL on MAST

This runbook launches the Qwen3.5-9B TMax terminal-agent RL recipe from
`yichuan/qwen35-port-cotrain`, monitors the MAST job, and syncs its offline W&B
run to `meta.wandb.io`.

The command below is the current production candidate: DPPO, fp32 master
parameters and AdamW states, MSL-style sliding-prefix Windowed FIFO, 512 active
rollouts, and no inline held-out validation.

## 1. Prerequisites and shell setup

```bash
cd /home/yichuan/torchtitan-claude-code-harness-cotrain

source /opt/miniconda3/etc/profile.d/conda.sh
conda activate rlmast

git branch --show-current
# Expected: yichuan/qwen35-port-cotrain
git rev-parse HEAD
git status --short
```

Prefer a reviewed, committed checkout for a long run. A reinstall does package
uncommitted TorchTitan edits, but the recorded commit hash alone then cannot
reproduce the job.

The model and training data are already staged:

```text
/mnt/torchtrain_datasets/tree/qwen3_5/Qwen3.5-9B
/mnt/torchtrain_datasets/tree/yichuan/tmax_data/tmax_train.jsonl
```

Load a Daytona key without printing the complete secret:

```bash
if [[ -f ~/.daytona_env ]]; then
    source ~/.daytona_env
elif [[ -f ~/.dtn_key ]]; then
    export DAYTONA_API_KEY="$(< ~/.dtn_key)"
fi

: "${DAYTONA_API_KEY:?Set DAYTONA_API_KEY before submitting}"
printf 'DAYTONA_API_KEY=%s...\n' "${DAYTONA_API_KEY:0:8}"
```

`submit_swe.sh` exits immediately if the key is missing. `mast.py` forwards the
key to the controller, which creates and grades the Daytona sandboxes.

## 2. Recommended 512-concurrency launch

Use a new dump directory for every new run. Reusing a directory can trigger an
unintended checkpoint resume, including incompatibility with older bf16
optimizer-state checkpoints.

```bash
cd /home/yichuan/torchtitan-claude-code-harness-cotrain
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate rlmast

RUN_TAG="q35_9b_tmax_w20_dppo512_$(date +%Y%m%d_%H%M%S)"
DUMP_DIR="/mnt/torchtrain_datasets/tree/yichuan/mast_runs/${RUN_TAG}"

SWE_GEN_BACKEND=torchtitan_wrapper \
SWE_LOSS=dppo \
SWE_TIME_BUDGET_SEC=1200 \
SWE_VAL_SAMPLES=0 \
SWE_DISABLE_SHUFFLE=0 \
SWE_SELECTION_WINDOW_GROUPS=20 \
SWE_MAX_BYPASS_GROUPS=off \
SWE_STRICT_FIFO=0 \
SWE_ROLLOUT_CONCURRENCY=512 \
SWE_NUM_ROLLOUT_WORKERS=16 \
SWE_MAX_NUM_SEQS=32 \
TT_DAYTONA_DISK_GB=10 \
TT_DAYTONA_CREATE_CONCURRENCY=8 \
TT_DAYTONA_HEARTBEAT_SEC=180 \
TT_DAYTONA_RPC_RETRIES=2 \
SWE_NUM_GENERATORS=6 \
SWE_TRAIN_STEPS=100 \
SWE_DUMP_DIR="${DUMP_DIR}" \
bash mast_rl/submit_swe_tmax_9b.sh
```

Do not add `--no-reinstall` to the first launch after any TorchTitan code
change. The launcher installs this checkout into `rlmast`, packs that environment,
and submits it. The `mast_rl/` workspace is shipped separately, but edits under
`torchtitan/` only reach MAST through that reinstall.

Use the faster form only when the installed package already contains the exact
code being submitted and only environment/config values changed:

```bash
# In the complete launch block above, replace only its final line with:
bash mast_rl/submit_swe_tmax_9b.sh --no-reinstall
```

## 3. Effective baseline

The important resolved values are:

| Setting | Value |
| --- | --- |
| Loss | DPPO, TV threshold `0.1`, ratio cap disabled |
| Optimizer | fused AdamW, lr `1e-6`, betas `(0.9, 0.999)`, eps `1e-8`, weight decay `0` |
| Precision | fp32 master parameters and optimizer states, bf16 compute, fp32 reduction |
| Context | model/batcher `65536`, agent session budget `63488`, per turn `16384` |
| Training batch | 8 retained prompt groups per step, 32 siblings per group |
| Zero-std groups | all-failed and all-solved groups are dropped from gradients |
| Async window | off-policy 4, therefore 40 active groups and at most 1280 schedulable siblings |
| Rollout pool | 16 worker processes; global concurrency 512 becomes 32 sibling slots per worker |
| Generator batch ceiling | `max_num_seqs=32` per engine; this is a ceiling, not reserved KV capacity |
| Generators | 6 generator roles, each exposing 8 TP-1 engines |
| Checkpoints | every 20 optimizer steps, plus final model export |
| Validation | disabled by `SWE_VAL_SAMPLES=0` |

The scheduler is work-conserving at sibling granularity. A completed sibling
immediately releases one slot; the next low-ID waiting sibling can start without
waiting for the previous group to finish all 32 siblings. Group aggregation still
waits for all siblings before classifying the group.

`TT_DAYTONA_CREATE_CONCURRENCY=8` is per rollout worker, not global. With 16
workers the nominal create ceiling is 128 concurrent create calls. It limits
sandbox boot bursts; `SWE_ROLLOUT_CONCURRENCY` limits active rollouts.

The MAST job name still contains `rl_grpo_qwen3_5_9b_tmax` for historical config
naming. It does not mean the selected loss is GRPO. Confirm the W&B config under
`trainer.loss.loss_fn`; DPPO has `divergence_threshold` and `divergence_type: tv`.

## 4. Shuffle, FIFO, and validation variants

The historical baseline uses shuffled prompts and the work-conserving take-any
buffer:

```text
SWE_DISABLE_SHUFFLE=0
SWE_SELECTION_WINDOW_GROUPS=<unset>
SWE_MAX_BYPASS_GROUPS=off
SWE_STRICT_FIFO=0
```

The recommended MSL-style Windowed FIFO candidate keeps every other setting
unchanged and sets:

```text
SWE_SELECTION_WINDOW_GROUPS=20
SWE_MAX_BYPASS_GROUPS=off
SWE_STRICT_FIFO=0
```

Each selection scans the first 20 entries in the current active admission map.
Removing any selected group shifts the next entry into that prefix, so `W=20`
limits instantaneous look-ahead but not lifetime bypass count. A replay of the
first 20 steps of the live W16 trace reproduced all 308 choices and estimated
that W20 would reduce window-blocked time by 59% while still changing 41% of
selection positions relative to take-any. W24 reduced blocking further but
changed only 11% of positions, making it too close to greedy selection for the
next trial. Replay is a scheduler comparison, not evidence of reward gain; use
fixed-task validation for that. The local launcher defaults to `W=20` and
`max_bypass_groups=off`.

`SWE_MAX_BYPASS_GROUPS=32` enables an experimental MSL-inspired global stall at
four step-equivalents of direct bypass. Do not enable it for an unattended run
until the controller has a hard group-RPC timeout or reliable cancellation
handshake: a crashed worker or hung RPC can otherwise leave a group `INFLIGHT`
and freeze selection permanently. MSL's `32 * E = 256` default is also not
appropriate with TMax's four-step policy cap. Use the nonempty `off` sentinel so
the setting is visible after MAST environment forwarding.

For an index-by-index Open-Instruct comparison, set
`SWE_DISABLE_SHUFFLE=1`. This is a diagnostic run and changes the sampled task
distribution. Strict FIFO is also diagnostic: set
`SWE_SELECTION_WINDOW_GROUPS=`, `SWE_MAX_BYPASS_GROUPS=off`, and
`SWE_STRICT_FIFO=1`; this removes completion-order selection but reintroduces
head-of-line straggler stalls.

`SWE_INCLUDE_PROMPTS=/mnt/.../instance_ids.txt` restricts only the training
split to an explicit curriculum whitelist. Keep `SWE_PROMPT_DATA` pointed at
the original full JSONL: the dataset applies the fixed file-order holdout split
before the whitelist, preserving the original validation cohort. The include
file accepts one bare instance ID per line or JSONL rows with `instance_id`.
Missing, empty, and out-of-split whitelists fail closed. `SWE_SKIP_PROMPTS`, if
set, is applied after the whitelist.

The default Open-Instruct geometry is 8 prompt groups with 32 siblings each.
Set `SWE_NUM_GROUPS_PER_TRAIN_STEP=32` and `SWE_GROUP_SIZE=8` for the historical
BS32/SPP8 schedule. `SWE_DROP_ZERO_STD=0` retains all-solved and all-failed
groups in the attempted batch composition; their centered advantages remain
zero.

`SWE_VAL_SAMPLES=0` disables fixed held-out validation. To enable the recipe's
held-out pass, set it to `32`; validation then runs at the start, end, and every
20 steps and adds substantial Daytona wall time. Validation currently uses the
controller-local rollouter rather than the worker gates, so a periodic pass may
temporarily create up to 32 sandboxes in addition to the training concurrency.

The launcher defaults every TMax sandbox to a 10 GiB root disk. A known heavier
task can override this by adding a positive integer to its JSONL metadata, for
example `"daytona_disk_gb": 20`. Tasks without that field use
`TT_DAYTONA_DISK_GB`. This changes only newly created sandboxes.

The 180-second Daytona heartbeat prevents the provider's 10-minute idle timer
from stopping a sandbox while the controller waits on an in-process model turn.
`TT_DAYTONA_RPC_RETRIES=2` applies only to idempotent command-log reads, identical
file uploads, and sandbox deletion. Transient command-submission failures have
up to five attempts within the existing command deadline. Every attempt uses
the same command UUID and an atomic claim in the sandbox, so duplicate launchers
do not execute the command body again. Retries use fresh Daytona sessions because
a detached command can outlive its original session. Recovery reads the command's
result files, even when Daytona returns no command ID. Claims persist until
sandbox deletion; losing both the launcher and its result does not trigger a
fresh execution.

Unrecovered infrastructure failures remain in the diagnostic records, but their
training rewards become NaN. They are excluded from advantage estimation and
training samples. Valid siblings remain eligible under the usual group filters.

Terminal sandbox failures emit immediate `[sandbox_issue]` JSON records in the
controller log. Recovered polling and retry events are kept out of the hot log
path and folded into the per-rollout summary. Records include the task instance,
group and sibling rollout IDs, full sandbox/session/command IDs, image, effective
disk allocation, retry attempt, whether recovery succeeded, and a bounded error
message. Affected rollouts also write `group=<G>_rollout=<R>.sandbox.json` beside
the human-readable rollout trace. Use these W&B metrics for aggregate health:

```text
rollout/sandbox_issue_frac
rollout/sandbox_issue_events_mean
rollout/sandbox_disk_full_frac
rollout/sandbox_disk_full_events_mean
rollout/sandbox_transport_issue_frac
rollout/sandbox_transport_issue_events_mean
rollout/sandbox_provision_issue_frac
rollout/sandbox_timeout_frac
rollout/infra_failed_frac
rollout/infra_failed_group_frac
training_sample_builder/num_untrainable_rollouts
```

The disk metrics distinguish a Daytona session-creation ENOSPC from an in-sandbox
command that exits nonzero with `Errno 28`. A successful command whose output
merely contains the same text is not classified as disk exhaustion.

## 5. Trying concurrency 1024

As of 2026-07-21, the shared Daytona account limits are 5000 vCPU, 20000 GiB
memory, and 25000 GiB storage. Each TMax sandbox requests 2 vCPU, 4 GiB memory,
and 10 GiB storage. Raise disk-heavy tasks through `metadata.daytona_disk_gb`
rather than increasing every sandbox allocation.

| Concurrency | vCPU | Memory | Storage |
| ---: | ---: | ---: | ---: |
| 512 | 1024 | 2048 GiB | 5120 GiB |
| 1024 | 2048 | 4096 GiB | 10240 GiB |

1024 fits the account limits in isolation, but the account is shared. Check live
Daytona usage immediately before launch. The 16-worker production split cannot
use concurrency 1024 with the default 40-group active cap: keeping every worker
gate supplied would require 48 active groups. For a controlled 1024 comparison,
change these values together:

```text
SWE_ROLLOUT_CONCURRENCY=1024
SWE_NUM_ROLLOUT_WORKERS=8
TT_DAYTONA_CREATE_CONCURRENCY=16
```

With eight workers, 1024 means 128 sibling slots per worker and the nominal
sandbox-create ceiling remains 128. The recipe automatically starts
all 40 active groups at this concurrency, leaving one queued 32-sibling group per
worker to refill completed slots; do not force `SWE_INITIAL_ACTIVE_GROUPS=32`.
Concurrency above 1280 cannot do useful work with the default 40-group active
window. With eight workers, the largest concurrency that also leaves at least
one queued sibling behind every gate is 1272. The launcher rejects worker and
concurrency combinations whose even gate split would need more than the active
group window; reduce either setting or increase `SWE_MAX_ACTIVE_GROUPS`.
To retain 16 rollout workers, use concurrency 1008 (63 slots per worker) instead.

Do not assume 1024 is faster. Compare Daytona create failures/rate limits,
generator inflight and queue-time metrics, rollout-worker CPU load, and
`timing/step/wait_for_training_batch/mean`. Higher concurrency can amplify early
completion bias and service contention even when the resource quota fits.

## 6. Capture the job ID

Submission ends with output similar to:

```text
submitted: mast_conda:///torchtitan-rl-rl_grpo_qwen3_5_9b_tmax-<hash>
https://www.internalfb.com/intern/mast/job/torchtitan-rl-rl_grpo_qwen3_5_9b_tmax-<hash>
```

Record the full job name and the dump path used above:

```bash
JOB="torchtitan-rl-rl_grpo_qwen3_5_9b_tmax-REPLACE_WITH_HASH"
MF_DUMP="${DUMP_DIR#/mnt/}"
```

Cold start through the first train step can take tens of minutes. The Daytona
preflight result is written to `${MF_DUMP}/daytona_preflight.txt`.

## 7. Monitor MAST and training

Job health:

```bash
mast get-status "${JOB}" | grep -iE 'state .enum.|numRestarts'
```

The expected top-level state is `RUNNING` and `numRestarts` should remain zero.
For machine-readable status:

```bash
mast --output json get-status "${JOB}" | \
    jq -r '.data | "state=\(.state) restarts=\(.numRestarts) attempt=\(.latestAttempt.attemptIndex)"'
```

Latest completed optimizer steps:

```bash
mast get-logs "${JOB}" --file-path stdout --regex 'Train . Step' | \
    grep 'Train . Step' | tail -5
```

OOM and fatal-error scan:

```bash
mast get-logs "${JOB}" --file-path stderr | \
    grep -aiE 'out of memory|traceback|fatal|nccl.*error' | tail -50
```

MAST log output is a window, not durable history. The controller mirror in
Manifold is more complete. Re-download it to refresh:

```bash
manifold get "${MF_DUMP}/controller_console.log" /tmp/tmax_controller.log \
    --overwrite >/dev/null 2>&1
tail -100 /tmp/tmax_controller.log
```

Checkpoint saves at steps 20, 40, 60, and so on can make a step look stalled.
Inspect what has landed before diagnosing a hang:

```bash
manifold ls "${MF_DUMP}/checkpoint"
```

`run.sh` enables human-readable host-loop traces and creates these artifacts:

```text
${MF_DUMP}/rollout_dumps/
${MF_DUMP}/rollout_samples.jsonl
```

`trajectories/` is also created for agent modes that emit their own trajectory
files, but it may be empty for `host_loop`.

## 8. Sync offline W&B

The MAST controller writes a growing offline W&B file into the dump directory.
Discover the newest offline run, download only its `.wandb` file, and sync it:

```bash
WB="${MF_DUMP}/wandb"
OFFLINE_RUN="$(manifold ls "${WB}" 2>/dev/null | \
    grep -aoE 'offline-run-[0-9_]+-[a-z0-9]+' | sort | tail -1)"
RUN_ID="${OFFLINE_RUN##*-}"
LOCAL_WANDB="/tmp/run-${RUN_ID}.wandb"

test -n "${OFFLINE_RUN}" || { echo 'offline W&B run not found' >&2; exit 1; }

manifold get "${WB}/${OFFLINE_RUN}/run-${RUN_ID}.wandb" \
    "${LOCAL_WANDB}" --overwrite

WANDB_BASE_URL=https://meta.wandb.io wandb sync "${LOCAL_WANDB}"
```

Repeated downloads and syncs update the same run ID. The command prints the run
URL, normally:

```text
https://meta.wandb.io/yichuan/torchtitan/runs/<run-id>
```

Syncing a snapshot of an offline run can make the web UI show `finished` while
the MAST job is still running. Use MAST status and controller logs for liveness;
W&B may lag by one or more steps.

## 9. Reward metrics

Use these metrics when comparing against Open-Instruct:

| Titan metric | Meaning |
| --- | --- |
| `rollout_reward/avg_train_reward/mean` | Attempted training-batch reward before zero-std filtering; counterpart of OI `val/avg_group_performance_pre_filter` |
| `rollout_reward/_mean` | Reward aggregate printed in the trainer step log |
| `rollout_reward/group_all_failed_frac/mean` | Fraction of attempted groups with 0/32 solved |
| `rollout_reward/group_all_solved_frac/mean` | Fraction of attempted groups with 32/32 solved |
| `rollout_reward/group_zero_std_frac/mean` | Combined all-failed and all-solved fraction |
| `training_sample_builder/num_groups_dropped_zero_std/sum` | Number of zero-std groups dropped from that gradient batch |
| `train/grad_norm/mean` | Gradient norm after batch construction |

Titan does not emit the exact key `val/avg_group_performance_pre_filter`. Do not
compare OI's metric against held-out validation or kept-only partial-group reward.

## 10. Common failure modes

1. **Code change submitted with `--no-reinstall`:** MAST runs the previously
   installed TorchTitan package. Resubmit without the flag.
2. **Reused dump directory:** checkpoint auto-resume can collide with a new run or
   an optimizer-state dtype change. Use a new timestamped directory.
3. **Missing Daytona key:** submit exits before creating the MAST job.
4. **W&B says `finished`:** this is an offline-sync artifact; check MAST health.
5. **Job name says GRPO:** this is historical naming; inspect the W&B loss config.
6. **Step appears stuck at a multiple of 20:** first check checkpoint output and
   trainer logs; full checkpoint saves are expected to be slower.
7. **Environment override has no effect:** only variables listed in `mast_rl/mast.py`
   are forwarded to MAST roles. Verify the resolved W&B config after launch.
   In particular, the remote defaults for command and verifier timeouts are
   currently 120 and 600 seconds; local `TMAX_EXEC_TIMEOUT_SEC` and
   `TMAX_EVAL_TIMEOUT_SEC` overrides are not forwarded by `mast.py` yet.

## 11. Known-good reference

The first fp32-AdamW plus work-conserving-scheduler run used:

```text
MAST job: torchtitan-rl-rl_grpo_qwen3_5_9b_tmax-89db01
Dump:     torchtrain_datasets/tree/yichuan/mast_runs/q35_9b_tmax_asyncgate_dppo512_20260721_021715
W&B:      https://meta.wandb.io/yichuan/torchtitan/runs/5qj78pte
```

Use it as an operational reference, not as a dump directory to resume or reuse.
