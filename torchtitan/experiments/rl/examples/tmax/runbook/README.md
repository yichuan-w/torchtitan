# Run TerminalWorld + TMax RL

Use this entry point for Qwen3.5-9B training, Terminal-Bench 2.1 (TB 2.1) evaluation, and the Codex
evolution loop on a provisioned Linux B300 host with systemd user services.
The example reserves six GPUs: two for training, three for rollouts, and one
for evaluation. It starts two evolution workers.

## Already have a prepared experiment

Create one configuration file from [run.env.example](run.env.example), outside
the checkout. Set the experiment, model, venv, TB JSONL, and synthesis credential
paths once. Select your own registered profile from `profiles/`; its `TRL_TT`
must name your checkout. A new user needs a separate profile and experiment root.
Do not reuse another person's profile or experiment.

From the repository root:

```bash
bash torchtitan/experiments/rl/examples/tmax/runbook/start.sh /absolute/path/run.env --dry-run
bash torchtitan/experiments/rl/examples/tmax/runbook/start.sh /absolute/path/run.env
```

The first command records a launch manifest and creates a dry-run directory;
it starts no trainer, evaluation, or evolution process. The second starts the
trainer and evolution as systemd user services, so closing the terminal does
not stop them. It refuses to replace services already running for that root.

TB evaluation is explicitly **89 tasks, 5 attempts per task, every 20 steps**.
The bare `launch_9b.sh` recipe disables evaluation by default; use this entry
point and configuration for the combined experiment.

Daytona credentials are read from `~/.config/daytona/env`. The synthesis
credential file is named by `SYNTH_ENV_FILE`; the root's `bin/` must provide
authenticated `codex` and `jq`. Keep credentials outside the repository.

## When to prepare data

**Merging code does not require rerunning every preparer.**

| Situation | Action |
| --- | --- |
| Code changed; prepared data is unchanged | Pull your checkout, then start a new run from the existing root. |
| HF data or the row format changed | Prepare a new data version before the next run. |
| Starting a separate experiment | Create a new root from a seed mix. |
| Resuming training | Set `RL_RESUME_FROM` to the previous run directory in the config. Keep its experiment root. |

The preparers are alternatives for different inputs, not a chain:

| Input | Tool | Output |
| --- | --- | --- |
| TerminalWorld / RTS task packages | `prepare_rts_data.py` | Rows for that corpus; quality filtering and sizing are separate. |
| Published TMax reaudit | `prepare_tmax_reaudit_data.py` | Pinned, checked TMax rows, including integrity hooks and protected lists. |
| Original AI2 TMax corpus | `prepare_tmax_data.py` | Original-corpus rows; this is not the reaudit input. |
| Extracted TW + TMax reaudit packages | `evolution/build_mix_v2.py` | Combined seed mix and input manifest. It packs both corpora directly, so no preceding `prepare_rts_data.py` is needed. |
| Terminal-Bench 2.1 | `prepare_tb2_1_data.py` | Separate evaluation JSONL. Never concatenate it into training data. |

The combined path is:

```text
download pinned task packages → extract/check → build_mix_v2 → apply audited sizes → new_root → start
```

## First experiment from prepared source packages

This step assumes the model, locked training environment, credentials, and
source packages are already provisioned. For a new machine, use the environment
instructions in [RUNBOOK.md](RUNBOOK.md) once. The data source root must contain
`data/sources/tw-extract/{tasks,metadata}`, `data/sources/tmax-extract/tasks`,
`data/sources/tmax-clean/splits/{reaudit,reaudit_full}.parquet`, and
`results/disk_full.jsonl`. Use a fresh extraction directory when the dataset
revision changes; mixing old and new task files is invalid.

Set these preparation-only paths in your run config:

```bash
SEED_SOURCE_ROOT=/absolute/path/to/prepared-sources
AUDITED_SIZING=/absolute/path/to/sizing.jsonl
SEED_MIX=/absolute/path/to/new-seed.jsonl
AGENT_BIN=/absolute/path/to/bin
```

`AUDITED_SIZING` is the output of `evolution/derive_sizing.py`, including the
oracle and agent measurements. Keep the published train-ready ID list; do not
replace it with all tasks that happen to pack. The source packages are retained
because evolution needs the reference solutions as well as the training rows.

