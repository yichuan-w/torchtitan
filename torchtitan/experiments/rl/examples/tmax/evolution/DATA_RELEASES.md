# Prepared data releases

Data releases accept a JSON configuration listing each upstream dataset repository,
full commit SHA, metadata path, task archives and selected row count. A null count
includes every published task. Rebench and SWE-Smith use measured peaks with 30%
headroom; TMax uses its published allocations without scaling them again. Missing
SWE peaks receive an explicit 2 GiB allocation per missing dimension.

The generated manifest records these inputs, code commit, dependency lock digest
and every output file digest. Per-task checkpoints and a timestamped build log
support resuming interrupted preparation. Identical inputs and preparation code
produce the same release SHA256. The release includes the prepared mix and source
packages needed by evolution.

Run from a clean, committed checkout with the project's Python environment:

```bash
python torchtitan/experiments/rl/examples/tmax/evolution/data_release.py build \
  --config torchtitan/experiments/rl/examples/tmax/evolution/data_rebench_tmax.json \
  --out ./data/prepared
```

The command prints `./data/prepared/releases/<release-sha256>`. Logs and resumable
checkpoints are under `./data/prepared/work/<input-key>/`. The supplied configuration
includes all 1,317 Rebench tasks and 452 TMax tasks at its pinned revisions, without
oversampling. Change `count` to select a deterministic subset of a source.

To add SWE-Smith, append this source to the configuration:

```json
{
  "name": "swe-smith",
  "repo": "Fzz1/SWE-Smith-Seeds-Clean",
  "revision": "58af1819c6a51fecd6662bd6d479d16e66514d19",
  "adapter": "swe_peaks",
  "metadata": "metadata/tasks.parquet",
  "archives": ["data/tasks-00000.tar"],
  "count": null
}
```

`data_agentpick.json` imports `Fzz1/tb-training-mix-agentpick`, one source per
split. Its TMax and CalibForge packages ship no `solution/`; the reference
solution is the row's `solution` column, a recorded action list. The
`tmax_reaudit` adapter writes that column to `solution/gpt6_actions.json`, the
file longlongcheck packages carry, because evolution reads the solution from the
package only. The training row does not change. A source with
`"skip_unsolved": true` leaves out every task that ends up with no reference
solution, and `"skip_unpackable": true` every package the row builder refuses
(for example a binary test fixture); both are logged by id in the build log.
`"exclude": [<task id>, ...]` leaves out listed tasks by id (logged the same
way). `data_agentpick_fast_audited.json` is `data_agentpick_fast.json` minus the
107 rows a 2026-10-24 audit found broken (wrong hardcoded answers, impossible
tasks, rewards without the work, live third-party repos, agent-plantable tests). `data_agentpick_clean_only.json` keeps only its clean_fast and calibforge_clean_fast sources (723 rows) at revision c180c10b, which gives tw_17238, tw_17688 and system-administration_20260605_015435_004 the 4 CPU / 8 GB / 4 GB their rollouts measured (at 1/1/1 their sandboxes failed to start), raises 18 run-1026b OOM/memory/disk rows to 8 GB RAM / 10 GB disk and optimization_20260606_172622_001 to 4 CPU (CPU otherwise unchanged), and excludes the rows run 1024 showed broken under miniswe: wrong or unstated verifier checks, impossible data, agda REPL tasks, a live-github grader, an agent-writable test path, and three rows the harness cannot grade (servers that must outlive a command, a stale agent server, a 1 GiB build disk), and tw_14495, whose debian:jessie tmux cannot report pid/socket so Terminus fails at turn 0 (it passes under miniswe). `data_agentpick.json` does not set it: its 104 unsolved repair rows
are imported without one, and the evolution loop's first easier rewrite of each
writes it (`EVOLVE_LOOP.md`, step 2).

When an upstream snapshot includes `metadata/cache_manifest.jsonl`, preparation
downloads and verifies the listed offline cache archives at the same commit. Tasks
marked `prefetched_cache_required` receive a Dockerfile step that fetches the
fixed-version archive, checks its SHA256 and unpacks it while building the image.
Both the cache archives and the prepared source Dockerfiles are retained in the
release so evolution starts from the same setup. Published
`validated_test_timeout_s` values become the row's verifier timeout.

Publication adds a release path to an existing HF dataset. Supply a project-scoped
token file with write access to that destination:

```bash
python torchtitan/experiments/rl/examples/tmax/evolution/data_release.py \
  --token-file ./secrets/hf-token publish \
  --release ./data/prepared/releases/<release-sha256> --repo <owner/dataset>
```

The command prints the HF commit SHA, release SHA256 and archive SHA256 after
downloading the published archive and checking it against the local file.
Retrieval requires both the HF commit SHA and release SHA256, then verifies every
extracted file:

```bash
python torchtitan/experiments/rl/examples/tmax/evolution/data_release.py fetch \
  --repo <owner/dataset> --revision <full-hf-commit-sha> \
  --release-sha256 <release-sha256> --out ./data/downloaded-release
python torchtitan/experiments/rl/examples/tmax/new_root.py \
  --base ./experiments/new-run --data-release ./data/downloaded-release
```

`new_root.py --data-release` consumes the verified mix and source directories
together. Keep the downloaded release available: the experiment links its source
directories for evolution.

Prepared Dockerfiles require tmux at image build time. This requirement does not
prove every image has been built: upstream image tags and package repositories can
still change. Reproducibility of the prepared data and Docker images is recorded
separately. Existing experiments do not change when a release is published.
