# Layout: one experiment root, and where everything under it lives

This file is the contract. `layout.py` implements the paths, `rollout_record.py`
the rollout file format; every producer and consumer under `tmax/` goes through
those two modules and never spells a path itself.

## Rules

1. **A process writes only under the directory it owns.** The trainer writes
   only its run directory. The evolve loop writes only `evolution/` and
   `data/mix/`. The trainer reads `evolution/status.json`; the loop reads
   `runs/*/signals/` and `runs/*/rollouts/`. Nothing else crosses.
2. **One unit of work, one directory, with everything it read and produced
   inside it.** One process lifetime is one run. One handled signal is one
   rewrite. One codex invocation is one session. A task's whole history is one
   task directory.
3. **Identity is in the name.** `<what>--<UTC stamp>`; anything about a task
   starts with the task id. Stamps are `YYYYMMDD-HHMMSSZ`, so names sort by
   time. No random suffixes.
4. **One rollout file format.** The trainer writes each rollout once as JSONL;
   a signal references those files; a rewrite hardlinks them into its package.
5. **Paths are not configuration.** `TRL_BASE` names the root; the launcher
   exports `TRL_RUN_DIR`; everything else is a convention in `layout.py`.
   Switches are booleans and never carry a path.
6. **Every fact has one home.** Elsewhere there are references (a path, a
   version number), hardlinks (the same inode), or a cache that says it is one
   and is rebuilt from its sources.

## The tree

```
$TRL_BASE/
├── experiment.json               name, created, profile, purpose, seed_mix_version, forked_from
├── bin/                          codex, jq
├── data/
│   ├── sources/…                 corpora as obtained; read-only
│   ├── evalsets/<name>.jsonl     fixed evaluation sets (tb2_eval.jsonl); never evolved
│   └── mix/
│       ├── history/v0001--<stamp>.jsonl           every version ever served; append-only
│       ├── history/v0001--<stamp>.manifest.json   version, parent_version, sha256, rows, stamp
│       ├── history/v0001--<stamp>.inputs.json     the seed builder's own manifest, copied in (v1 only)
│       └── live.jsonl            a hardlink to the current version (replaced atomically); never edited in place
├── runs/
│   ├── latest -> tmax-9b--<stamp>
│   └── tmax-9b--<stamp>/         one process lifetime; only the trainer writes here
│       ├── launch.json           profile, tt commit, mix version+sha, gpus, env, resumed_from, checkpoint_step
│       ├── launch.diff           the checkout's uncommitted changes, when tt_commit ends in -dirty
│       ├── stdout.log
│       ├── inputs/mix.jsonl      hardlink to the history version loaded at boot
│       ├── trainer/              structured logs, metrics, profiling
│       │   └── mix_versions.jsonl   one line per boot or hot reload: stamp, event, version, sha256, replaced, appended, retired
│       │                              (which step ran on which version: join the stamp with the structured logs' step times)
│       ├── checkpoints -> <local disk>/ckpt/tmax-9b--<stamp>   rolled by SWE_CKPT_KEEP
│       ├── checkpoints-held/step-<N>/   hardlinks of a step kept past the rotation (cp -al from checkpoints/)
│       ├── checkpoints-staged/step-<N>/ a GPFS copy for eval nodes, made and pruned by eval_watcher.sh
│       ├── checkpoints-mirror/step-<N>/ the newest complete step, copied to GPFS by ckpt_mirror.sh; one at a time
│       ├── rollouts/<task>/g<group>-r<idx>.jsonl   one file per rollout (format below)
│       │                     …/g<group>-r<idx>.pane    the terminal transcript, when TMAX_PANE_DUMP=1
│       ├── signals/<task>--g<group>.json           one per zero-variance training group
│       └── advisories/{infra_quarantine,no_tmux}.jsonl   per-task warnings; append-only
├── evals/<stamp>--<run>-step<N>/ an evaluation is neither a run nor the loop
├── evolution/                    only the loop writes here
│   ├── loop.log  loop.lock  loop.env
│   ├── ledger.jsonl              one line per signal seen: when, which rewrite, outcome
│   ├── status.json               rebuilt every round from ledger + lineage; the trainer reads it
│   └── tasks/<task>/
│       ├── lineage.jsonl         this task's rewrite and fold events
│       ├── r0/ r1/ …             accepted revisions, package only; r0 is the seed, copied
│       │                     from data/sources/<corpus>/tasks/<task> by the loop on the
│       │                     task's first signal
│       └── rewrites/<stamp>--<job>/          one handled signal (job = harder | easier)
│           ├── rewrite.json      signal, input rev, status, verdicts, resources, result rev
│           ├── pretest.json      the input row's pin hook (pre_test_sh + environment stamp); only when the row has one
│           ├── package/          the working copy; renamed to r<N+1>/ when accepted
│           │   └── traces/attempt-NN.jsonl   hardlinks to the run's rollout records
│           └── sessions/<stamp>--<kind>/     one codex invocation (agent, repair, verifier, oracle)
│               ├── session.json  prompt.md  stdout.txt  stderr.txt
│               └── codex/        the CLI's own session jsonl
└── logs/<tool>--<stamp>.log      one-shot tools that belong to neither a run nor the loop;
                                  a tool with more than a log writes a directory logs/<tool>--<stamp>/
```

