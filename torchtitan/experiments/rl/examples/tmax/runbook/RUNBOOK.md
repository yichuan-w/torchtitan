# TerminalWorld 9B RL — reproduction runbook

How to bring up the terminal-agent RL stack from zero on a fresh 8x B300 host.

Everything here is the configuration that ran on `della-tridao`, copied from the
run's own dump rather than retyped: paths, versions and numbers are the real
ones. Where your machine differs (paths, credentials) substitute your own.

**Code version.** Everything below is `yichuan-w/torchtitan`, branch
`yichuan/qwen35-port-cotrain` -- the collaboration's single canonical branch.
The reference run described here executed commit
`f7747008d6c63911e40bc5d2b403b6e6246fc47c` (since merged into the canonical
branch); reproducing on the current branch head gets that run plus later
stability fixes (orphaned-request aborts, sandbox boot allowance, ephemeral
TTL), all default-compatible.

**What is in this directory**

| file | what it is |
|---|---|
| `RUNBOOK.md` | this document |
| `requirements.lock.txt` | `pip freeze` of the venv that ran the job, 300 packages |
| `pyproject.toml` | the same set as a uv project, with the override block explained |
| `uv.lock` | a real, resolving lock (`uv lock --check` passes) |
| `rltrain.env` | every tuned value the live run had set |
| `launch_9b.sh` | the launcher, runs as-is |

**How to read the confidence markers.** Statements are either *verified* (read
off the running system or executed here) or *not verified* (marked inline, and
collected in the last section). Nothing in between is guessed at silently.

---

## 1. Hardware and prerequisites

Verified, from the reference host:

| | value |
|---|---|
| GPUs | 8x NVIDIA B300 SXM6, 275040 MiB (268.6 GiB) each |
| Driver | 610.57.04 |
| CUDA wheels | cu130 |
| OS | RHEL 9.8, glibc 2.34 |
| Python | CPython 3.12.13 |
| Host RAM | ~1.2 TiB (vLLM reported 1203.14 GiB available) |
| Filesystem | GPFS |
| Network | direct outbound internet from the training host |

The run used **five** of the eight GPUs: `RL_GPUS=0,1,2,3,4`, split
`SWE_DP_SHARD=2` (trainer) + `SWE_GEN_DP=3` (generator engines). That is five
rather than eight because the box is shared with another user, not because five
is the right answer. Those two variables are where the split is set, and they
must sum to the number of GPUs in `RL_GPUS`.

Non-obvious requirements, all verified:

- **Outbound internet from the training host.** Not for the model — for Daytona.
  Every rollout creates a cloud sandbox over the network. A compute node with no
  egress cannot run this at all.
- **A Daytona account and `DAYTONA_API_KEY`.** There is no local, Docker, or
  offline sandbox backend. `TT_SANDBOX_BACKEND` exists but raises on any value
  other than `daytona` — `"unknown sandbox backend {backend!r}; only 'daytona'
  is bundled"`. Without a key the job boots and then fails every rollout.
