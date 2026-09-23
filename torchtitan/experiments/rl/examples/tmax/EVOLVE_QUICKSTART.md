# Training with the evolve loop

Two processes over one experiment root. The trainer samples tasks from
`data/mix/live.jsonl`, and for every group it scores it may write one signal
file under `runs/<run>/signals/`. The loop reads those signals, rewrites the
task, and publishes a new mix version; the trainer picks that version up the
next time it samples a task, minutes later at most. Neither process talks to
the other: the files under the root are the whole interface, so either side can
be stopped and restarted on its own.
What happens to one task between signal and fold is in
[`EVOLVE_LOOP.md`](EVOLVE_LOOP.md); the on-disk contract is
[`LAYOUT.md`](LAYOUT.md).

## 1. Data: the published TMax release

Seed a new root from the published release. It carries the mix rows and the
source package of every task, and the loop needs both: a task's first rewrite
starts from `data/sources/<corpus>/tasks/<task>/`, and a mix copied in without
its sources stops every rewrite at `NoSeed`.

```bash
python torchtitan/experiments/rl/examples/tmax/evolution/data_release.py fetch \
    --repo andylizf/TerminalWorld-Seeds-Clean \
    --revision ef0a100cd93cd2ecd316adb30eb3819ec3f37201 \
    --release-sha256 3d0f1eedc6e68c15e67b67cd8a59c2d763de0c8efa19d4f002083ff7cc17c328 \
    --out ./release
```

`release/mix.jsonl` holds 1,748 rows: 431 TMax tasks from the `longlongcheck`
split (the repaired reaudit tasks published 2026-09-14) and 1,317 SWE-Rebench
tasks. For a TMax-only run, keep the rows whose `metadata.corpus` is `tmax`
and link only that source:

```bash
python - <<'EOF'
import json
rows = [l for l in open("release/mix.jsonl") if json.loads(l)["metadata"]["corpus"] == "tmax"]
open("release/mix_tmax.jsonl", "w").writelines(rows)
print(len(rows))
EOF
python torchtitan/experiments/rl/examples/tmax/new_root.py \
    --base <root> --mix release/mix_tmax.jsonl --sources release/sources/tmax \
    --bin <dir holding codex and jq> --purpose "<one line>"
```

For both corpora, `new_root.py --base <root> --data-release ./release` does the
same in one step. Keep `./release` where it is: the root links its source
directories rather than copying them. The TMax images in this release have not
been built before, so probe a few packages with `daytona_revalidate.py` before
committing a run to them ([`README_SEED_DATA.md`](README_SEED_DATA.md),
section 4).

TMax longlongcheck packages carry `solution/gpt6_actions.json` instead of
`solution/solve.sh`. Evolution reads and edits that action list, and both
`./sandbox oracle` and `daytona_revalidate.py` replay it through Terminus in one
persistent terminal. Replay preserves recorded turn offsets, keystrokes, waits
and the two final `completion_marker` entries. No solution symlink or generated
shell script is needed. The size check counts non-comment lines in the
keystrokes, excluding JSON metadata.

## 2. The trainer

On della, training is the `rltrain.service` unit over `runbook/launch_9b.sh`
and `runbook/rltrain.env`; [`evolution/RUNBOOK.md`](evolution/RUNBOOK.md) is
the operating manual. On
the hip box, `evolution/hip/titan_tmax_evolve_host.sh` launches the same recipe
from the host venv; its `BASE=` line names the root. Whichever launcher, four
trainer-side settings decide whether the run takes part in evolution:

| setting | value in our runs | what it does |
|---|---|---|
| `SYNTH_EFFORT` | `medium` (default) | Reasoning effort passed to Codex through `model_reasoning_effort` and to Claude Code through `--effort`, and recorded in `session.json`. Use a level supported by the selected CLI and model. |
| `SWE_EVOLUTION_SIGNALS` | `1` (default) | `0` writes no signals at all: the loop has nothing to do, whether or not it runs. |
| `SWE_EVOLUTION_HARDER_RATIO` | `0.9` (default `1.0`) | A group solved at or above this fraction asks for `harder`; 0.9 on a group of 16 means 15/16 already asks. A group with no solve at all asks for `easier`, whatever this is set to. |
| `SWE_DATA_HOT_RELOAD` | `1` (default `0`) | Pick up a newly published mix version mid-run. Without it the trainer keeps the version it booted with and evolution never reaches training. |
| `SWE_DROP_ZERO_STD` | `0` (default `1`) | Keep zero-variance groups in the batch. Dropping them sheds exactly the tasks the loop is rewriting. |

The trainer records which mix version it serves in
`runs/<run>/trainer/mix_versions.jsonl`: a `boot` line, then one `hot_reload`
line per version it picked up.

## 3. The loop

