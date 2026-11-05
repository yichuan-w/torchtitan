# Data audit: agentpick clean-only mix after run 1026b (2026-10-26)

Handoff for whoever maintains `data_agentpick_clean_only.json` (release `965a2f05`,
724 rows: clean_fast 335 + calibforge_clean_fast 389). It lists the tasks that
wasted compute or misbehaved in the first ~90 training steps of run 1026b, with a
recommended action for each. Nothing here has been applied yet.

**Scope.** Training groups only: 52,754 rollouts over all 724 tasks (6 groups x 12
rollouts each for most), Terminus-2 harness, 96K context, 180 turns, binary
verifier reward, unsubmitted rollouts graded. Per-task numbers are by original task
id; about 500 tasks were rewritten by the evolve loop during the run and their
revisions are counted under the original id. Solve counts come from the run's
training lineage (`selected` events); error and sandbox-issue counts come from the
controller log. Verifier tails are attributed to rollouts by worker order and are
approximate (~1% mismatch).

**Overall.** Infra failure is small: 455 rollouts (0.9%) errored. The cost is
elsewhere: the 60 tasks that never solved took 10.8% of rollout compute, and the 10
near-always-solved tasks gave almost no learning signal. Excluding or fixing the
tasks below recovers about 12% of rollout compute.

## 1. Exclude

| task_id | cpu/mem/disk | rollouts | solve | errors | why |
|---|---|---|---|---|---|
| `tw_14495` | 1/1/1 | 72 | 0 | 72 (100%) | Image is `debian:jessie`; its tmux cannot report pid/socket, so every Terminus rollout fails at turn 0 (`terminal_identity_invalid`). It was restored in f8a4712e2 because it passes .29 under miniswe; under Terminus it cannot run. Exclude for Terminus runs, or rebase the image onto a release with tmux >= 2.x that supports `display-message -p '#{pid}'`. |
| `software-engineering_20260508_045740_011` | 1/1/1 | 72 | 0 | 32 (44%) | The task is about process pools and zombie reaping; the agent's process killing plus 1 GB memory take the terminal down (`terminal_signal_unknown` 16, `connection_lost` 11). |
| `software-engineering_20260417_112147_020` | 1/1/1 | 72 | 72/72 | 0 | Always solved: trivial, or the verifier always passes. Harden the verifier or exclude. |

## 2. Raise resources (then re-measure)

| task_id | now cpu/mem/disk | rollouts | solve | evidence | suggested |
|---|---|---|---|---|---|
| `data-querying_20260508_045800_001` | 2/2/2 | 72 | .07 | oom_suspect 57/72, sandbox lost 12 | mem 8 GB |
| `data-science_20260607_104421_004` | 1/1/1 | 72 | .50 | agent fills the 1 GB disk (8 disk-exhausted), then the verifier's venv breaks (`pytest is not installed`, `No module named 'ctrf'` ~23x) | disk 4 GB |
| `task_002324_f03c9c85` | 1/1/1 | 77 | .53 | service task; sandbox lost 10 under memory pressure | mem 2-4 GB |
| `software-engineering_20260614_141608_024` | 1/1/1 | 72 | 0 | the verifier itself is OOM-killed (pytest killed in 21 runs) | mem 2-4 GB |
| `system-administration_20260601_053159_008` | 1/1/1 | 72 | 0 | multi-service daemon task; terminal lost 19, oom_suspect 31 | 2/4/4, else exclude |
| `system-administration_20260615_143301_008` | 1/1/1 | 72 | 0 | telnet/login server; terminal lost 14, oom_suspect 20 | 2/4/4, else exclude |
| `pymc-devs__pymc-7637` | 1/2/4 | 72 | .22 | 47 command timeouts (600 s): the test suite is too slow on 1 CPU | 2-4 CPU |
| `optimization_20260606_172622_001`, `mathematics_20260511_015840_001`, `software-engineering_20260416_133458_003`, `software-engineering_20260604_004411_011`, `software-engineering_20260508_045557_011` | various | 72 each | 0-.20 | 18-23 command timeouts each (benchmarks, brute force, builds) | raise CPU; exclude if still 0 |