- **Disk for checkpoints.** One 9B checkpoint is **98 GiB** (measured on the
  live run's `step-5`). The reference config keeps 24 of them
  (`SWE_CKPT_KEEP=8`, saved every 5 steps), which is ~0.8 TiB. The code default
  is 3. Size the filesystem or lower the number — the failure mode when you run
  out is `OSError: [Errno 122] Disk quota exceeded` partway through a save,
  which also leaves a truncated checkpoint behind.
- **~19 GiB for the model** plus room for the task corpus.
- **A W&B account**, or set `WANDB_MODE=offline`. The launcher explicitly
  unsets `WANDB_MODE` to force online logging.

---

## 2. The simplest 9B command

Four steps from a clean machine. This is the whole thing.

```bash
# 0. Pick a profile. profiles/<name>.env holds the checkout (TRL_TT) and the
#    data root (TRL_BASE); two people launch on this box from one account, each
#    from their own. The model and venv are shared.
export TRL_PROFILE=andy                                       # or yichuan
set -a; . profiles/$TRL_PROFILE.env; set +a                   # for the steps below
export TRL_MODEL=/scratch/gpfs/TRIDAO/al9080/models/Qwen3.5-9B
export TRL_VENV=/scratch/gpfs/TRIDAO/al9080/titan-rl         # virtualenv

# 1. Code, at the exact commit that ran.
git clone -b yichuan/qwen35-port-cotrain https://github.com/yichuan-w/torchtitan "$TRL_TT"
git -C "$TRL_TT" checkout f7747008d6c63911e40bc5d2b403b6e6246fc47c

# 2. Environment. torchtitan is NOT installed -- it goes on PYTHONPATH.
uv venv --python 3.12 "$TRL_VENV"
uv pip install --python "$TRL_VENV/bin/python" \
    -r "$TRL_TT/torchtitan/experiments/rl/examples/tmax/runbook/requirements.lock.txt" \
    --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu130

# 3. Model and data.
hf download Qwen/Qwen3.5-9B --local-dir "$TRL_MODEL"
mkdir -p "$TRL_BASE/data/mix"
# ... build the task JSONL, see section 4 ...

# 4. Credentials, then launch.
export DAYTONA_API_KEY=...        # your key; never commit it
cd "$TRL_TT/torchtitan/experiments/rl/examples/tmax/runbook"
./launch_9b.sh
```

`launch_9b.sh` reads `rltrain.env` from the same directory, refuses a GPU layout
the allocator cannot honour, and execs:

```
python -m torchtitan.experiments.rl.train \
    --module torchtitan.experiments.rl.examples.tmax \
    --config rl_grpo_qwen3_5_9b_tmax \
    --num-generators 1 \
    --hf_assets_path $TRL_MODEL
```

Anything exported before the script wins over `rltrain.env`, so a one-off change
needs no edit: `RL_DATA=/path/other.jsonl ./launch_9b.sh`.

To run it under systemd the way we do, `EnvironmentFile=` the same `rltrain.env`
and `ExecStart=` the same script. Our unit uses `Restart=always` with
`RestartSec=30`: a trainer NCCL abort exits with code 0, which `on-failure` read
as a clean stop and left the service down. With `always` a crash resumes from
the latest checkpoint, and reaching the step limit restarts too -- stop the unit
explicitly when a run is meant to end.

### What the first ten minutes look like

Verified against the reference run's log. These lines appear in this order:

1. `Monarch runtime timeouts configured: {'message_delivery_timeout': '300s', ...}`
2. W&B login and `View run at https://wandb.ai/...`
3. **The GPU accounting line — check this one first:**
   ```
   1 generator(s) + 0 eval generator(s) * 3 GPUs + 2 trainer GPUs = 5 total
   ```
   If that total is not the number of GPUs you meant to use, stop now.
4. `Building device mesh with parallelism: pp=1, dp_replicate=1, dp_shard=2, cp=1, tp=1, ep=1`
5. `Applied FullAC activation checkpointing to the model`
6. `Spawned 16 RolloutWorker(s) (requested 16), concurrency by worker=[192, ...] (global target 3072)`
   -- that line is from the reference boot; `rltrain.env` has since halved
   `SWE_ROLLOUT_CONCURRENCY` to 1536 to match the ~768 LLM decode slots, so
   expect `global target 1536`.
7. `Optimizer AdamW (model_part=0): 523 params ... {'fused': True, 'lr': 3e-06, ...}`
8. `Loading HF safetensors from --model.hf_assets_path: <your model dir>`
9. Trainer: `Finished loading the checkpoint in 54.49 seconds.`
10. Generators: `Initializing a V1 LLM engine ... max_seq_len=65536, data_parallel_size=3`
11. `Loading safetensors checkpoint shards: 100% Completed | 4/4`
12. vLLM memory report, then `Available KV cache memory: 204.61 GiB` and
    `Maximum concurrency for 65,536 tokens per request: 96.93x`
13. Rollouts begin. Group completions look like:
    ```
    [buffer] complete group_id=241 solved=13/16 class=partial_solve -> TRAINABLE target_ver=6 cur_ver=4
    ```
14. The first optimizer step:
    ```
    [trainer_loop] step 1: begin; trainer.sync_log_step
    [trainer_loop] step 1: awaiting training batch
    [trainer_loop] step 1: forward_backward microbatch 1/122
    ...
    [trainer_loop] step 1: forward_backward done, loss=0.0224
    [trainer_loop] step 1: optim done
    [trainer_loop] step 1: weights pushed
    [trainer_loop] step 1: weights pulled (step done)
    ```

Two warnings are expected and harmless: `initial_load_model_only=True has no
effect without an initial_load_path` and `Mamba cache mode is set to 'align' ...
when prefix caching is enabled`.

Note that step 1 does not arrive quickly. It needs
`SWE_INITIAL_ACTIVE_GROUPS=64` groups of 16 rollouts to finish, each rollout up
to a 2400-second agent budget. Minutes of `[buffer] complete` lines with no
`[trainer_loop] step` line is the normal state, not a hang.

---

## 3. The environment, locked

This is the part most likely to cost you a day, so it is the most detailed.

### What we ship

`requirements.lock.txt` is `pip freeze` from the venv that ran the job — 300
packages, captured 2026-08-29. `pyproject.toml` and `uv.lock` express the same
set for uv; `uv lock --check` passes against them.

Install with either:

```bash
uv pip install -r requirements.lock.txt \
    --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/nightly/cu130
# or, from this directory:
uv sync
```

**torchtitan is deliberately not in the lock.** It is not pip-installed at all —
verified: there is no `torchtitan*` in the venv's `site-packages` and no
`__editable__` path file. The launcher sets `PYTHONPATH=$TRL_TT` instead, so
edits to the checkout take effect without reinstalling.

### Provenance: no unresolved lines

A `pip freeze` line reading `-e /some/path` would not be reproducible. Verified:
there are none, and no bare local paths either. Five entries come from outside
PyPI and all five carry a commit or a full URL:

| package | origin |
|---|---|
| `batch-invariant-ops` | git `thinking-machines-lab/batch_invariant_ops` @ `f22b1fbe534b1fe045080f44a970a441caaf3c4f` |
| `renderers` | git `PrimeIntellect-ai/renderers` @ `2a7e77eb14dd2a1b26bafce580d17934d374faa2` |
| `torchstore` | git `meta-pytorch/torchstore` @ `5a4d5d3f4d653f2ed7cc913a66e49f822dfd6c1d` |
| `flashinfer-cubin` | release wheel `v0.6.15.post1` |
| `vllm` | pytorch nightly wheel `1.0.0.dev20260806+cu130` |

Read from each package's `direct_url.json` in the live venv, not inferred.

### Where the exact build matters

- **`torch==2.14.0.dev20260806+cu130` and `vllm==1.0.0.dev20260806+cu130`.** A
  same-day nightly pair from the same index. vLLM links against torch's C++ ABI;
  a mismatched pair fails at import or, worse, at a kernel call. Move both or
  neither.
- **`torchmonarch==0.6.0` and `torchstore` @ that commit.** Monarch is the actor
  runtime that places the trainer and generator meshes; torchstore is the weight
  transport between them. See the conflict note below — these two disagree on
  paper and work in practice.
- **`triton==3.8.0+git10f6be36`.** A git build, not a release. The GDN kernels go
  through it.
- **`flash-attn-4==4.0.0b26` and `flash_attn_3==3.0.0`, both installed.** FA4 is
  why the reference run uses `SWE_GEN_BACKEND=vllm_native`: on this box the
  `torchtitan_wrapper` varlen path trips an FA4-cute assert, `page_table is not
  supported with cu_seqlens_k`. If you change the FA version, re-test that knob.
- **`fla-core==0.5.2` / `flash-linear-attention==0.5.2`.** The Gated DeltaNet
  recurrence for the hybrid model, on both the trainer and generator sides.
- **`transformers==5.14.1` with `tokenizers==0.23.0rc0`.** Qwen3.5 support is
  recent enough that a stable tokenizers release will not do.
- Everything `nvidia-*` and `cuda-*` is pinned twice, once for cu12 and once for
  cu13, because vLLM and torch pull different sets. Do not prune them.

### The one thing to know before you trust the lock

**The live environment does not satisfy its own packages' declared metadata.** It
was assembled by successive `pip install`s where a later install overwrote a pin
an earlier package had asked for; pip does not re-check, so nothing complained.
`uv`'s resolver does check, and a plain `uv lock` fails. Verified conflicts:

```
torchstore @5a4d5d3   requires torchmonarch==0.4.1     installed 0.6.0
torchstore @5a4d5d3   requires torch==2.11.0 (cu130)   installed 2.14.0.dev20260806+cu130
vllm 1.0.0.dev...     requires tilelang==0.1.12        installed 0.1.13
datasets 4.7.0        requires fsspec<=2026.2.0        installed 2026.7.0
datasets 4.7.0        requires dill<0.4.1              installed 0.4.1
```

and more behind those — enumeration stopped once the pattern was clearly
systemic rather than a handful of exceptions.

So `pyproject.toml` restates **every** pin in `override-dependencies`. That makes
the resolve succeed and makes `uv sync` reproduce the exact venv, which is the
point. It does not make the set self-consistent — the overrides are precisely
what suppress the errors. The lock reproduces a known-working environment; it
does not certify a sound one. The job ran 150 steps, so these violations are
evidently tolerable in practice, but none has been shown harmless in principle.
If you hit something that smells like an ABI or API mismatch, start here.

---

## 4. Model and data

### Model

`Qwen/Qwen3.5-9B` — public, ungated (verified against the HF API).

```bash
hf download Qwen/Qwen3.5-9B --local-dir "$TRL_MODEL"
```

19 GiB on disk, 4 safetensors shards, 17.98 GiB of weights. It is a Gated
DeltaNet hybrid: 32 layers, every 4th is full attention and the rest are linear
attention, `head_dim=256`, `vocab_size=248320`, untied embeddings. The config
also carries a vision tower; the trainer freezes it (`Froze 441 vision_encoder
params`) and the recipe is text-only.

### Task corpus

Both source datasets are public and ungated (verified):

- `andylizf/TerminalWorld-Seeds-Clean` — the TerminalWorld half. Ships
  `data/tasks-00000.tar` plus metadata: `solvable_ids.txt` (766),
  `train_ready_ids.txt` (668), `fragile_build_ids.txt` (174),
  `policy_blocked_ids.txt` (28), `oversized_memory_ids.txt` (5),
  `pass_at_5.csv`, `tasks.parquet` (per-task `req_cpus` / `req_memory_mb` /
  `est_disk_mb` / `terminal_domain`), `measured_disk.csv` (real block usage
  from a full server-side build of every task).
- `Fzz1/Tmax-Tasks-Clean` — the TMax half, take8 onward (earlier takes mixed
  `Fzz1/SWE-Smith-Seeds-Clean` instead). Its `train` split carries 400
  rubric-audited, solver-verified task ids with measured `peak_ram_mb` /
  `peak_disk_mb` per task.

"Solvable" here means one specific measured thing: **pass@5 != 0** under a
reference solver, with the denominator being *graded* attempts, so a container
that died is excluded rather than counted as a failure. `train_ready_ids.txt`
(668) is the solvable set minus the fragile-build, policy-blocked and
oversized-memory exclusions, minus one task (`tw_572920`) whose package fails
to pack into a training row at all; it is the list to train on.

### Building the JSONL

Adapt a task tree into the training format with the in-repo preparer:

```bash
PYTHONPATH=$TRL_TT python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
    --tasks-root "$TRL_BASE/data/tw-extract/tasks" \
    --inject-agent-runtime \
    --out "$TRL_BASE/data/mix/tw_all.jsonl"
```

Repeat for the SWE tree, filter each to its id list, and concatenate. Row schema,
verified against the loader (`examples/tmax/data.py`) and against the live file:

```
{"label": "tw_240515",
 "prompt": "<the task instruction as text>",
 "metadata": {
    "instance_id": "tw_240515",
    "problem_statement": "...",
    "dockerfile": "FROM ubuntu:22.04\n...",   # or "image"
    "build_context": {"WorldCup.csv": "<base64>"},
    "workdir": "/app",
    "tmax": {"test_sh": "#!/bin/bash\n...", "fixtures": ..., "reward_path": ...},
    "oracle_commands": 11}}
```

Training rows carry no `agent_timeout_sec`: the launcher's flat
`SWE_TIME_BUDGET_SEC=2400` governs the rollout budget. `prepare_rts_data` emits
a task's declared `[agent] timeout_sec` only under `SWE_EMIT_AGENT_TIMEOUT=1`,
an eval-only opt-in -- declared budgets are sized for oracle solvers, and a
backfill of them into training rows killed 75-85% of rollouts for the 9B policy
before it was reverted.

Hard requirements — the loader raises without them:

```python
if not (image or dockerfile) or not tmax:
    raise ValueError(f"row {instance_id!r} missing image/dockerfile/tmax in metadata")
```

`dockerfile` wins over `image` when both are present. `metadata.tmax.test_sh` is
the verifier and is read at grading time. `workdir` defaults to `/workspace`.
Fields that fall back silently rather than raising — `instance_id` (falls back to
`label`, then the literal `"unknown"`) and `problem_statement` (falls back to
`prompt`) — are worth validating yourself, because a missing one poisons the
pipeline with no error.

### The take8 mix is reproducible (mix_v2)

`mix_live.jsonl` for take8 started as `mix_v2.jsonl`, built in one shot by
`build_mix_v2.py` (source of truth: the `terminalworld-seeds` repo; deployed
copy under `$TRL_BASE/evolve-onhost/scripts/`) — 668 TerminalWorld rows plus
400 TMax rows, shuffled with seed 1208, the last 64 rows after that shuffle
being the holdout:

```bash
python3.11 build_mix_v2.py --out $TRL_BASE/data/mix/mix_v2.jsonl --apply
```

Per-row sandbox sizing comes from first sources only: `tasks.parquet`
declarations above the 1 vCPU / 2 GiB fleet defaults, `measured_disk.csv`
above 2 GiB (capped at Daytona's 10 GiB), and the TMax parquet's
`peak_ram_mb` / `peak_disk_mb` plus 1 GiB slack. No template boilerplate is
copied through: 674 of 695 `task.toml` files declare 2 vCPU / 4096 MB
verbatim, and writing those back would undo the fleet sizing corpus-wide.

The build writes `mix_v2.jsonl.manifest.json` beside the output: row counts,
every id that failed to resolve, and a SHA-256 for each of the four inputs
(TW id list, `tasks.parquet`, TMax train parquet, prepared TMax rows). The
published `tasks.parquet` is byte-identical to the pinned input. The pinned TW
id list is the pre-correction 669-id version whose one extra id (`tw_572920`)
drops out at pack time, so rebuilding from the published 668-id list yields
the same rows. The prepared TMax rows (`data/tmax_train.jsonl`, 14,601 rows)
are `prepare_rts_data` output over the TMax task pool; the mix joins the 400
`train`-split ids against it.

After launch the file diverges from `mix_v2.jsonl` by exactly one mechanism:
the evolution loop's replace-only folds. Every fold is one commit in the
`$TRL_BASE/evolution` git repository (retuned task tree plus a
`mix_snapshot.jsonl`), so any intermediate state of the live mix is a
checkout, and the full lineage from `mix_v2` to now is the `git log`.

### The holdout

**The last 64 rows of the training JSONL, in file order, are the validation
slice.** The count is `_TMAX_9B_HOLDOUT_N = 64` in
`examples/tmax/config_registry.py` — a module constant, **not configurable** by
env var or config field. The split happens in `data.py` before any id filtering,
so it is stable regardless of which ids you include or skip:

```python
samples = (samples[-config.holdout_n:] if config.split == "validation"
           else samples[:-config.holdout_n])
```

The live file is 1,068 rows: 1,004 in rotation plus the 64-row holdout tail
(verified: `wc -l mix_live.jsonl` = 1068).

Two consequences worth internalising:

- **Row order is the eval instrument.** Anything that appends to the file shifts
  the last 64 rows and silently rotates your holdout. The evolution loop's fold
  is replace-only for exactly this reason, and refuses to add a label that is not
  already present.
- **The take8 starting file is exactly reconstructible** (see the section
  above), and the holdout is a deterministic function of the seeded shuffle.
  This was not true of take7: that file's order was the accumulated result of a
  concatenation followed by months of in-place folds and backfills, which is
  precisely why take8 rebuilt the mix from first sources. A mix rebuilt with a
  different seed or input set is a valid training set with a *different*
  holdout, so its eval numbers are not comparable to ours.

---

## 5. Sandbox backend (Daytona)

Every rollout runs in its own cloud sandbox: **one sandbox per rollout**, never
reused, never shared across a group, deleted on exit. A group of 16 is 16
sandboxes. At `SWE_ROLLOUT_CONCURRENCY=1536` that is a lot of sandbox churn, and
`TT_DAYTONA_CREATE_CONCURRENCY` (128 in the reference config; 32 left a
restart's create queue tens of minutes deep) is what keeps the create rate
under the platform's throttle.

Credentials: `DAYTONA_API_KEY` is required and hard-checked —
`RuntimeError("DAYTONA_API_KEY is not set; required for the daytona sandbox
backend.")`. `DAYTONA_API_URL` and `DAYTONA_TARGET` are optional overrides.

The image comes from the task row, not from configuration: `metadata.image`, or a
server-side build of `metadata.dockerfile` plus base64 `build_context`. Daytona
caches the build, so only the first sandbox per distinct Dockerfile pays for it.

### What the image must contain

Verified from the exec wrapper and the agent harness:

- `bash`, and GNU coreutils `timeout` supporting `--signal`/`--kill-after`
- `base64`, `printf`, `mkdir`, `rm`, `mv`, `head`, `tail`, `stat`, `cat`
- `sh` and procfs (`/proc/$$/fd/N`)
- writable `/dev/shm` and `/tmp`
- everything runs as **root**; tmax hard-overrides the exec user

Explicitly *not* required: `curl` (the tmax rollouter passes
`install_claude=False` precisely because the task images have no curl), and
`asciinema` (terminal recording is off, so it is never probed).

### tmux — the claim needs correcting

tmux is required *by the agent at runtime*, but it does **not** have to be baked
into the image. Terminus-2 drives a live tmux pane, and harbor installs tmux
itself at session bring-up (`TmuxSession.start` → `_attempt_tmux_installation`:
package manager first, then a from-source build). The in-repo comment is explicit
that baking it in is "**A latency optimization, NOT a requirement**", with
measurements: harbor's runtime install succeeded on 6/6 bases — ubuntu:16.04
5.0s, centos:7 10.9s, ubuntu:22.04 9.1s.

`prepare_rts_data --inject-agent-runtime` bakes it in anyway to move those
seconds off every rollout. That injected block is deliberately non-fatal (it ends
in `|| echo ...`), because a hard failure dropped whole images to `BUILD_FAILED`
and burned create quota — one archlinux task took 192 rollouts down that way.

**The failure mode when tmux is genuinely absent** and the runtime install also
fails (which needs both no usable package manager and no compiler):

```
RuntimeError: Failed to start tmux session. Error: <stderr>
```

raised inside agent setup, caught, and turned into `finish_reason="error"`,
`turns=0`, `submitted=False`. The verifier never runs, so **reward is 0.0** — and
critically this is `infra_failed=False`, meaning it is scored as a genuine zero
and *enters the advantage baseline*. That is different from a sandbox that failed
to boot, which is scored `NaN` and dropped. `stderr` is usually empty on Daytona,
which is why the code logs the failing command separately.

Because the injected preinstall is non-fatal, a build where it failed looks
identical to one where it worked. The rollouter therefore runs one cheap
`command -v tmux` per rollout and records misses under
`$(dirname $SWE_TASK_EVOLUTION_DIR)/no_tmux/`. Treat that list as "where to look
first", not as a verdict: on the first pass 78 tasks landed there while none of
them actually died, so filtering on it would have dropped 78 healthy tasks.

### When sandbox creation fails

Three layers, and the run never crashes: `TT_DAYTONA_CREATE_RETRIES` (8 in the
reference config) with jittered backoff, inside `SWE_BOOT_RETRIES` (default 2),
inside a per-rollout `except` that marks the rollout `infra_failed`. An
infra-failed rollout's reward is then overwritten to `NaN` so the advantage
estimator drops it — a group of 16 with one infra failure baselines over the
surviving 15 rather than treating the failure as a zero.

Memory: `TT_DAYTONA_MEM_GB` (4) is the fallback when a row declares nothing;
`TT_DAYTONA_MAX_MEM_GB` (8) **clamps** whatever a row asks for. They are not
synonyms. The clamp exists because five TerminalWorld tasks declare 16 GiB
against an 8 GiB platform cap and had burned 704 rollout slots between them.

Sizing has since moved under the run: the live fleet default is now **1 vCPU /
2 GB RAM / 2 GB disk** per sandbox, with per-row `daytona_cpu` /
`daytona_mem_gb` / `daytona_disk_gb` overrides for the tasks measured to need
more. Daytona's hard per-sandbox caps are **4 vCPU / 8 GB RAM / 10 GB disk**
(daytona.io/docs/en/limits) -- the `TT_DAYTONA_MAX_MEM_GB=8` clamp is that cap,
not a tunable. A full-corpus disk measurement (real block usage, not apparent
size -- a sparse `truncate -s 10G` file is not 10 GB of usage) found every task
in the current mix builds inside the 10 GB cap; an earlier "tier-unrunnable"
verdict on four tasks was a sparse-file measurement artifact and was retracted.

### Account capacity, and what actually bounds concurrency

Per-sandbox resources come from the DATA ROW first. `TT_DAYTONA_CPU` /
`TT_DAYTONA_MEM_GB` / `TT_DAYTONA_DISK_GB` apply only where a row declares
nothing, so sizing the fleet from the env values alone mis-states it by whatever
fraction of the corpus carries its own overrides, and that fraction moves every
time the mix is rebuilt. Measure it, do not assume it.

Measured on the live 1068-row `mix_live.jsonl` (2026-08-30): 60 rows declare
`daytona_cpu`, 109 declare `daytona_mem_gb`, 58 declare `daytona_disk_gb`. The
remaining ~95% take the env values, giving data-weighted averages of **1.08
vCPU, 2.23 GiB memory and 2.08 GiB disk** per sandbox. A live snapshot the same
day put 377 active sandboxes at exactly 1/2/2 with no outliers
(`terminalworld-seeds`, `scripts/daytona_snapshot.py`; that script uses the
SDK's `list()` because the REST endpoint ignores its `page` parameter and
returns the same window on every page).

Storage is not the binding axis at this sizing. It was, in a corpus where every
row declared 10 GiB, but the current mix leaves disk to the env value on 95% of
rows and averages 2.08 GiB.

Account limits, recorded 2026-08-29 and **not re-verified here** (the API's
`/organizations` endpoint rejects the sandbox key, so this number has no
first-hand check behind it): 20000 vCPU, 80000 GiB memory, 80000 GiB storage.
The older 5000 / 20000 / 25000 figures under-count. The account is SHARED: 1024
sandboxes belonging to another run were live when those limits were recorded, so
read current usage before raising concurrency rather than assuming the whole
account is yours.

Taking those limits at face value, 20000 vCPU / 1.08 vCPU per sandbox is a
ceiling near 18000 concurrent sandboxes, and memory and disk sit higher still
(80000 / 2.23 and 80000 / 2.08). Nothing the recipe schedules comes close, so
Daytona capacity is not what bounds TMax concurrency. The scheduler is: 40
active groups of 32 siblings is 1280 schedulable rollouts, and concurrency above
1280 cannot add useful rollout work without widening the active-group window.
The production 512-concurrency split uses 16 rollout workers with 32 sibling
slots each; 1024 over those same 16 workers would need 48 active groups and is
rejected under the 40-group cap, so use 8 workers for 1024, or concurrency 1008
with 16 workers.

Raising concurrency does not guarantee a speedup either way. Check Daytona
failures and rate limits, generator queue and inflight metrics, rollout-worker
CPU load, and trainer batch-wait time.

---

## 6. Environment variable reference

145 environment variables are read across the RL tree (AST scan of the live
commit, non-test files). Listed here are the ones that affect a tmax 9B run. The
value column is what the reference run used; where it differs from the code
default, both are shown.

### Must be set — no usable default

| variable | note |
|---|---|
| `DAYTONA_API_KEY` | every rollout. Hard-checked. |
| `SWE_PROMPT_DATA` | training JSONL. Defaults to `""`, and the dataset raises on an empty path. |
| `LOCAL_RANK` | `os.environ['LOCAL_RANK']`, no fallback — set by the launcher/Monarch, not by you. |

### Correctness — these change what the run *is*

| variable | ours | code default | controls |
|---|---|---|---|
| `SWE_DP_SHARD` | `2` | `0` (keep base FSDP-8) | trainer FSDP width. Must match GPU count with `SWE_GEN_DP`. |
| `SWE_GEN_DP` | `3` | `0` (base DP-8) | vLLM engines. Must match GPU count with `SWE_DP_SHARD`. |
| `RL_GPU_OFFSET` | `0` | `0` | start of the contiguous GPU window. The only way to move placement. |
| `SWE_GEN_BACKEND` | `vllm_native` | `vllm_native` | `torchtitan_wrapper` is the unified-model path; it asserts under FA4 here. |
| `SWE_NUM_GROUPS_PER_TRAIN_STEP` | `32` | `8` | prompt groups per optimizer step. |
| `SWE_GROUP_SIZE` | `16` | `32` | rollouts per prompt — the GRPO group. |
| `SWE_OFFPOLICY_STEPS` | unset | `4` | policy-age cap. |
| `SWE_DROP_ZERO_STD` | `0` | `1` | drop groups with no reward variance. Off here *only* because evolution re-tunes them instead. Without evolution, use `1`. |
| `SWE_TRAIN_STEPS` | `150` | `100` | total optimizer steps. |
| `SWE_LR` | `3e-6` | `0` (keep `1e-6`) | learning rate. |
| `SWE_LOSS` | unset | `dppo` | `dppo` / `dapo` / `grpo`. |
| `SWE_MAX_CONTEXT_LEN` | `63488` | **four different defaults** by entry point (32768 / 63488 / 22528 / 20480) | per-session context budget. Always set it explicitly. |
| `TMAX_AGENT` | `terminus` | `vanillux` | agent scaffold. Changes the action distribution. |
| `SWE_AGENT_TIMEOUT_FLOOR_SEC` | `900` | `7200` | floor on a task's declared budget, never a ceiling. |
| `SWE_WRONG_SUBMIT_PENALTY` | `0.3` | `0` | reward penalty for submitting a wrong answer. |
| `SWE_REWARD_DENSE` | unset | `0` | dense per-test reward instead of binary. |
| `TMAX_FORMAT_ERROR_FEEDBACK` | unset | `0` | at `0`, a turn with no tool call ends the rollout immediately (open-instruct parity). |
| `TMAX_TERMINUS_SUMMARIZE` | unset | `0` | upstream defaults this on; the module docstring says "on a 9B it is lethal". |

### Performance and capacity

| variable | ours | code default | controls |
|---|---|---|---|
| `SWE_ROLLOUT_CONCURRENCY` | `1536` | `16` | global cap on live rollouts (and sandboxes). |
| `SWE_NUM_ROLLOUT_WORKERS` | `16` | `8` | rollout worker processes. |
| `SWE_MAX_ACTIVE_GROUPS` | `512` | `40` | run-ahead group buffer. |
| `SWE_INITIAL_ACTIVE_GROUPS` | `64` | computed | cold-start admission. |
| `SWE_SELECTION_WINDOW_GROUPS` | `64` | unset (take-any) | sliding-prefix batch selection. |
| `SWE_GPU_MEM_LIMIT` | `0.85` | `0` (keep `0.8`) | vLLM's **total** budget: weights + activations + KV. |
| `SWE_GEN_PREFIX_CACHE` | `1` | unset (vLLM's choice) | prefix caching. ~2x prefill on this hybrid, byte-identical outputs in a local smoke. |
| `SWE_GEN_CUDAGRAPH` | unset | `1` in tmax, `0` in swe_r2e | ~3x GDN decode. Note the families disagree. |
| `SWE_DISABLE_CUSTOM_ALL_REDUCE` | `1` | unset | falls back to NCCL. |
| `SWE_TIME_BUDGET_SEC` | `2400` | `2400` | agent wall clock per rollout. |
| `TMAX_EXEC_TIMEOUT_SEC` | `120` | `120` | per-command timeout inside the sandbox. |
| `TMAX_TERMINUS_MAX_TURNS` | `120` | `64` | turn ceiling. |
| `TMAX_TURN_MAX_TOKENS` | `32768` | `16384` (terminus) | per-turn generation cap. |
| `SWE_CKPT_INTERVAL` | `5` | `20` | steps between saves. |
| `SWE_CKPT_KEEP` | `8` | `3` | checkpoints retained. 8 x 98 GiB ~= 0.8 TiB. |
| `SWE_LMHEAD_TF32` | `1` | `0` | TF32 tensor cores for the fp32 lm_head matmuls (loss). Measured 08-30 (B300, 65536-token rows): with `SWE_LOSS_CHUNKS=8`, loss_fn 6.60 -> 0.82 s/mb, whole fwd_bwd 14.10 -> ~5.9 s/mb; grad_norm unchanged. |
| `SWE_AC` | `selective` | FullAC | per-op selective activation checkpointing. Safe only with the packed-row cu_seqlens hoisted out of AC (4380eaab); before that fix, both AC modes could deadlock in recompute. Measured 08-30 on top of TF32+chunks: fwd_bwd ~5.9 -> 5.65 s/mb median (117 mb), backward 3.99 -> 3.72 s/mb; trainer GPUs 238/275 GiB. |
| `SWE_LOSS_CHUNKS` | `8` | `32` | chunked-loss width; fewer = larger lm_head GEMMs. Validated with TF32 above. |
| `SWE_MAX_NUM_SEQS` | `256` | `256` | decode slots per engine. 512 collapsed the pipeline: per-seq decode halved, turns stopped fitting agent budgets. |
| `SWE_SANDBOX_BOOT_ALLOWANCE_SEC` | `2700` | `2700` | extra initial rollout-guard headroom for the sandbox boot queue; rescheduled away once the sandbox is up. |
| `TT_DAYTONA_CREATE_CONCURRENCY` | `8` | `16` | **per rollout-worker process**, not global: the semaphore is module-level and each worker is its own process, so the effective global parallelism is this value x SWE_NUM_ROLLOUT_WORKERS. 8 x 16 workers = 128 global, which saturates the platform's ~2000 creates/min. A `128` here (misread as global) is 2048 global and produces 429/BadRequest storms at every boot wave. |
| `SWE_VAL_SAMPLES` | `0` | 89 or 32 | `0` disables validation entirely. |
| `SWE_VAL_INTERVAL` | `20` | `20` | steps between validation passes. |
| `SWE_NUM_EVAL_GENERATORS` | `0` | `0` | dedicated eval GPUs; `>0` also makes validation async. |
| `SWE_EVAL_GEN_DP` | `0` | `0` | engines per eval generator. `0` = as wide as a training generator (`SWE_GEN_DP`), which on one 8-GPU box hands most of the box to a host that works every `SWE_VAL_INTERVAL` steps; `1` spends one GPU on it. A pass still running at the next interval is skipped, not queued, so undersizing thins the eval curve rather than stalling the step. |
| `SWE_DATA_HOT_RELOAD` | `1` | `0` | re-read the JSONL on mtime change. Required for evolution. |
| `TT_DAYTONA_CREATE_RETRIES` | `8` | `5` | create retries. |
| `TT_DAYTONA_MEM_GB` | `4` | `4` | per-sandbox memory when the row declares none. |
| `TT_DAYTONA_MAX_MEM_GB` | `8` | `8` | clamp on a row's declared request. |
| `TT_DAYTONA_DISK_GB` | `10` | `6` | per-sandbox disk. |
| `TT_DAYTONA_HEARTBEAT_SEC` | `180` | `min(180, auto_stop*20)` | keeps a sandbox alive while the rollout waits on generation. |
| `TT_DAYTONA_AUTO_DELETE_MIN` | `15` | `0` | cloud-side delete delay. |
| `TT_DAYTONA_EPHEMERAL` | `1` | `0` | ephemeral create flag. |
| `SWE_BRIDGE_POLL_INTERVAL` | unset | `0.2` | **do not lower.** At 64 bridges, 0.05 gives 1280 req/s, past the platform's ~833 req/s cap. |

### One trap

`SWE_GDN=1` appears in our launcher and in the in-repo READMEs. **No Python code
in the tree reads it** (verified by AST scan and by grep across the repo). The
only live reference is a comment noting that a `run.sh` elsewhere used it to
switch `CUDA_HOME`. It is inert for this recipe. `SWE_GDN_BI` is a different,
real variable.

---

## 7. The evolution loop

Optional. A plain RL run works with it entirely absent, and nothing in the
training tree imports anything from it — the coupling is one directory of JSON
files plus one JSONL path. But it is what the reference run was doing, so it is
documented in full. Source is vendored in this repo at
`examples/tmax/evolution/` (16 files). The modules import each other by bare
module name, so they must stay flat in that directory.

### What it does

GRPO learns nothing from a group whose rollouts all agree: if all 16 fail or all
16 pass, reward variance is zero and the advantage is zero. Rather than discard
those prompts, the loop rewrites them — too hard gets easier, too easy gets
harder — and folds the rewrite back into the live training file.

1. **Signal.** After scoring a group, the trainer checks
   `statistics.pstdev(rewards) == 0`. If so it writes one JSON file per task to
   `$SWE_TASK_EVOLUTION_DIR`, named `<instance_id>.json`, carrying `task_id`,
   `solved`, `total`, a `direction` of `"harder"` or `"easier"`, and the per-turn
   transcript. One file per task, write-once — that is what survives a FUSE mount
   and pooled worker processes.
2. **Retune.** `evolve_ondella.py` picks signals up, and routes: all-fail →
   simplify the instruction only, never the verifier; all-pass → a structural
   change to one operator, one rung above **the version the mix is serving**.
   That version is kept in `evolution/parents/<task_id>/` with its revision and
   rung recorded beside it; a rewrite starts from it, and from the seed only
   when there is none or when the recorded revision no longer matches the live
   row (the mix was rebuilt, the family dropped, a later fold rejected). Before
   this the source was always the seed, so a task needing three rungs rebuilt
   rung one every time it signalled and never climbed.
3. **Revalidate.** A structural change must be rebuilt and its reference solution
   re-run before it is trusted. On a host with no Docker that happens in Daytona
   — two probes in two fresh sandboxes (oracle, then a shortcut/cheat check).
   Both open in the seed's box (`daytona_*`), raised to what the reference
   solution measured in the agent's own container. The size that lands in the
   row comes from the oracle probe's own counters, sized by
   `derive_sizing.size_from_oracle` (the seed campaign's rule) and never below
   the seed: the agent's `./sandbox check` reading only picks the box the
   probe runs in, since the agent can edit its copy of the sandbox tool and
   cannot reach the probe's container. When the box cuts the reference
   solution short the agent's check says so, and `./sandbox check --max`
   measures at the platform ceiling. The oracle probe also asks the untouched
   container about every path the rewritten verifier requires that neither
   the instruction, the Dockerfile nor a file in the build context names: one
   that does not exist there is something an agent cannot know to create, and
   the rewrite goes back to the agent's session as `dark_paths` (measured on
   wd-20260903b: the static audit flags a third of agentic rewrites, most of
   them README-documented and fine; the container check is what separates
   those from a genuinely unsolvable task). The same goes for names: the keys,
   line labels and file names the rewritten verifier depends on have to be
   stated, spelled the same, where the agent can read them, or the rewrite
   goes back as `dark_literals` (`verifier_literals.py`; the agent's own
   `./sandbox check` fails on the same audit first, so a repair round is the
   exception). Reviewed on wd-20260903b: five of eight hardened tasks that went
   0/16 failed on a report key or line label only the reference solution knew,
   three of them with the work otherwise complete. A session that never passed
   its own `./sandbox check` is discarded as `agent_failed`.
   The rewrite also has to be one rung above the seed, by size (`task_size.py`):
   the reference solution grows by 3 to 8 non-comment lines over the seed's and
   the verifier gains at most 5 assertions, or the check fails as `step_size`
   and revalidate sends it back. The numbers come from the seed corpus's own
   training outcomes (solutions of 14 to 20 lines have the largest mixed-signal
   share; the 0/16 share doubles past 20; the agentic arm's unbounded rewrites
   had a median of 125 lines and came back 0/16 five times in six). Size is a
   heuristic for how much the policy has to reproduce, not a difficulty
   measurement; the difficulty probe on the eval host (step 6) is the
   measurement, and the training signal is the final word.
   A simplify takes an instruction-only fast path with no build and no sandbox,
   which is why the loop is cheap on the direction that dominates.
4. **Fold.** Accepted rewrites are written back into the mix atomically
   (temp file, then `os.replace`), **replace-only**: a label not already in the
   file is skipped, because a new row lands at the end and would shift the
   holdout tail. The folded row carries the size from step 3 in its `daytona_*`
   keys (`.resources.json` beside the retuned package says where it came from),
   and the `folded` lineage event records it with its source.
5. **Reload.** With `SWE_DATA_HOT_RELOAD=1` the trainer re-reads the file on
   mtime change (rate-limited to one stat per 20s). Same-id rows are swapped in
   place so a resumed checkpoint's ordering still points at the same tasks.
   Validation stays pinned to the boot-time file. A malformed reload is logged
   and ignored, never fatal.

6. **Probe, before a change to the loop reaches a run.** A rewrite's difficulty
   is measured, not judged: `eval_host/difficulty_probe.sh <rows.jsonl>
   <base|step-N> [k]` on the flow-matic eval host runs the policy k times per
   task at the training rollout's sampling and prints passes per task; 0/k is
   too hard for that policy, k/k too easy, the band between is what a fold
   should land in. Used on a dev workdir's folds (`della/probe_rows.sh` picks
   the rows) to check a change to the harder arm before the production loop
   gets it; the training signal remains the final measurement.

### Credentials and knobs

| variable | live value | note |
|---|---|---|
| `OPENAI_API_KEY` | *(supplied)* | the retune model. Dies at startup without it. `SYNTH_ENV_FILE` can point at a file holding it instead. |
| `DAYTONA_API_KEY` | *(supplied)* | structural revalidation. Without it every all-pass retune is declined `no_docker`. |
| `SYNTH_API_BASE` | `https://us.api.openai.com/v1` | note the regional host; `api.openai.com` 401s with "incorrect regional hostname". |
| `SYNTH_MODEL` | `gpt-5.6` | an alias that resolves to the most expensive of three tiers — price against the resolved name, not the alias. |
| `SWE_RETUNE_AGENT` | `codex` | `chat` (default) does single API calls. `codex` runs an agent session under `agents/task_evolution.md`, capped at 25 tool calls / 600s, with a private `CODEX_HOME` so a stray token cannot win. Falls back to `chat` on failure. |
| `SWE_EVOLVE_SIMPLIFY` | `0` | **off.** Default is on. |
| `SWE_SIMPLIFY_HINT` | `vague` | `specific` bakes where-to-look hints into hundreds of instructions and the holdout experiment showed the policy learns hint-following that does not transfer. |
| `TRL_TT` | the checkout | the packer imports the training side's own row builder rather than mirroring the schema, so it fails loudly without this. |

**Why simplify is off** is worth quoting, because it is a real finding rather
than a preference — the ratchet only turns one way. A simplify is accepted almost
every time (revalidation only asks whether `solve.sh` still passes, and rewriting
an instruction cannot break `solve.sh`), while an evolve has to survive a rebuilt
verifier. Measured on this corpus: **693 accepted simplifies against 335 accepted
evolves in one week, and 814 against 26 in an earlier window, with the on-mix
solve rate climbing while the fixed eval stayed flat.** That is the signature of
a training set getting easier rather than a policy getting better. With simplify
off, the too-hard tail freezes instead of being loosened.

### Running it

```bash
cd "$TRL_TT/torchtitan/experiments/rl/examples/tmax/evolution"
set -a; . ../runbook/profiles/andy.env; set +a   # TRL_BASE, TRL_TT, signals dir; or yichuan.env
export OPENAI_API_KEY=...  DAYTONA_API_KEY=...
export SWE_RETUNE_AGENT=codex SWE_EVOLVE_SIMPLIFY=0 SWE_SIMPLIFY_HINT=vague

# one round, then exit
python evolve_ondella.py --once --workers 16
# continuous, which is how the reference run has it
python evolve_ondella.py --interval 120 --workers 16
```

Safe dry run — writes elsewhere and leaves the live mix alone:

```bash
python evolve_ondella.py --once --only <task_id> \
    --mix-out /tmp/mix_test.jsonl --keep-signal
```

Restart with `restart_evolve.sh`, not Ctrl-C: an interrupt gets absorbed
mid-round and you end up with two instances. Verify with
`pgrep -cf evolve_ondella` — it should print `1`. The loop's credentials live
only in its own process environment, which is why the restart script carries them
across from the process it replaces.

### Reading its log

`$TRL_BASE/logs/evolve_ondella.log`. Normal lines:

```
INFO  tw_385269 solved=16/16 -> evolve (revalidate_shortcut_failed, arm=agent_harder)
WARN  tw_244302 revalidate_shortcut_failed: passed on: cd /app; ...
INFO  HTTP Request: POST https://us.api.openai.com/v1/chat/completions "HTTP/1.1 200 OK"
```

`revalidate_shortcut_failed` means the cheat probe solved the task without doing
the work, so the task needs hardening — that is the loop working, not an error.
`no signals` means the trainer has not produced any zero-variance groups since
the last round, which is common: the loop is signal-starved, and roughly 89% of
rounds carry 8 signals or fewer.

Cost, derived from the code: an all-fail simplify is 1 model call and no sandbox.
An all-pass evolve is roughly 9-11 calls at high reasoning effort plus two
sandbox probes. The codex arm spends considerably more per signal than that.
Token usage is stamped onto every record, so a round can be priced after the
fact.

Enabling evolution puts sandbox load on the same Daytona quota the rollouts use.

---

## 8. Verifying it worked

**After boot.** The GPU accounting line matches your intent, both meshes reach
`Finished loading the checkpoint`, and vLLM reports a KV cache figure and a
maximum concurrency. On the reference host at `SWE_GPU_MEM_LIMIT=0.85`:
`Available KV cache memory: 204.61 GiB`, `Maximum concurrency for 65,536 tokens
per request: 96.93x`.

**After step 1.** You want the full sequence, ending in `weights pulled (step
done)`. `forward_backward done, loss=<small>` — the reference run's step 5 was
`loss=0.0224`; this is a policy-gradient surrogate, so its absolute value is not
a quality signal, but a `nan` is a real problem.

**Group variance is the number to watch.** It is what decides whether the run
learns anything at all. From the reference run's log:

| class | lifetime | most recent 2000 groups |
|---|---|---|
| `partial_solve` (has gradient) | 2985 (58.7%) | 1262 (63.1%) |
| `full_solve` (no gradient) | 899 (17.7%) | 430 (21.5%) |
| `not_solve` (no gradient) | 1204 (23.7%) | 308 (15.4%) |

So **roughly 60% of groups carry gradient**, and that is a healthy figure for
this corpus. If yours sits near 0%, the task pool is mismatched to the policy —
all-fail means too hard (or a broken environment), all-pass means too easy. Note
the drift between the two columns: `not_solve` fell from 23.7% to 15.4% while
`full_solve` rose, which is what the evolution loop is designed to produce and
also exactly what you must watch, since the easy direction is the one that
ratchets.

The per-step confirmation line is:

```
[buffer] step 5: RELEASE(trained) 32 trainable group slots (this step trained on 32 partial-solve groups)
```

32 matches `SWE_NUM_GROUPS_PER_TRAIN_STEP`. A number consistently below it means
the buffer is not keeping up.

**Checkpoints** land in `<run dir>/outputs/rl/checkpoint/step-<N>` — relative to
the launcher's working directory, which is why the script `cd`s into the dump
directory first. 98 GiB each, verified.

**W&B** gets the metrics under `WANDB_PROJECT`; the run URL is printed during
boot.

---

## 9. What we could not verify

Stated plainly, because a runbook that quietly guesses is worse than one that
admits a gap.

- **Nothing here was executed end-to-end on a clean machine.** Every value was
  read off a running system; the from-zero sequence in section 2 is assembled
  from that system's launcher, unit file and logs. The install step in
  particular has not been run against an empty venv.
- **`uv sync` was never executed.** `uv lock` and `uv lock --check` both pass and
  the lock's pins were verified against the venv package by package, but no
  install was performed from it — the resolution ran on macOS while the lock
  targets linux/x86_64.
- **The 8-GPU layout is untested.** The reference run used 5 of 8 because the box
  is shared. `SWE_DP_SHARD` + `SWE_GEN_DP` must equal the GPU count; what values
  are best at 8 has no measurement behind it here.
- **Only `Qwen/Qwen3.5-9B` public availability was checked**, not that the public
  weights are byte-identical to the local copy.
- **The harbor version is unpinned.** The RL tree declares no constraint on it,
  and harbor is what actually issues the tmux commands. The only version visible
  from here (0.21.0) is in a separate smoke-test venv, so which version the
  training venv resolves is unconfirmed. Given that harbor owns the tmux
  behaviour described in section 5, this is the most likely source of a silent
  behavioural difference between your run and ours.
- **Checkpoint resume was not exercised.** `RL_RESUME_DUMP` is wired through the
  launcher and the systemd unit restarts automatically, but no resume was
  performed during this work.
- **Evolution cost figures are derived from the code paths**, not from a billing
  statement.