On della: `bash evolution/restart_evolve.sh [workers] [interval]` with
`TRL_PROFILE`, `TRL_BASE` and the three `TT_DAYTONA_*` fleet defaults exported,
as the runbook shows. On hip: `bash evolution/hip/evolve_hip.sh [workers]
[interval]`, which sets the same environment from fixed paths. Both run the loop
as a systemd user unit named `evolve-<root basename>`, log to
`<root>/evolution/loop.log`, and snapshot the unit's environment to
`<root>/evolution/loop.env`, which is where to confirm every switch below after
launch. `systemctl --user stop evolve-<root basename>` stops the loop; training
continues and signals accumulate until the loop is started again.

The loop needs `codex` and `jq` under `<root>/bin/`, the Daytona key the
trainer uses, since revalidation opens sandboxes at the row's own size, and a
model behind `SYNTH_API_BASE` / `SYNTH_MODEL`. Our runs use Claude Opus 5
through the LiteLLM proxy in `evolution/claude_proxy/`: `proxy.sh start`, then
`claude_env.sh` for the environment. The `TT_DAYTONA_CPU` / `MEM_GB` /
`DISK_GB` values must equal the trainer's, since a row declaring no size of its
own is revalidated at that size.

| setting | value in our runs | what it does |
|---|---|---|
| `SWE_EVOLVE_SIMPLIFY` | `0` (default) | The easier arm. At `0`, an all-fail signal is written to the ledger as `deferred` and the task stays as it is. At `1`, the loop simplifies it. The bound on how much the reference solution may grow does not apply to this direction, so a simplification is rejected only when the reference solution fails the verifier, when an untouched workspace passes it, or when the edit touches files its operator may not; the "Simplification" section of [`EVOLVE_LOOP.md`](EVOLVE_LOOP.md) has the detail. |
| `SWE_SIMPLIFY_HINT` | `vague` | How much guidance a simplification may write into the instruction. `specific` writes where-to-look hints in, and the holdout experiment described in that same section showed the policy learning to follow hints rather than to solve. |
| `SWE_RETUNE_AGENT` | `codex` | The rewrite runs as a Codex CLI session over the package and the group's rollout records. `claude` is the same session driven by Claude Code instead, which authenticates with a Claude subscription rather than an API key; `EVOLVE_CLAUDE_MODEL` (default `opus`) picks the model, since `SYNTH_MODEL` names an OpenAI one. `chat` (the code default) makes a single model call instead. |
| `EVOLVE_HARDER_OPERATORS` | `0` | Hardening follows the student's traces without an operator menu. `1` restores the fixed operator shortlist. |
| `EVOLVE_REPAIR_ROUNDS` | `3` (default) | When the reference solution and the verifier disagree, or the caller's revalidation fails, the author's session repairs first and the verifier's session second; that pair is one iteration, and this is how many are tried before the rewrite is discarded. |
| `EVOLVE_REWRITE_BUDGET_SEC` | unset (default) | No repair iteration starts once the rewrite has been open longer than one training epoch, which the loop measures from the run: rows in the mix over groups per step, times the median step interval (`rewrite budget …` in `loop.log` shows the number). Set this to a number of seconds to use a fixed budget instead. |
| `--workers` | `16` (default `8`) | Concurrent rewrites in a round. The loop is signal-starved most of the time; workers only drain a burst faster. |
| `--interval` | `120` | Seconds between rounds. |

A round handles the signals present when it starts, one per task, and ends
when all of them are done; signals arriving meanwhile wait for the next round.
Since a rewrite runs one to three hours, a round that started on a single
signal keeps everything after it waiting that long. Expect this at the start
of a run, when the first group finishes alone.

`evolve_ondella.py --once --dry --limit 1` with the same environment, or
`evolve_hip.sh dry` on hip, runs one round that handles and publishes nothing,
and is the check that the paths, the key and the model all resolve before the
unit is started.

## 4. Readouts while it runs

- `<root>/evolution/loop.log`: one line per verdict, `-> accepted`, `-> kept`
  or `-> failed` with the reason, and a `round:` summary per round.
- `<root>/evolution/status.json`: the ledger's counts, rebuilt at the end of
  every round.
- `<root>/data/mix/history/`: one file per published version, with its
  manifest.
- `runs/<run>/trainer/mix_versions.jsonl`: whether the trainer has picked the
  version up.
- W&B: per-step outcomes and cumulative counts appear directly in the training
  run. See [the training runbook](runbook/RUNBOOK.md#evolution-charts-in-the-training-wb-run)
  for the observed-step and origin-step outcome curves,
  the issued/consumed/rewrite-outcomes overlay in `evolution/run/signal_flow`,
  `evolution/run/*_total`, and the supplementary
  observer link. The original `evolution/step/*` scalar keys remain available.
  The top-level `evolution/*` counters retain their experiment-wide, round-level
  meaning.
- On hip, `python3 evolution/hip/run_health.py <run dir>` prints one line with
  steps done, per-sequence generation speed, the timeout rate and agent seconds
  per turn: the numbers to look at before touching any concurrency setting.