Gone, with their contents at the places above: `consumed/`, `junk/`,
`deferred_easier/`, `parents/`, `retuned/`, `signals/codex_traces/`, `meta/`,
`evolution_stats.json`, `evolution_lineage.jsonl`, `mix_snapshot.jsonl`, the
flat `logs/`, `rollout-dumps/`, `exec-traces/`, `*.sandbox.json`, `launch.info`.

## Environment

Kept: `TRL_PROFILE`, `TRL_BASE`, `TRL_TT`, `TRL_MODEL`, `TRL_VENV`, and every
`SWE_*` / `TMAX_*` / `TT_DAYTONA_*` that configures training itself.
Added: `TRL_RUN_DIR` (exported by the launcher; the trainer writes there).
Switches: `SWE_ROLLOUT_RECORDS` (default 1) writes `rollouts/`;
`SWE_EVOLUTION_SIGNALS` (default 1) writes `signals/`.
Removed: `SWE_TASK_EVOLUTION_DIR`, `SWE_ROLLOUT_DUMP_DIR`, `TMAX_EXEC_TRACE_DIR`,
`SWE_ZERO_STD_DIR`, `SWE_EVOLUTION_LINEAGE`, `SWE_EVOLUTION_STATS`,
`SWE_EVOLUTION_TRACE_DIR`.

## Formats

All JSONL lines are one JSON object each, UTF-8, `ensure_ascii=False`. All
times are `stamp` strings (UTC) unless the key ends in `_unix_ns`.

### Rollout record `runs/<run>/rollouts/<task>/g<group>-r<idx>.jsonl`

Line 1, the rollout:

```json
{"task": "tw_380466", "rev": 2, "run": "tmax-9b--20260904-181500Z",
 "group": 713, "rollout": 13, "reward": 1.0, "status": "completed",
 "finish_reason": "submit", "submitted": true, "format_errors": 0,
 "infra_failed": false, "error": "",
 "sandbox": {"id": "f2ee…", "disk_gb": 2, "issues": {}, "dropped_details": 0},
 "secs": 412.3, "budget_sec": 1800, "turns": 7, "started": "20260904-182201Z",
 "exec": [{"t": 1725474121.1, "secs": 0.4, "exit": 0, "cmd": "tmux send-keys …"}]}
```

Every later line, one turn, keys in reading order:

```json
{"turn": 1, "keystrokes": ["ls -la /app\n"], "task_complete": false,
 "output": "New Terminal Output:\n…", "analysis": "…", "plan": "…", "think": "…"}
```

`keystrokes` is a list (a turn may send several); empty on a closing turn. A
turn whose completion holds no Terminus response keeps its text under `raw`
instead of `keystrokes`. `output` is the terminal reply the next turn was
prompted with; empty on the last turn. Validation groups (negative ids) write
no records; the controller's validation report owns those.

### Signal `runs/<run>/signals/<task>--g<group>.json`

```json
{"task": "tw_380466", "rev": 2, "run": "tmax-9b--20260904-181500Z", "group": 713,
 "direction": "harder", "solved": 16, "total": 16, "created": "20260904-183012Z",
 "attempts": ["rollouts/tw_380466/g713-r0.jsonl", "…", "rollouts/tw_380466/g713-r15.jsonl"]}
```

`attempts` are paths relative to the run directory, in rollout order. Written
as `<name>.incoming` and renamed into place. `direction` is `harder` for an
all-pass group and `easier` for an all-fail group. An all-fail group in which
no attempt took a turn is not a signal; it is an `infra_quarantine` advisory.

### Advisory `runs/<run>/advisories/<name>.jsonl`