From the repository root, prepare the new experiment once:

```bash
set -a
. /absolute/path/run.env
set +a
export TRL_TT="$PWD" PYTHONPATH="$PWD"
TMAX="$TRL_TT/torchtitan/experiments/rl/examples/tmax"
PY="$TRL_VENV/bin/python"

TRL_BASE="$SEED_SOURCE_ROOT" "$PY" "$TMAX/evolution/build_mix_v2.py" --out "$SEED_MIX" --apply
"$PY" "$TMAX/evolution/apply_audit_sizing.py" --sizing "$AUDITED_SIZING" --mix "$SEED_MIX" --include-holdout --apply
"$PY" "$TMAX/new_root.py" --base "$TRL_BASE" --mix "$SEED_MIX" \
  --profile "$TRL_PROFILE" --bin "$AGENT_BIN" \
  --sources "$SEED_SOURCE_ROOT/data/sources/tw-extract" \
            "$SEED_SOURCE_ROOT/data/sources/tmax-extract" \
            "$SEED_SOURCE_ROOT/data/sources/tmax-clean" \
  --purpose "TerminalWorld + TMax with TB 2.1 evaluation and evolution"
cp "$AUDITED_SIZING" "$TRL_BASE/data/mix/seed-sizing.jsonl"
bash "$TMAX/runbook/start.sh" /absolute/path/run.env --dry-run
```

Inspect the preparation counts and manifest before starting: unresolved task
IDs or missing measurement files must be resolved, not accepted as a smaller
dataset. Sizing above applies to a fresh seed before any run, including its
held-out tail; it does not resize an existing experiment's validation data.

## See results and stop

Under the configured experiment root:

| What | Where |
| --- | --- |
| Training and inline evaluation log | `runs/latest/stdout.log` |
| Exact code, configuration, and input | `runs/latest/launch.json`, `runs/latest/inputs/` |
| Rollout traces | `runs/latest/rollouts/` |
| Evolution log and decisions | `evolution/loop.log`, `evolution/ledger.jsonl` |
| Data versions | `data/mix/history/` |

With the run config loaded, inspect or stop the two services:

```bash
systemctl --user status "train-$(basename "$TRL_BASE")" "evolve-$(basename "$TRL_BASE")"
systemctl --user stop "train-$(basename "$TRL_BASE")" "evolve-$(basename "$TRL_BASE")"
```

Evaluation is part of the trainer process. Stopping the trainer stops its
evaluation too. The longer [RUNBOOK.md](RUNBOOK.md) covers tuning and debugging.

## Watch accuracy while tasks evolve

The runbook starts one W&B companion named `accuracy-<training-run-id>`, in the
training run's project. Open its single `Accuracy` panel. It updates every five
minutes without restarting training.

The default view compares the first and latest results on the same unchanged
tasks. The slope and percentage-point label show the change directly. Both ends
use the same task IDs and sample hashes; infrastructure failures and missing
scores are excluded. The participating tasks can change between updates, so
compare the two points within a snapshot. These are training tasks; fixed TB
pass@5 remains the measure of generalization.

To inspect one task and its rewrites, enter its task ID in the same panel and click Show.
Each point names its task revision. A change across revisions also reflects a
changed problem. The policy label is the version at group start, not an exact
single-policy evaluation. Click All unchanged tasks to return to the overview.

The observer is enabled by `RL_OBSERVE_REWARDS=1` in the shared defaults. Set it
to 0 in the run config to disable it. Logs, input caches, snapshots, and the W&B
URL (`wandb.json`) stay under `runs/<run>/observer/`. It stops after W&B reports
that the training run ended.

For an existing run, attach the observer from your checkout with an output
directory you own:

```bash
"$TRL_VENV/bin/python" torchtitan/experiments/rl/examples/tmax/evolution/observe_rewards.py \
  --root /absolute/path/to/experiment --run exact-run-directory-name \
  --out /absolute/path/to/observer-output --upload --watch
```

Keep that output directory when restarting to reuse the cached inputs. Omit
`--upload --watch` for one local snapshot. Stop only the observer with
`systemctl --user stop observe-<run-directory-name>`.