Disk-exhausted rollouts (44 total) cluster on 1-2 GB disks: besides the row above,
`tw_338534` (4) and `tw_18271` (3).

## 3. Never solved in any group (60 tasks)

54 calibforge, 6 clean-fast; 4,335 rollouts, 6.0M rollout-seconds (10.8% of
compute). 56 of them were rewritten "easier" mid-run and stayed at 0. For most the
verifier runs normally and the core tests fail on substance (LLL recovery, PAX tar,
LZ decompressors, git smart-HTTP/cgit, SMTP->IMAP), so they look agent-limited
rather than broken. Ones with environment or resource evidence: `tw_14495` (above),
`software-engineering_20260614_141608_024` and `software-engineering_20260606_172554_016`
(verifier OOM), the 1/1/1 sysadmin service tasks
`system-administration_20260606_172606_004`, `_20260601_053159_005`,
`_20260610_111012_007`, `_20260615_143956_008`, `_20260611_205750_004` (median 134-167
turns, oom_suspect in up to 34/72 rollouts), `scientific-computing_20260604_144210_001`
(timeouts; `pytest is not installed` in the verifier), and
`software-engineering_20260501_051538_013` (qemu-riscv killed by timeout).

**Recommendation:** exclude all 60 from the next run, or first put them through a
strong-model zero-of-N check and keep the sysadmin ones only if they pass at 2-4 GB.