```json
{"stamp": "20260904-183012Z", "task": "tw_380466", "image": "…", "reason": "all_fail_zero_turns", "group": 713, "rollouts_lost": 16}
{"stamp": "20260904-183012Z", "task": "tw_266088", "image": "…", "reason": "no_tmux", "group": 714, "rollout": 3}
```

`infra_quarantine` has one line per group that died at zero turns;
`no_tmux` one per rollout whose image lacked tmux (a warning, not a verdict:
Terminus usually installs it at runtime).

### Ledger `evolution/ledger.jsonl`

```json
{"stamp": "20260904-183300Z", "signal": "tmax-9b--20260904-181500Z/tw_380466--g713",
 "task": "tw_380466", "rev": 2, "run": "tmax-9b--20260904-181500Z", "group": 713,
 "direction": "harder", "outcome": "handled", "rewrite": "tasks/tw_380466/rewrites/20260904-183300Z--harder"}
```

`outcome` is `handled`, `deferred` (the direction is switched off; replayed
when it is switched on), `junk` (unreadable, unknown task, a rev the task
never had), or `superseded` with a `reason`: a second pending signal for the
same task and rev, or a signal about a rev the task has already moved past
(groups still in flight when the mix reloaded). The loop handles one signal
per task per round, the newest whose `rev` is the task's latest. The loop's
"pending" set is every file under `runs/*/signals/` whose `signal` id has no
ledger line. Handling a deferred signal later appends a new line; the old one
stays. A signal whose handling failed before its rewrite directory existed
(the copy of `r<rev>` or the hardlinks could not be made) gets no ledger
line and is retried next round; `loop.log` holds the traceback.

### Rewrite `tasks/<task>/rewrites/<stamp>--<job>/rewrite.json`

```json
{"task": "tw_380466", "job": "harder", "signal": "tmax-9b--…/tw_380466--g713",
 "input_rev": 2, "started": "…", "finished": "…", "status": "accepted",
 "stage": null, "reason": null,
 "operator": "container_build_alignment", "arm": "codex",
 "verdicts": {"oracle": "pass", "dark_paths": [], "dark_literals": [], "step": []},
 "resources": {"cpu": 2, "mem_gb": 4, "disk_gb": 6, "source": "measured"},
 "result_rev": 3, "sessions": ["sessions/20260904-183301Z--agent", "sessions/20260904-184010Z--repair"]}
```

`stage` names the check that settled a non-accepted rewrite (`oracle`,
`dark_literals`, `step`, `setup`, `fold`, …) and `reason` says why in a
sentence; both are null on an accepted one. `status` is `running`, `accepted`, `rejected`, `blocked`, `failed`,
`interrupted` (the loop died with it running; `finalize_interrupted_traces.py`
marks it), or `kept`. An accepted rewrite stays `running` until the round's
fold renames its package, so a loop that dies between the two reads as
interrupted rather than as accepted with no revision. `kept` is
the agent's own verdict that none of the offered axes fits the task, so the
task stays as it is: neither a success nor a failure of the pipeline, and
left out of acceptance rates. A rejected or failed rewrite keeps its `package/`. On `accepted`, `package/` is renamed
to `r<result_rev>/` after the harness files (`AGENTS.md`, `sandbox`, `run/`,
`traces/`) are removed from it, so an accepted rewrite has no `package/`.

### Pretest `tasks/<task>/rewrites/<stamp>--<job>/pretest.json`

```json
{"pre_test_sh": "set -u\n…", "pretest_env_identity": "image:hamishi740/swerl-tmax-v3:37a79d0fd9b9"}
```

The pin hook the input row carried (`metadata.tmax.pre_test_sh` and the
environment identity its pins were captured against), snapshotted by the loop
when the rewrite starts; absent for a row without one. A package holds no
hook, so this file is what the loop's probe grades with
(`daytona_revalidate.py --pretest-file`), and the harness copies it into the
package as `run/pretest.json` for the agent's own `./sandbox check`. At the
fold the hook returns to the row from the row it replaces, and the row
builder derives the new package's environment identity beside the stamp: a
rewrite that kept the environment keeps the check, one that rebuilt it is
skipped by grading.

### Session `…/sessions/<stamp>--<kind>/session.json`

```json
{"kind": "agent", "model": "gpt-5.6", "reasoning_effort": "high", "driver": "exec",
 "started": "…", "finished": "…", "status": "completed", "exit_code": 0,
 "error": null, "timeout_sec": 2400, "filtered": false, "resumes": null}
```

