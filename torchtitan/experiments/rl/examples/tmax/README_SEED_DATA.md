# tmax seed-data pipeline (RTS / TerminalWorld / SWE-Smith)

How to turn a published **seed corpus** of terminal-agent tasks into a training
JSONL the tmax RL loop can roll out on. All three corpora below are
[Harbor](https://www.harborproject.org) task trees with the same layout and the
same verifier contract, so **one adapter (`prepare_rts_data.py`) and one grader
(`grading.py`) read all of them** -- only the source and a few per-dataset filter
columns differ.

> Scope: this doc is about *preparing the data*. For the training recipe, the
> rollout loop, and the run knobs, see [`README.md`](./README.md). For how each
> task's sandbox gets its cpu / memory / disk, see
> [`SANDBOX_SIZING.md`](./SANDBOX_SIZING.md).

---

## 1. The three seed corpora

| corpus | HF dataset | tasks | domain | environment |
|---|---|---|---|---|
| **RTS** (Recursive-Task-Synthesis) | `Zhongzhi1228/Recursive-Task-Synthesis` | 37,484 (8 shards) | general terminal, recursion-synthesized | self-contained Dockerfile |
| **TerminalWorld-Seeds-Clean** | `andylizf/TerminalWorld-Seeds-Clean` | 1,353 | general terminal (TerminalWorld benchmark seeds) | self-contained Dockerfile |
| **SWE-Smith-Seeds-Clean** | `Fzz1/SWE-Smith-Seeds-Clean` | 1,775 | Python repo bug-fix (SWE-bench/SWE-smith) | `FROM` a Docker Hub base image + bug patch |

All three ship in the RST release layout, so a single loader reads any of them:

```
<task>/instruction.md          # the agent instruction (the "problem statement")
<task>/task.toml               # cpus / memory / verifier+agent timeouts
<task>/environment/Dockerfile  # the task env -- usually NOT a published image
<task>/tests/test.sh           # verifier: pytest -> /logs/verifier/reward.txt (0 or 1)
<task>/solution/solve.sh       # oracle solution (used only for validation, not training)
<task>/seed.json               # (SWE-Smith) image, repo, F2P/P2P lists, conda activation
metadata/tasks.parquet         # per-task columns incl. the FILTER columns (see below)
data/tasks-*.tar               # the task trees, tarred
```

The verifier contract (`bash tests/test.sh` -> writes `0`/`1` to
`/logs/verifier/reward.txt`) is identical to tmax, so `grading.py` grades every
row unchanged.

---

## 2. The adapter: `prepare_rts_data.py`

Reads one or more extracted `tasks/` directories and emits a training JSONL where
each row is `{"prompt", "label", "metadata"}`. `metadata` carries everything the
sandbox + grader need: `instance_id`, `dockerfile` (text, since most rows have no
published image), `workdir`, `problem_statement`, `oracle_commands`, the `tmax`
grading blob (`test_sh` / `fixtures` / `reward_path`), and -- when the Dockerfile
`COPY`s local files -- a `build_context` (`{relpath: base64}`) the sandbox writes
back beside the Dockerfile so the build resolves.

### CLI

| flag | meaning |
|---|---|
| `--out PATH` | output JSONL (required) |
| `--tasks-root DIR` | extracted `tasks/` dir; **repeat** to mix shards/difficulties |
| `--inject-agent-runtime` | append a tmux install step to each Dockerfile. **Required for any corpus that ships upstream content verbatim (TerminalWorld, SWE-Smith)** -- see the tmux gotcha below. RTS Dockerfiles already carry it. |
| `--max-oracle-commands N` | drop tasks whose `solve.sh` runs > N commands (see Difficulty) |
| `--limit N` | emit at most N tasks |
| `--seed N` | task-order shuffle seed (default 42) |
| `--smoke-size N` | also write a small `*_smoke.jsonl` with N rows |

### What it filters out

A task is dropped if its environment needs a host we do not control: an init
system, the docker socket, `--privileged`, an end-of-life base image with no
mirror, or a verifier that never writes a reward. `COPY` from the build context
is **not** a drop reason -- those sources are carried as `build_context` instead.

### Example

```bash
# extract first
tar xf tasks-00000.tar -C /path/to/extracted     # -> /path/to/extracted/tasks/<id>/...

python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
    --tasks-root /path/to/extracted/tasks \
    --inject-agent-runtime \                       # terminus needs tmux (non-RTS corpora)
    --out /tmp/seed_train.jsonl
```

The row's `label` and `metadata.instance_id` are the task id that **joins back to
`metadata/tasks.parquet`** -- that is how you apply the per-dataset filters below.

---

## 3. Per-dataset filter columns (use them!)

The adapter emits every task it can build; the *quality* filtering is in the
parquet columns the dataset authors provide. Join `tasks.parquet.task_id` to the
jsonl `label` and keep the subset you want.

### TerminalWorld-Seeds-Clean

| column | values | use |
|---|---|---|
| **`reward_verdict`** | pass 861 / fail 446 / unknown 46 | **Keep `== "pass"`.** The fail+unknown 36% have a reference solution that cannot even earn reward 1, so every rollout on them scores 0 -- pure wasted generation (and with `SWE_DROP_ZERO_STD=1` they are discarded but still burn the rollout). |
| `verdict_flipped` | True 54 | flaky (network/timing); drop for stability |
| `reference_partial` | True 55 | reference is deliberately incomplete (mostly already `fail`) |

The dataset also ships the settled filter as id lists under `metadata/`:
**`train_ready_ids.txt` (663) is the canonical training subset** -- the
oracle-passed tasks minus the fragile-build, policy-blocked and
oversized-memory ids. Count it rather than quoting one: it moves whenever a task
is repaired back in or a decayed one is dropped. Prefer joining on it over re-deriving from the columns.
Clean-for-training means exactly three things: the reference solution passes
its verifier, the task builds and runs inside the sandbox platform's limits,
and the metadata reflects the task. Audit flags (instruction text quoting
verifier literals, reward-hackability) ship as measurements, not filters --
they do not gate this list.

### SWE-Smith-Seeds-Clean

| column | values | use |
|---|---|---|
| `reward_verdict` | pass (all 1,775) | already all-pass; nothing to drop here |
| **`network_required`** | True 70 | **Drop** if your grader runs `--network none`; those pass only with egress |
| **`in_main_pool`** | True 1,408 | the authors' stratified, repo-balanced, de-duplicated draw that also holds the low-training-value `func_basic` family to 5%. **Prefer this over the full 1,705.** |
| `bug_family` | pr / lm_rewrite / procedural / combine_file / combine_module / lm_modify | pr / lm_rewrite / procedural are the sweet spot; combine_* bundle multiple bugs (all-or-nothing reward, harder); lm_modify (`func_basic`) is often over-specified/easy |

Recommended subsets:
- TerminalWorld: `metadata/train_ready_ids.txt` -> 663 tasks (equivalently:
  `reward_verdict == "pass"` minus the fragile-build, policy-blocked and
  oversized-memory id lists).
- SWE-Smith: `in_main_pool and not network_required` -> 1,408 tasks.

### On instruction sufficiency (SWE-Smith)

`reward_verdict == "pass"` proves the *reference fix* works; it does **not**
guarantee an agent can solve from the instruction alone. In practice the SWE-Smith
instructions are genuine GitHub-issue text (symptom + reproduction + often a
code-location hint) and the agent localizes by exploring the repo + running the
failing tests (standard SWE-bench setup, no F2P list handed to it). Difficulty
splits by `bug_family` -- which is exactly why `in_main_pool` front-loads the
higher-value families.

---

## 4. End-to-end recipe

```bash
# 0. deps
pip install huggingface_hub pandas pyarrow

# 1. download (metadata + one tar shard) -- example for SWE-Smith
python - <<'PY'
from huggingface_hub import hf_hub_download
import shutil, os
repo = "Fzz1/SWE-Smith-Seeds-Clean"
for f in ["metadata/tasks.parquet", "data/tasks-00000.tar"]:
    shutil.copy(hf_hub_download(repo, f, repo_type="dataset"),
                os.path.join("/path/to/dl", os.path.basename(f)))
PY

# 2. extract
tar xf /path/to/dl/tasks-00000.tar -C /path/to/extracted

# 3. convert (tmux baked in for terminus)
python -m torchtitan.experiments.rl.examples.tmax.prepare_rts_data \
    --tasks-root /path/to/extracted/tasks --inject-agent-runtime \
    --out /path/to/seed_all.jsonl

# 4. filter by the parquet columns (SWE-Smith: main-pool, offline)
python - <<'PY'
import json, pandas as pd
df = pd.read_parquet("/path/to/dl/tasks.parquet")
keep = {t for t, mp, nr in zip(df.task_id, df.in_main_pool, df.network_required)
        if mp and not nr}
rows = [json.loads(l) for l in open("/path/to/seed_all.jsonl")]
with open("/path/to/seed_filtered.jsonl", "w") as f:
    for r in rows:
        if r["label"] in keep:
            f.write(json.dumps(r) + "\n")
PY

# 5. (recommended) oracle-validate a handful through the REAL sandbox path:
#    boot each task, run solve.sh, grade -- expect reward 0 (untouched) -> 1 (fixed).
#    This is the ONLY check that the base image pulls, the COPY build_context uploads,
#    and the grader fires. NOTE it does NOT exercise the agent runtime (tmux) -- only a
#    real terminus rollout does that.

# 6. upload seed_filtered.jsonl to your data store and point SWE_PROMPT_DATA at it,
#    then launch the recipe (see README.md).

# To MIX corpora, just concatenate the filtered JSONLs (label namespaces do not
# collide across datasets) and point SWE_PROMPT_DATA at the combined file.
```

---

## 5. Gotchas

- **terminus needs tmux -- bake it in at build time.** The terminus agent drives a
  `tmux` session. Its runtime self-install (harbor `_attempt_tmux_installation`:
  package manager, then from-source) **fails on the SWE-Smith conda base images**
  (`Failed to install tmux from source`), so without `--inject-agent-runtime` the
  whole SWE-Smith (or TerminalWorld) half scores reward 0. The build-time inject
  installs tmux as root with egress and caches it in the image. **The oracle smoke
  (`solve.sh` direct) does NOT catch a missing-tmux bug** -- only a real terminus
  rollout does.
- **SWE-Smith base images live on Docker Hub** (`jyangballin/swesmith.x86_64.*`).
  The Dockerfile is `FROM <that image>` + apply the bug patch. The sandbox builder
  must pull the (large) public image; make sure the build host has registry egress
  and enough disk (`TT_DAYTONA_DISK_GB`).
- **`COPY` from the build context** is handled via `build_context` (base64 in the
  row), not by uploading a context dir. This is what lets SWE-Smith's
  `COPY bug.patch ...` build from the Dockerfile alone.
- **Difficulty = `oracle_commands`, not the `difficulty` field.** The field is
  inherited from the synthesis seed and reads "easy" even for hard tasks. Cap the
  turn budget with `--max-oracle-commands` (a rollout capped at T turns cannot
  solve a task whose oracle needs more than T commands).
- **`reward_verdict` filtering pays for itself** under `SWE_DROP_ZERO_STD=1`: an
  unsolvable-by-reference task returns an all-zero-reward group that is discarded
  but still burned rollout budget. Dropping fail/unknown up front removes that
  waste.