| task_id | corpus | cpu/mem/disk | groups | rollouts |
|---|---|---|---|---|
| `data-processing_20260528_174918_003` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `data-science_20260612_191200_002` | calibforge-clean-fast | 2/2/2 | 6 | 72 |
| `data-science_20260617_210354_003` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `debugging_20260508_204815_005` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `file-operations_20260415_182321_004` | calibforge-clean-fast | 1/1/1 | 7 | 84 |
| `file-operations_20260416_172928_002` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `file-operations_20260425_072438_002` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `file-operations_20260501_172213_004` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `file-operations_20260602_105139_002` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `file-operations_20260604_004411_003` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `file-operations_20260605_015435_002` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `file-operations_20260611_112034_004` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `games_20260604_002443_001` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `machine-learning_20260508_180627_002` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `mathematics_20260422_235114_002` | calibforge-clean-fast | 1/1/1 | 6 | 69 |
| `mathematics_20260511_015840_001` | calibforge-clean-fast | 4/4/4 | 6 | 72 |
| `mathematics_20260606_172606_002` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `mathematics_20260610_112157_001` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `optimization_20260606_172622_001` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `personal-assistant_20260414_211453_001` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `scientific-computing_20260513_204848_004` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `scientific-computing_20260603_181444_003` | calibforge-clean-fast | 1/1/1 | 5 | 60 |
| `scientific-computing_20260604_144210_001` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `security_20260514_012509_001` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `security_20260528_174918_002` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `security_20260603_181444_007` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `security_20260604_003113_003` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `security_20260604_193356_002` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `software-engineering_20260419_023954_005` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260419_112101_004` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260429_103257_026` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `software-engineering_20260501_051538_013` | calibforge-clean-fast | 1/1/1 | 6 | 70 |
| `software-engineering_20260503_030115_014` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260508_045740_011` | calibforge-clean-fast | 1/1/1 | 6 | 40 |
| `software-engineering_20260508_180627_010` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260509_205636_022` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260601_053159_004` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `software-engineering_20260604_004411_014` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260604_193056_015` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260606_172554_016` | calibforge-clean-fast | 1/1/1 | 6 | 68 |
| `software-engineering_20260610_111012_016` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `software-engineering_20260610_112157_021` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `software-engineering_20260613_152233_015` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `software-engineering_20260613_152233_017` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `software-engineering_20260614_141608_024` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `system-administration_20260601_053159_005` | calibforge-clean-fast | 1/1/1 | 6 | 70 |
| `system-administration_20260601_053159_008` | calibforge-clean-fast | 1/1/1 | 6 | 53 |
| `system-administration_20260604_002443_004` | calibforge-clean-fast | 2/2/2 | 6 | 71 |
| `system-administration_20260606_172606_004` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `system-administration_20260610_111012_007` | calibforge-clean-fast | 1/1/1 | 6 | 72 |
| `system-administration_20260611_205750_004` | calibforge-clean-fast | 1/1/1 | 6 | 71 |
| `system-administration_20260615_143301_008` | calibforge-clean-fast | 1/1/1 | 6 | 58 |
| `system-administration_20260615_143956_008` | calibforge-clean-fast | 1/1/1 | 5 | 56 |
| `video-processing_20260604_144210_001` | calibforge-clean-fast | 2/2/2 | 6 | 72 |
| `pallets__werkzeug-2596` | clean-fast | 1/1/4 | 6 | 72 |
| `pyccel__pyccel-2038` | clean-fast | 1/1/4 | 6 | 72 |
| `pymc-devs__pytensor-1231` | clean-fast | 1/1/4 | 6 | 72 |
| `rts_task_0b9b2bac6586b91383a541a9` | clean-fast | 1/2/6 | 6 | 68 |
| `tw_14495` | clean-fast | 1/1/1 | 6 | 0 |
| `tw_733601` | clean-fast | 1/1/2 | 6 | 71 |

## 4. Near-always solved (>= 11/12 in every group)

Almost no learning signal; exclude, or leave them to the "harder" rewriter.

| task_id | groups | solved |
|---|---|---|
| `data-science_20260506_213248_004` | 6 | 68/72 |
| `mathematics_20260418_044216_001` | 6 | 68/72 |
| `pallets__markupsafe.620c06c9.func_basic__09grfhrm` | 6 | 66/71 |
| `software-engineering_20260414_091921_009` | 6 | 71/72 |
| `software-engineering_20260417_112147_020` | 6 | 72/72 |
| `task_000022_21389603` | 6 | 71/72 |
| `task_000092_f4073751` | 6 | 71/72 |
| `task_002064_da683645` | 6 | 71/72 |
| `task_006486_83be373e` | 6 | 71/72 |
| `tw_229615` | 7 | 82/84 |

## 5. Sandbox issues by kind (training, 970 total)

| kind | count | note |
|---|---|---|
| command_status_timeout | 332 | spread widely |
| command_timeout | 301 | concentrated in the section 2 timeout tasks |
| sandbox_lost | 147 | `data-querying_20260508_045800_001` 12, `task_002324_f03c9c85` 10, `tw_100135` 6, `system-administration_20260610_111702_002` 5 |
| command_launch_lost | 57 | |
| command_disk_exhausted | 44 | all on 1-2 GB disks |
| delete_failed | 38 | |
| file_upload_failed | 21 | |
| file_download_missing | 14 | |
| create_retry | 13 | transient platform 502s; no task repeatedly failed to boot or build |

Verifier-side infrastructure failures are rare (pytest/ctrf missing 54, pytest killed
39, ModuleNotFoundError 38, no space left 22, real network failures ~8) and mostly
follow from the agent filling the disk or exhausting memory.

## How to apply

- Exclusions: add the ids to the `exclude` list of the matching source in
  `data_agentpick_clean_only.json` and rebuild the release (`data_release.py build`).
- Resources: rows carry `daytona_cpu` / `daytona_mem_gb` / `daytona_disk_gb`; raise
  them in the source or in the release builder, then re-run the batch canary.
- Re-measure the resized tasks before trusting their new solve rate.