`kind` is `agent` (the rewrite itself), `repair`, `verifier` (blind verifier
author), `oracle`. `prompt.md`, `stdout.txt`, `stderr.txt` sit beside it;
`codex/` holds the CLI's session jsonl and nothing else of its home. A
`verifier` session works in its own `package/` (it must not see the
solution); a `repair` session resumes the thread of the session it names in
`resumes`. Under `EVOLVE_CODEX_DRIVER=sdk` the session also holds
`events.jsonl` (the app-server event stream) and `sdk.json` (the driver's
own record). `status` is `running`, `completed`, `blocked`, `failed`, or
`interrupted`.

### Lineage `tasks/<task>/lineage.jsonl`

```json
{"stamp": "…", "event": "rewrite", "rewrite": "rewrites/20260904-183300Z--harder", "job": "harder", "input_rev": 2, "status": "accepted"}
{"stamp": "…", "event": "fold", "from_rev": 2, "to_rev": 3, "mix_version": 42, "rewrite": "rewrites/20260904-183300Z--harder"}
```

Two events. `rewrite` is an index line; `rewrite.json` is authoritative for
the rewrite's details. `fold` is the only record of a revision entering the
mix. Signals are not here; they are in the ledger.

### Status `evolution/status.json`

```json
{"updated": "…", "mix_version": 42, "pending": 3, "handled": 478, "deferred": 184,
 "junk": 2, "rewrites_running": 4, "accepted": 120, "rejected": {"oracle": 30, "dark_literals": 8},
 "blocked": 12, "failed": 5, "kept": 9}
```

Rebuilt from the ledger and every task's lineage and rewrite files at the end
of each round, written atomically. Losing it loses nothing. `rejected` is
keyed by the stage that rejected; `interrupted` rewrites count under `failed`;
`superseded` counts the ledger lines with that outcome.

### Mix manifest `data/mix/history/v<N>--<stamp>.manifest.json`

```json
{"version": 42, "parent_version": 41, "stamp": "…", "sha256": "…", "rows": 663}
```

Which rows changed between versions is in the `fold` lines of the tasks'
lineage files, keyed by `mix_version`. Rows carry `metadata.rev`; the seed is
rev 0.

`live.jsonl` shares its inode with the history file, so writing into it in
place would rewrite history; every change to the mix, the loop's folds and a
tool's edits alike, goes through `layout.MixDir.publish(rows)`, which writes
the next version and moves the link.

### Launch `runs/<run>/launch.json`

```json
{"run": "tmax-9b--…", "started": "…", "profile": "andy", "tt": "/…/torchtitan", "tt_commit": "5726ab1e",
 "mix_version": 41, "mix_sha256": "…", "gpus": "2,0,6,1,4", "resumed_from": null, "checkpoint_step": null,
 "env": {"SWE_GROUP_SIZE": "16", "…": "…"}}
```

### Experiment `experiment.json`

```json
{"name": "tw-evolve-sep", "created": "…", "profile": "andy", "purpose": "…", "seed_mix_version": 1,
 "seed_mix": {"path": "/…/mix_tw_20260904.jsonl", "sha256": "…", "inputs": "data/mix/history/v0001--<stamp>.inputs.json"},
 "forked_from": null}
```

## Checkpoints

The trainer writes checkpoints to the host-local disk and keeps the last
`SWE_CKPT_KEEP` steps; `runs/<run>/checkpoints` links there, and a resume
chain shares one such directory. A step that has to outlive the rotation (one
an eval is queued on, one worth comparing later) is held as hardlinks:
`cp -al checkpoints/step-<N> checkpoints-held/step-<N>` costs no bytes and
keeps the inodes alive when the trainer unlinks its copy, so the space is
freed only when the held copy is removed. A copy that eval nodes can read
(they see GPFS only) is `checkpoints-staged/step-<N>`, which
`eval_watcher.sh` makes before submitting and prunes when the job leaves the
queue. The local disk is one box with no backup, so `ckpt_mirror.sh` (run
from a timer) keeps the newest complete step of `runs/latest` in
`checkpoints-mirror/step-<N>` on GPFS and drops the previous mirror when a
newer step is complete: one checkpoint of quota buys a run that survives the
box. None of the three directories is written by the trainer.

## Experiments and runs

A root holds one evolving mix, one loop, and a sequence of runs. Runs in one
root never overlap: two trainers would emit signals about two policies into
one loop and hot-reload each other's folds. Two concurrent trainings are two
roots; fork by copying `data/` and `evolution/tasks/` and recording
`forked_from`. A resume is a new run directory with `resumed_from` set; the
checkpoint directory is the one the previous run linked.
