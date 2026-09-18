# What the training side already neutralises, and what it cannot

A rubric finding is not automatically a task to repair or drop. Some of what V12 reports is already handled
generically by the grading harness on `yichuan/qwen35-port-cotrain`; those tasks need a data entry, not a verifier
change. This note says which findings map onto the existing mechanism, which do not, and how to extract the list
from the judge rows mechanically.

## The mechanism that already exists

`torchtitan/experiments/rl/examples/tmax/grading.py` runs two integrity steps before the verifier:

1. **Integrity baseline** (`integrity_baseline.py`, used at `grading.py:301`). A task row may carry
   `protected_paths` (and `protected_cmds`). The rollouter digests those entries right after setup and hands the
   digests to `grade_tmax` as `baseline_digests`; the same entries are re-digested just before the verifier runs and
   any difference scores the episode 0 **without running the verifier**. A row with protected entries and no
   baseline raises rather than silently skipping.
2. **`pre_test_sh`** (`grading.py:325`, PR #56), the earlier per-task assert-refuse pin check, keyed by task id and
   guarded by an environment-identity check. Rows with protected entries do not consult it.

Both are assert-refuse: they do not restore a mutated file, they refuse the episode that mutated it.

## The class this covers: movable ground truth (V12 rule A4)

V12 fires A4 (`expectation_movable`) when the verifier computes its expected value **at grade time** from material
that is present during rollout and that the verifier neither hash-pins nor regenerates — so an agent that edits that
material moves the target. Examples from the validation set: a wav file the verifier re-simulates against
(`task_000056_f726766b`), log files whose records the verifier derives the expectation from
(`task_000927_43407097`), a raw syslog plus the hashing tool the verifier executes (`task_000681_6f824421`).

For exactly this shape, listing the path in the row's `protected_paths` closes the hole with no change to
`tests/test.sh`: the digest taken after setup will not match the digest taken before grading, and the episode scores
0. **These tasks are usable as they are** once the path is protected; they should not be counted as tasks needing a
bounded verifier repair, and they must not be dropped.

V12 already separates this from the case where protecting the path would be wrong. When the instruction asks the
agent to change the material and the verifier grades its new state (a database migration, an in-place source fix),
V12 records it in `excluded_material` with exclusion `expected_edit` and A4 does not fire. **Never protect an
`expected_edit` path** — doing so would refuse honest episodes.

## Extracting the list from the rows

`tools/score_v12.py` carries the test as `hook_replaceable(row)`:

- **pure** — the only floor that fired is `expectation_movable`, the tier is REPAIR, and the named `verifier_fix` is
  a pin/regenerate of that material. Protect the path in the row and the task needs nothing else.
- **partial** — A4 fired with a pin-shaped fix, but another floor fired too (an oracle, an over-specific check).
  Protecting the path removes one mechanism; the other still needs its own repair.

On the 70-judgment validation run the tag selects 6 pure rows (the three A4 control tasks above, each judged twice)
and 4 partial rows (`task_001068_ba585e13` A1+A4, `task_000240_83a610a2` A4+B1, `task_000369_e855128a`). The tag
matches on the fix text, so treat it as triage: read the row's `expectation_movable` anchor before protecting a path.

## What the harness cannot fix — these stay per-task

- **A1 `oracle_reachable`** — an in-image program the submitted deliverable can call instead of doing the work.
  Removing or renaming it at grade time does not help: the agent copied it during rollout. Denying it during rollout
  can make the task unsolvable when the instruction tells the agent to call it. The fix is in `tests/test.sh`
  (rebuild the reference from source embedded in the test, or exercise the deliverable where the oracle is absent).
- **A2 / A3 leaks on the expected side or in readable material** — protection does not remove the value from the
  image; the verifier has to stop reading it or the expected value has to be generated at grade time.
- **A5 `untouched_image_passes: yes`** — the untouched image already scores; a property of the task.
- **B1 `overspecific_check`** — a wrong hardcoded number or a source-text grep rejects faithful solutions; only the
  verifier can fix it.
- **B3 `unstable_reward`** — a wall-clock threshold. Running graders in isolation reduces noise but does not correct
  a threshold the intended solution cannot meet.

## A second harness-level class: unresolved verifier dependencies

When `tests/test.sh` imports a module `setup.sh` never installs, V12 records `unconfirmed_dependency` and **does not
change the tier** — the row is PASS-held or REPAIR-held and the controller carries the hold. In the staged sample
this is 18% of tasks (mostly `requests`). One probe on one image of that class settles the whole class: run
`python3 -c "import requests"` in the graded container. If it imports, list the module in the prompt's base-image
fact and the rule stops firing; if it does not, the class is a real `env_mismatch` and the tasks need the dependency
installed. Until then no task is rejected for it.

## Suggested use of a V12 run

1. Rows tagged **pure** → add the `expectation_movable` path to that task's `protected_paths`; keep the task.
2. Rows tagged **partial** → protect the path, then repair the remaining mechanism named in the row.
3. Rows with `unconfirmed_dependency` and no other finding → hold, run the base-image probe once, release.
4. Everything else → `verifier_fix` names a change confined to `tests/test.sh`, or `reject_shape` names why no such
   change exists.
