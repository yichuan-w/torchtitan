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
