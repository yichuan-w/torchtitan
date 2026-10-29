# The evolveloop as a running system — deployment readout

Ref: `origin/andy/proposer-drafts-checker`, head `ac7604b7`. Third readout,
companion to `ANDY_BRANCH_READOUT.md` (architecture, visibility, gates, pins)
and `ANDY_BRANCH_FOLLOWUP.md` (leak detectors, solutionless rows, harden
trigger, entry seam, pin safety). Those two covered the code path; this one
covers the **deployment layer** — how the loop is configured, started, fed and
measured on the data host.

Paths are relative to `torchtitan/experiments/rl/examples/tmax/` unless written
out. Every claim carries a `<path>:<line>` on the ref. "not found" means I
looked and it is not there.

---

## 1. CONFIG

### `evolution/della/evolveloop_env.sh` — the whole file is 57 lines

It is sourced (not executed) and "exports everything `evolve_ondella.py` reads
and leaves `PY`, `TT`, `EVO`, `UNIT` and `LOG` set for the caller"
(`evolution/della/evolveloop_env.sh:4-5`). It is the *only* place the loop's
environment is assembled; `restart_evolve.sh` and `offline_drive.sh` both source
it (`evolution/restart_evolve.sh:25`, `evolution/offline_drive.sh:40`), as do
`replay_signals.sh:21` and `ab_verifier_author.sh` indirectly.

**Required inputs (the script refuses to load without them).** All four are
`${var:?message}` guards:

| variable | line | what it switches | read by |
|---|---|---|---|
| `TRL_PROFILE` | `:14` — "name the profile whose checkout this is; see runbook/profiles/" | which checkout+root this loop is. Resolves `$HERE/../../runbook/profiles/$TRL_PROFILE.env` (`:27`), from which `TRL_TT` (`:29`) and the default `TRL_BASE` (`:31`) come | the whole file; `runbook/RUNBOOK.md:909-910` |
| `TT_DAYTONA_CPU` | `:15` — "the per-sandbox vCPU default the trainer runs with" | the fleet default a row with no `daytona_*` is verified at | `evolution/daytona_revalidate.py:277` ("A key left None falls to the harness default (TT_DAYTONA_*)") |
| `TT_DAYTONA_MEM_GB` | `:16` | same, GiB | same |
| `TT_DAYTONA_DISK_GB` | `:17` | same, GiB | same |

The reason they are mandatory is stated at `:10-12`: "The three `TT_DAYTONA_*`
are the trainer's fleet defaults and have to match its launch env: a row
declaring no `daytona_*` of its own is verified at this size." The live values
are `1 / 2 / 2` (`evolution/RUNBOOK.md:231`, matching
`runbook/rltrain.env:141-144`).

**Paths and interpreter.**

| variable | value | line |
|---|---|---|
| `HERE` | the script's own directory — used **only** to locate the profile (`:18-25`: "Locating the profile is the only thing the script's position is used for") | `:26` |
| `PROFILE` | `$HERE/../../runbook/profiles/$TRL_PROFILE.env`; missing ⇒ exit 2 | `:27-28` |
| `TT` / `TRL_TT` | `sed -n 's/^TRL_TT=//p' "$PROFILE"`; not a directory ⇒ exit 2 | `:29-30`, exported `:47` |
| `TRL_BASE` | ambient wins, else the profile's line; not a directory ⇒ exit 2 | `:31-32` |
| `EVO` | `$TT/torchtitan/experiments/rl/examples/tmax/evolution` | `:33` |
| `TRL_VENV` | `${TRL_VENV:-/scratch/gpfs/TRIDAO/al9080/titan-rl}` — "The training venv: the loop's interpreter, and the one `daytona_revalidate` and `./sandbox` run in" | `:35-37` |
| `PY` | `$TRL_VENV/bin/python` | `:38` |
| `UNIT` | `evolve-$(basename "$TRL_BASE")` | `:39` |
| `LOG` | `$TRL_BASE/evolution/loop.log` | `:40` |
| `PYTHONPATH` | `=$TT` | `:47` |

A mismatch between the profile's checkout and the script's location is warned
about, not fatal: `case "$HERE" in "$TT"/*) ;; *) echo "[env] running $TT
(profile $TRL_PROFILE); this script was invoked from $HERE"` (`:34`).

**Credentials.**

| source | line | what breaks without it |
|---|---|---|
| `. ~/.config/daytona/env` | `:45` | "Without the daytona env, structural revalidation fails `no_docker`" (`:42`); restated `evolution/RUNBOOK.md:243-244`, `runbook/RUNBOOK.md:901` |
| `SYNTH_ENV_FILE=${SYNTH_ENV_FILE:-/scratch/gpfs/TRIDAO/al9080/terminal-rl/.synth_env}` | `:46` | "without `SYNTH_ENV_FILE` the loop dies at startup with `no OPENAI_API_KEY`" (`:43`). Read by `evolution/synth_client.py:106-108` (`ENV_FILE`) and `_api_key()` at `:111-115` |

**Agent arm, directions, hardening mode.**

| variable | value set | line | what it switches | read by |
|---|---|---|---|---|
| `SWE_RETUNE_AGENT` | `codex` (hard-set, not defaulted) | `:51` | agentic retune with full rollout records as files; comment at `:48-49`: "Agentic retune with the full rollout records as files, no chat fallback" | `evolution/feedback_loop.py:715` (`os.environ.get("SWE_RETUNE_AGENT", "chat")`), dispatch `:737-738` |
| `SWE_SIMPLIFY_HINT` | `vague` (hard-set) | `:51` | how much guidance a simplify may write into the instruction; comment `:49-50`: "a vague hint level, since specific where-to-look hints teach hint-following" | `evolution/feedback_loop.py:772` |
| `SWE_EVOLVE_SIMPLIFY` | `${SWE_EVOLVE_SIMPLIFY:-0}` | `:52` | the easier (0/k) arm. Off ⇒ 0/k signals get a `deferred` ledger line | `evolution/evolve_ondella.py:89-93` (`SIMPLIFY_ENABLED`) |
| `EVOLVE_HARDER_OPERATORS` | `${EVOLVE_HARDER_OPERATORS:-0}` | `:55` | 0 = student-trace-guided hardening with no operator menu; "Set to 1 before launch to restore the fixed shortlist" (`:53-54`) | `evolution/synth_operators.py:25-30` (`harder_uses_operators()`) |

Last line: `mkdir -p "$TRL_BASE/evolution"` (`:57`).

**What `evolveloop_env.sh` does NOT set, and where those values come from.**
It sets no model, no effort, no timeout, no worker count, no harder ratio.

| knob | default | code line | live value |
|---|---|---|---|
| `SYNTH_MODEL` | `gpt-5.6` | `evolution/evolve_codex.py:90` (`CODEX_MODEL`), `evolution/synth_client.py:102`, `evolution/codex_session.py:215` | `gpt-5.6` — "an alias that resolves to the most expensive of three tiers" (`runbook/RUNBOOK.md:903`) |
| `SYNTH_EFFORT` | `high` | `evolution/synth_client.py:105`; `evolve_codex.py:94` reuses it as `CODEX_EFFORT` ("Same knob as the chat calls: high unless SYNTH_EFFORT says otherwise", `:91-93`) | `high` |
| `SYNTH_API_BASE` | `https://us.api.openai.com/v1` | `evolution/synth_client.py:99-101`, `evolve_codex.py:95` | regional host is mandatory: "api.openai.com answers 401 with 'incorrect regional hostname'" (`synth_client.py:99-100`) |
| `SWE_VERIFIER_AUTHOR` | `blind` | `evolution/evolve_codex.py:943` | blind |
| `EVOLVE_CODEX_DRIVER` | `exec` | `evolution/evolve_codex.py:107` | exec (`sdk` needs `TRL_SDK_PY`, `:108-110`) |
| `EVOLVE_AGENT_TIMEOUT` | `2400` | `evolution/evolve_codex.py:952` | 2400 on OpenAI, `7200` under the Claude proxy (`evolution/claude_proxy/claude_env.sh:23`) |
| `EVOLVE_REPAIR_ROUNDS` | `3` | `evolution/evolve_codex.py:959` | 3 |
| `CODEX_RETUNE_MAX_CALLS` | `25` | `evolution/evolve_codex.py:99` | 25 |
| `CODEX_RETUNE_TIMEOUT` | `600` | `evolution/evolve_codex.py:112` | 600 |
| `EVOLVE_REWRITE_BUDGET_SEC` | unset ⇒ computed | `evolution/evolve_codex.py:990-1017` | "one epoch: `<rows>` rows / `<groups>` groups per step = … steps x … min per step" (`:1009-1012`); "about 14 h on hip's MI350Xs, a few hours on B300s" (`:964`) |
| `SWE_EVOLUTION_HARDER_RATIO` | `1.0` | `config_registry.py:183-185`, field default `rollouter.py:921` | **`0.9`** in `runbook/rltrain.env:163` — "15/16 for a fully scored group of 16. Mixed groups still train" (`:160-161`) |
| `SWE_EVOLUTION_SIGNALS` | `1` | `runbook/RUNBOOK.md:711`, `LAYOUT.md:96` | 1 |
| `SWE_ROLLOUT_RECORDS` | `1` | `LAYOUT.md:95` | 1 |
| `SWE_DROP_ZERO_STD` | code default 1 | `runbook/rltrain.env:73` sets **0** — "0 = KEEP zero-variance groups… the evolution loop re-tunes those prompts instead of discarding them" (`:65-67`) | 0 |
| `SWE_DATA_HOT_RELOAD` | — | `runbook/rltrain.env:49` sets **1** — "Required if the evolution loop is running" (`:47-48`) | 1 |
| `EVOLVE_SOCK_DIR` | `/tmp` | `evolution/agent_sandbox.py:258` | `/dev/shm/$USER-evolve` offline (`evolution/RUNBOOK.md:443`) |

**Which model writes what.** All three sessions run **one model**: there is no
per-role model split anywhere on this ref. `CODEX_MODEL` (`evolve_codex.py:90`)
is passed to every session kind — author, blind verifier, independent probe
author, repair — and `LAYOUT.md:225` shows the recorded session metadata:
`{"kind": "agent", "model": "gpt-5.6", "reasoning_effort": "high", "driver":
"exec"}`, with `kind` being "`agent` (the rewrite itself), `repair`, `verifier`
(blind verifier author), `oracle`" (`LAYOUT.md:230-231`). So:

* **tasks (author / proposer)** — `SYNTH_MODEL`, default `gpt-5.6`, effort high.
* **verifiers (blind verifier author)** — the same model, same effort, a
  *different session* with a different `AGENTS.md`
  (`evolution/evolve_codex.py:1462`, spec `:944`).
* **probes (independent probe author)** — again the same model, a third session
  (`evolution/evolve_codex.py:1592`, prompt `agents/independent_verifier_probes.md`).

Two alternative arms are documented and neither changes the split:
`EVOLVE_CODEX_AUTH_FILE` + `SYNTH_MODEL` for a ChatGPT login
(`evolution/RUNBOOK.md:258-263`, code `evolve_codex.py:309-314`), and the
LiteLLM proxy that fronts Claude with the Responses API
(`evolution/claude_proxy/claude_env.sh:12-14`: `SYNTH_MODEL=claude-opus-5`,
`SYNTH_API_BASE=http://127.0.0.1:4000/v1`, `OPENAI_API_KEY` = the proxy's master
key). Effort is still one global knob — the RUNBOOK names lowering it per stage
as a *future* lever, not a current setting: "Effort `medium` on the verifier and
probe stages is the first lever" (`evolution/RUNBOOK.md:503-504`).

**Bottom line.** `evolveloop_env.sh` is small and opinionated: four mandatory
inputs, two credentials sourced from outside the repo, and exactly four
behavioural switches (`codex` arm, `vague` hints, simplify off, operator menu
off). Everything about the model — which one, at what effort, with what timeout
— lives in `evolve_codex.py`/`synth_client.py` defaults, not in the deployment
file, and one model at one effort writes tasks, verifiers and probes alike.

---

## 2. LIFECYCLE

### Start / restart

One command: `TRL_PROFILE=andy TRL_BASE=… TT_DAYTONA_CPU=1 TT_DAYTONA_MEM_GB=2
TT_DAYTONA_DISK_GB=2 bash $EVO/restart_evolve.sh [workers] [interval]`, defaults
`16, 120` (`evolution/RUNBOOK.md:229-232`; `evolution/restart_evolve.sh:22-23`).
The steps, in order:

1. Source `della/evolveloop_env.sh` (`restart_evolve.sh:25`); refuse if `$PY` is
   not a virtualenv python (`:29-30`).
2. Find a live loop through `$TRL_BASE/evolution/loop.lock`, which holds
   `host=` and `pid=` (`:34-36`). Same host + live pid + `/proc/<pid>/cmdline`
   ending in `evolve_ondella.py` ⇒ that is the old loop (`:37-39`). Another
   host with a heartbeat under 90 s ⇒ **refuse**: "a loop on $host holds $LOCK
   (heartbeat under 90 s); stop it there first" (`:40-43`).
3. Kill the old loop's **whole process group** (`kill -TERM -- "-$PGID"`,
   `:51`), 60 s grace then SIGKILL (`:52-53`), with a guard refusing to signal
   pgid 0 or 1: "A signal to group 0 or -1 reaches every process of the user;
   never that" (`:48-49`). Rationale at `:12-14`: "Codex sessions and probes must
   not outlive the loop being replaced".
4. `finalize_interrupted_traces.py --stopped-loop-pid "$OLD"` marks what was
   `running` as interrupted (`:54-55`).
5. Snapshot the environment to `$TRL_BASE/evolution/loop.env` in systemd
   `EnvironmentFile=` form — `KEY="value"`, backslash and quote escaped — keeping
   only prefixes `PATH HOME LANG LC_ SYNTH_ TRL_ PYTHONPATH SWE_ TMAX_
   TT_DAYTONA_ DAYTONA_ OPENAI_ CODEX_ EVOLVE_` (`:63-77`), under `umask 077`.
6. `systemd-run --user --unit="$UNIT" --collect --working-directory="$TRL_BASE"
   -p EnvironmentFile="$ENVFILE" bash -c "exec $PY $EVO/evolve_ondella.py
   --interval $INTERVAL --workers $WORKERS >> $LOG 2>&1"` (`:80-82`).

Why a user unit and not `nohup`: "nohup'd processes are SIGKILLed with the ssh
session on della-tridao" (`restart_evolve.sh:16-17`; repeated
`evolution/RUNBOOK.md:210-211`). `--collect` removes the unit on exit, "so read
`loop.log` for health rather than `systemctl status`"
(`restart_evolve.sh:18-19`).

Stopping: "**`restart_evolve.sh`, or `systemctl --user stop evolve-<root>`, not
Ctrl-C.** Ctrl-C mid-round gets absorbed and you end up with two instances"
(`evolution/RUNBOOK.md:409-410`). The singleton is `evolution/loop.lock` —
"flock on its own node, a 30 s heartbeat on the file's mtime for other nodes"
(`:411-412`).

### Loop flags

`--once` one round and exit; `--only <task>`; `--limit N`; `--dry`;
`--signal <id>` (implies dry); `--workers`; `--interval`
(`evolution/RUNBOOK.md:311-315`; `evolution/evolve_ondella.py:47-52`).
The worker count is explicitly not a throughput knob: "the loop is
signal-starved (89% of rounds carry ≤8 signals) and it only drains rare bursts
faster" (`evolution/RUNBOOK.md:255-256`).

### Offline drive (a chosen subset of signals, not the trainer's stream)

Four steps, `evolution/RUNBOOK.md:430-447`: `offline_select.py` picks tasks →
`new_root.py --base … --fork-from/--mix` makes a root of its own →
`offline_drive.sh` as a user unit runs rounds until done → `offline_publish.py`
writes `live.jsonl` out as a named seed with a manifest naming every replaced
row's rewrite.

`evolution/offline_drive.sh` is the driver: it waits out any live round over
the root by polling `loop.lock`'s pid (`:47-49`), stages with
`offline_stage.py --run … --selected … --skip-accepted --only-failed
--max-attempts --attempts-since` under a fresh run-dir name
`$(basename $SOURCE_RUN).offline$k-<stamp>` (`:50-55`), then runs
`evolve_ondella.py --once --workers $WORKERS` (`:64`). It stops when
`staged 0 signals` (`:57-61`) or after `MAX_ROUNDS` (default 6, `:31`).
Defaults: `WORKERS=64 MAX_ROUNDS=6 MAX_ATTEMPTS=2` (`:31`).
It must run `$PY` and not the login node's python: "the login node's python3 is
3.9 and cannot parse offline_stage.py; its traceback went to the journal and the
round read 'staged 0' (2026-09-14)" (`:51-53`).
`CLAUDE_PROXY=1` additionally sources `claude_proxy/claude_env.sh` (`:41-44`).
Why restaging works at all: "A signal the ledger closed is never handled again,
so every retry round stages the failed tasks under a fresh run-dir name"
(`evolution/RUNBOOK.md:449-452`).

### Replay (dry, against signals the loop already closed)

`evolution/della/replay_signals.sh [n] [direction]`, defaults `3 harder`
(`:17-19`). It takes the ids from "the ledger's newest `handled` lines of that
direction" (`:13`, implemented `:30-43`) and runs `evolve_ondella.py --signal
<sid> --workers 1` (`:28`). Each replay "implies `--dry`: the rewrite directory
is written in full (package, sessions, rewrite.json marked dry) and no ledger
line, lineage line or mix version is" (`:4-6`). Before replaying it prints the
checkout's commit and dirty-file count (`:24`) — "and that count has to be zero"
(`evolution/RUNBOOK.md:303-304`). Cost note: "Each replay costs one Codex
session and a few sandboxes for a harder signal, so keep n small" (`:14-15`).

### The A/B harness

`evolution/della/ab_verifier_author.sh <dev-root> <from-root> <signal-id>...`
runs "The same signals through the evolve loop twice, `SWE_VERIFIER_AUTHOR=same`
then `=blind`, on a DEV root, so the two modes are compared on identical tasks
from an identical starting state" (`:2-4`). It refuses a root whose name does
not contain `-dev` (`:27`) and one with no `experiment.json` (`:28`). Mechanics:
stage the signal file and hardlink every rollout record it names (`:44-56`),
snapshot the "before" state = ledger minus the replayed ids + newest mix version
+ the tasks' directories (`:60-70`), then for each mode `restore_state` (`:73-88`)
and run a round (`:90-98`), copying the ledger delta and the task dirs into
`$DEV/logs/ab_verifier_author--<stamp>/<mode>/` (`:102-106`).
**One gap:** it invokes `"$HERE/evolve_dev_round.sh"` (`:98`) and that script
**does not exist on this ref** — `git ls-tree -r` over the branch finds no
`evolve_dev_round.sh` anywhere. As shipped, `ab_verifier_author.sh` cannot run.

### What the loop consumes and produces

Consumes (`LAYOUT.md:9-12`, rule 1): `runs/*/signals/*.json` and the
`runs/*/rollouts/` records they reference — nothing else crosses. Produces, all
under `$TRL_BASE`:

| artefact | contents | spec |
|---|---|---|
| `evolution/loop.log`, `loop.lock`, `loop.env` | log / singleton / unit env | `LAYOUT.md:65` |
| `evolution/ledger.jsonl` | one line per signal seen: `handled` / `deferred` / `junk` / `superseded` | `LAYOUT.md:160-178` |
| `evolution/status.json` | rebuilt every round from ledger+lineage; the trainer reads it | `LAYOUT.md:251-262` |
| `evolution/tasks/<task>/r0…rN/` | accepted revisions, package only; r0 copied from `data/sources/<corpus>/tasks/<task>` on first signal | `LAYOUT.md:70-72` |
| `…/rewrites/<stamp>--<job>/` | `rewrite.json`, `pretest.json`, `package/` (+ `traces/`), `sessions/<stamp>--<kind>/` | `LAYOUT.md:73-80` |
| `…/lineage.jsonl` | `rewrite` and `fold` events; "`fold` is the only record of a revision entering the mix" | `LAYOUT.md:240-249` |
| `data/mix/history/v<N>--<stamp>.jsonl` + `.manifest.json` | every version ever served | `LAYOUT.md:39-42` |
| `data/mix/live.jsonl` | a **hardlink** to the newest history file | `LAYOUT.md:42`, `:274-277` |
| the audit git | `evolution/.git` metadata, work tree = the root; ledger, status, lineage, rewrite.json, mix manifests, "named one by one and nothing else" | `evolution/RUNBOOK.md:390-394` |

### How a folded revision reaches the trainer

1. `fold` renames `package/` → `r<N+1>/` after stripping `AGENTS.md`, `sandbox`,
   `run/`, `traces/` (`LAYOUT.md:201-203`), rebuilds the row with `pack.to_row`
   carrying the replaced row's hook and size, and publishes the next version —
   **replace-only**: "a label not already in the file is skipped, because a new
   row lands at the end and would shift the holdout tail"
   (`runbook/RUNBOOK.md:871-875`).
2. Publication goes through `layout.MixDir.publish` (`layout.py:289`) which
   "writes the next `history/v<N>--<stamp>.jsonl` with its manifest and relinks
   `live.jsonl` in one rename" (`evolution/RUNBOOK.md:562-565`).
3. The trainer hot-reloads on mtime change with `SWE_DATA_HOT_RELOAD=1`,
   "rate-limited to one stat per 20s… Same-id rows are swapped in place"
   (`runbook/RUNBOOK.md:879-881`); watch for `TMaxDataset: hot reload` in
   `stdout.log` and one line per reload in
   `runs/<run>/trainer/mix_versions.jsonl` (`evolution/RUNBOOK.md:134-137`).
4. Folding happens **per acceptance**, not per round: "The loop folds each
   accepted rewrite the moment it is accepted, so stopping the unit mid-round
   loses only the rewrites in flight" (`evolution/RUNBOOK.md:458-460`;
   `evolve_ondella.py:33-38`).

### Signal reuse

"Repeated signals reuse the last completed rewrite decision when task ID,
revision, direction, solved count, and total count match. Run IDs, timestamps,
and rollout paths do not make feedback new… Failed or interrupted executions
remain retryable, and explicit `--signal` replay bypasses reuse"
(`evolution/RUNBOOK.md:220-225`).

### Live-code hazard

Most of the loop is frozen into the process at import, but five things are read
from the checkout at the moment they are used, so "a `git pull` in that
directory changes the behaviour of a run already in flight, with nothing in its
log to say so" (`evolution/RUNBOOK.md:332-336`): `agents/task_evolution.md`
(copied per session), `agent_sandbox.sh` (copied in as `./sandbox` per session),
`agent_sandbox.py` (executed per `./sandbox` call), `task_size.py` +
`verifier_literals.py` (imported by it), `daytona_revalidate.py` (a subprocess
per probe) — table at `:338-344`.

**Bottom line.** One systemd user unit per experiment root, whose entire
environment is a snapshot file written by `restart_evolve.sh`; a `loop.lock`
singleton; three drive modes (continuous, offline-subset, dry replay). Nothing
but the loop writes `evolution/` or `data/mix/`, and a folded row reaches the
trainer through one atomic relink plus a 20-second mtime poll. The shipped A/B
script is broken: the helper it calls is absent from the ref.

---

## 3. DATA

### The mix the loop and the trainer run on

One file: `$TRL_BASE/data/mix/live.jsonl`, a hardlink to the newest
`data/mix/history/v<N>--<stamp>.jsonl` (`LAYOUT.md:39-42`). The trainer reads it
through `SWE_PROMPT_DATA`, which is **per profile**, not in `rltrain.env`
(`runbook/rltrain.env:43-44`). Only the loop writes `data/mix/`
(`evolution/RUNBOOK.md:562`).

### Builders

| builder | what it assembles | line |
|---|---|---|
| `evolution/build_mix_v2.py` | "the take8 restart mix: clean TerminalWorld seeds + Tmax-Tasks-Clean" — the TW `train_ready_ids.txt` half plus the Tmax `reaudit` split | `evolution/build_mix_v2.py:2` |
| `evolution/build_mix_rebench_tmax.py` | "Fzz1/SWE-Rebench-Tasks-Clean + latest Fzz1/Tmax-Tasks-Clean reaudit" | `:2` |
| `evolution/data_release.py` (+ `release_pipeline.py`) | the immutable release path; `data_rebench_tmax.json` pins the two sources | `evolution/DATA_RELEASES.md:17-26` |
| `prepare_tmax_reaudit_data.py` | standalone JSONL from the reaudit split | `:7` |
| `prepare_tmax_data.py` | JSONL from the raw `allenai/tmax-15k-open-instruct` corpus | `:7` |
| `prepare_rts_data.py` | the shared adapter for RTS / TerminalWorld / SWE-Smith | `README_SEED_DATA.md:45-54` |

Both mix builders run `pack.to_row` over the same package directory the loop
copies `r0` from, deliberately: "packed through `pack.to_row` like the TW seeds,
so a seed row and a folded row come off one adapter"
(`evolution/build_mix_v2.py:18-19`), and the TMax packages live at
`data/sources/tmax-extract/tasks` — "the same directory the loop copies r0 from"
(`:17-18`, restated `:244-245`).

### Composition by source, with every recorded count

**TerminalWorld half.** `evolution/build_mix_v2.py:8` says
"`metadata/train_ready_ids.txt` (**669**)"; `README_SEED_DATA.md:124` says
"**`train_ready_ids.txt` (663) is the canonical training subset**", and adds the
reason the two disagree: "Count it rather than quoting one: it moves whenever a
task is repaired back in or a decayed one is dropped" (`:126-127`).
Upstream of that: of 1,530 TerminalWorld tasks, 1,353 "build, run and get
graded" (`evolution/README.md:155-156`); of the 1,353, `reward_verdict`
**pass 861 / fail 446 / unknown 46** (`README_SEED_DATA.md:119`) —
"**861 have a reference solution that earns a passing grade**"
(`evolution/README.md:158`). `verdict_flipped` True **54**; `reference_partial`
True **55** (`README_SEED_DATA.md:120-121`).
Difficulty of the 861 at k=5 on GPT-5.6-sol, 821 verdicts:
pass@5 = 1.0 → **591 (72.0%)**, 0<p<1 → **167 (20.3%)**, 0.0 → **63 (7.7%)**
(`evolution/README.md:169-176`).

**TMax half.** `evolution/DATA_RELEASES.md:25-26`: "The supplied configuration
includes all **1,317 Rebench** tasks and **452 TMax** tasks at its pinned
revisions, without oversampling." The pinned TMax revision in the release config
is `Fzz1/Tmax-Tasks-Clean` @ `d6781bfe…`, split `splits/reaudit.parquet`, archive
`data/tasks-reaudit-00000.tar` (`evolution/data_rebench_tmax.json:13-21`);
`build_mix_rebench_tmax.py:39` pins a different, later revision
`TMAX_REV = "a84676aa19ae12713492245ffac98559e572ea86"`.
The reaudit split's row count is not hard-coded: the builder counts what the
parquet carries (`build_mix_v2.py:184-199`) and the manifest records
`tw_rows`, `tmax_rows`, `tmax_hooked`, `total`
(`build_mix_v2.py:327-332`).

**TMax reaudit split vs the raw TMax corpus.** Two different artefacts:
* raw `allenai/tmax-15k-open-instruct` — per-task tree is "`instruction.md`,
  `setup.sh` (ignored — already baked into the image), and `tests/test.sh`"
  (`prepare_tmax_data.py:16-17`); `env_config.image` is "the PUBLIC dockerhub
  image the task runs in (setup.sh baked in)" (`:14`). **No Dockerfile, no
  solution.**
* `Fzz1/Tmax-Tasks-Clean` `reaudit` split — per-task tree is
  `instruction.md`, `environment/Dockerfile` ("a bare single FROM `<ref>` for
  every task"), `tests/test.sh`, `tests/reference_pins.sha256` "where present",
  `solution/solve.sh`, `setup.sh` (`prepare_tmax_reaudit_data.py:16-21`).
  **Dockerfile and solution both present.**

**Composite mix sizes actually recorded.** `LAYOUT.md:267` uses `"rows": 663` as
the manifest example. The only real observed number is in the unstartable-ids
file: "purged from mix_live (**1961->1953**)"
(`evolution/della/daytona_unstartable.ids:8-15`). An older configuration is
recorded in `handoff/2026-08-24-terminalworld-online-evolution.md:34`:
"TerminalWorld-Seeds-Clean, **859 tasks**, pure (no mix)". The holdout is
always the last 64 rows (`evolution/RUNBOOK.md:531-533`,
`build_mix_v2.py:274`, `data.py:200-207, 245-258`).

### Are TMax rows eligible for evolution?

**Yes — there is no corpus filter anywhere in the loop.** `materialize_r0`
(`evolution/evolve_ondella.py:416-434`) searches every directory under
`data/sources/*/tasks/<tid>` that has an `instruction.md`, with no id-prefix or
corpus test; it fails only on zero candidates (`NoSeed`, `:430-431`) or on two
(`:432-433`). `discover`/`choose` key on the ledger and the task's `rev`, not on
provenance. The row-level statement that makes TMax evolvable is the builder's:
the TMax packages under `data/sources/tmax-extract/tasks` are "the same
directory the loop copies r0 from" (`evolution/build_mix_v2.py:17-18`).

I searched for a statement excluding TMax from evolution and found none —
**not found**. The nearest thing on the ref is an older, corpus-wide remark that
the TMax half has no oracle: "`oracle_*` is the reference solution's own peak,
measured at the platform ceiling; it is empty for the TMax half, which ships no
reference solution to run" (`evolution/export_measured_csv.py:11-13`). That is
about the *measurement campaign over the raw tmax corpus*, and it is contradicted
for the reaudit split by `prepare_tmax_reaudit_data.py:20` (`solution/solve.sh`
in the expected layout) and by the test fixture that ships one
(`test_prepare_tmax_reaudit_data.py:88`). Both statements are on the same ref;
they are about different artefacts, and nothing on the ref reconciles them in
one sentence.

Two things do keep raw-corpus TMax rows out, but at *dataset build* time, not in
the loop:
* `pack.to_row` → `prepare_rts_data._to_row` refuses a package with no
  `environment/Dockerfile`: `paths = {instruction, test_sh, dockerfile}` …
  `return None, f"missing_{name}"` (`prepare_rts_data.py:486-493`), so
  `build_mix_v2.tmax_rows` records it under `tmax_missing_package`
  (`build_mix_v2.py:210-214`).
* `prepare_tmax_reaudit_data._to_row` applies the same three-file requirement
  (`:401-408`).

**Bottom line.** The live mix is one `live.jsonl` assembled from two halves by
`pack.to_row`: TerminalWorld `train_ready_ids.txt` (663 or 669 depending on the
cut, from 861 oracle-passing of 1,353 clean of 1,530) plus the
`Fzz1/Tmax-Tasks-Clean` **reaudit** split (452 TMax tasks in the pinned release
config). Nothing in the loop filters by corpus, and the reaudit split ships the
Dockerfile and solution the loop needs — so reaudit TMax rows are fully
evolvable, while raw tmax-15k packages cannot even be packed into a row.

---

## 4. WHAT A SEED PACKAGE MUST CONTAIN

### The exact file map `evolve.load` reads

`evolution/evolve.py:42-45`:

```
FILES = {"instruction": "instruction.md",
         "dockerfile": "environment/Dockerfile",
         "solve_sh": "solution/solve.sh",
         "test_state_py": "tests/test_state.py"}
```

with the verifier path resolved per package:
`VERIFIER_CANDIDATES = ("tests/test_state.py", "tests/test.sh")` (`:53`),
`vrel = next((c for c in VERIFIER_CANDIDATES if (d / c).exists()),
FILES["test_state_py"])` (`:85-86`), then

```
task = {key: (d / path).read_text(errors="replace") for key, path in fm.items()}   # :88-89
```

There is no `try`, no `exists()` test, no default. **All four are fatal if
absent.** The docstring says so for the verifier case: "A package with neither
falls back to the TW path and fails loudly on the read, which is the right
outcome for a malformed package" (`:81-83`). Dict order is `FILES`' insertion
order, so the *first* missing file is the one that raises:
`instruction.md` → `environment/Dockerfile` → `solution/solve.sh` → the verifier.

| file | absent ⇒ |
|---|---|
| `instruction.md` | fatal at `evolve.py:88`; also fatal earlier — `materialize_r0` will not even find the package (`evolve_ondella.py:425`) |
| `environment/Dockerfile` | **fatal** at `evolve.py:88` |
| `solution/solve.sh` | **fatal** at `evolve.py:88` |
| `tests/test_state.py` **or** `tests/test.sh` | fatal at `evolve.py:88` (falls back to the TW path and fails on it) |
| everything else (`task.toml`, fixtures, entrypoints, `tests/reference_pins.sha256`, `setup.sh`) | not read by `load`; carried by `_src_dir` (`:95`) and copied by `evolve_codex._lay_out` |

### Is there a converter from a raw TMax task to an evolvable package?

**On this ref, no in-tree converter builds the missing files.** The only code
that writes a Dockerfile for a prebuilt image is inside `materialize_r0`:

```
dockerfile = incoming / "environment" / "Dockerfile"
if not dockerfile.exists(): dockerfile = incoming / "Dockerfile"
if not dockerfile.exists(): raise ValueError(f"{tid}: prebuilt source has no Dockerfile")
...
dockerfile.write_text(f"FROM {image}\n")          # evolve_ondella.py:446-460
```

and it fires only for a row that already carries `prebuilt_provenance` and a
digest-pinned `image` (`:441-445`) — it **replaces** an existing audited recipe
with a bare `FROM`, it does not create one. `pack_to_dataset.py:279-301` is its
mirror on the way out. Nothing anywhere writes a `solution/solve.sh` for a seed;
the follow-up readout already established that ("There is no 'author a missing
solve.sh for a seed' step anywhere on this ref",
`ANDY_BRANCH_FOLLOWUP.md`, Q2).

What *did* produce the evolvable TMax packages is the **dataset**, not the
training tree: the `Fzz1/Tmax-Tasks-Clean` `reaudit` split ships
`environment/Dockerfile` as "a bare single FROM `<ref>` for every task", plus
`solution/solve.sh` and `tests/reference_pins.sha256`
(`prepare_tmax_reaudit_data.py:16-21`). The consumer side checks it:
"a bare single FROM resolves to the UNPREFIXED `image:<ref>`, which is what the
stamp was captured as" (`prepare_tmax_reaudit_data.py:486-488`).

### What the reaudit split ships per task, and what the prep refuses

Expected layout (`prepare_tmax_reaudit_data.py:16-21`):

```
tasks/<task_id>/instruction.md
tasks/<task_id>/environment/Dockerfile   # a bare single FROM <ref> for every task
tasks/<task_id>/tests/test.sh            # verifier: writes /logs/verifier/reward.txt
tasks/<task_id>/tests/reference_pins.sha256   # where present
tasks/<task_id>/solution/solve.sh
tasks/<task_id>/setup.sh
```

Three files are required per row (`:401-408`): `instruction.md`,
`tests/test.sh`, `environment/Dockerfile` — missing any ⇒ `missing_<name>`.
Further per-row refusals: `needs_privileged` (`:412-413`), `copy_source_missing`
/ `build_context_too_large` (`:416-421`), `empty_instruction_or_verifier`
(`:429-430`), `verifier_writes_no_reward` — "`if "reward.txt" not in test_sh and
"reward.json" not in test_sh`" (`:431-432`), and non-UTF-8 or oversize
`tests/` fixtures (`:468-473`). `solution/solve.sh` is read **only if present**
and only to count oracle commands (`:455-460`); its absence is not a refusal.
Whole-run refusals are listed under "WHAT IS REFUSED" (`:39-51`), including
"a row whose `pre_test_sh` and `pre_test_env_identity` are not both set or both
empty" and "0 of the stamped rows matching their episode identity (the
corpus-wide silent-skip guard)". "An EMPTY `pre_test_sh` is a task with no hook,
never a failure" (`:51`).

### What is missing for a *raw* TMax task, and where it breaks, step by step

Starting point: `instruction.md` + `setup.sh` (baked into a prebuilt image named
in the row) + `tests/test.sh`; no `environment/Dockerfile`, no `solution/`.

1. **Dataset build.** `pack.to_row` → `prepare_rts_data._to_row` returns
   `(None, "missing_dockerfile")` (`prepare_rts_data.py:486-493`);
   `build_mix_v2.tmax_rows` catches the `ValueError` from `pack.to_row` and lists
   the id in `tmax_missing_package` (`evolution/build_mix_v2.py:210-214`, manifest
   key `:330`). *The task never reaches the mix by this route at all.* Only a row
   built by `prepare_tmax_data.py` (which never touches a Dockerfile,
   `:261-315`, and emits `metadata.image` with no `metadata.dockerfile`) can put
   such a task in front of the trainer.
2. **Training.** Such a row trains normally — `_maybe_emit_evolution_signal` does
   not look at the package (`rollouter.py:1219`).
3. **First signal → `materialize_r0`.** The package is found by
   `instruction.md` alone (`evolve_ondella.py:425`) and copied whole
   (`:438`). Then the mix scan at `:439-445`: a raw-corpus row has no
   `prebuilt_provenance`, so the `FROM <image>` rewrite is **skipped** and r0
   still has no Dockerfile. (If the row *did* carry `prebuilt_provenance`, the
   very next lines raise `ValueError(f"{tid}: prebuilt source has no
   Dockerfile")` at `:449-450`.)
4. **`handle`** copies r0 into `package/` and hardlinks traces
   (`evolve_ondella.py:553-557`) — still fine.
5. **`feedback_loop.process_one`** — first package access is
   `task = ev.load(work)` (`evolution/feedback_loop.py:740`).
6. **`evolve.load`** raises `FileNotFoundError` on `environment/Dockerfile`
   (`evolution/evolve.py:88-89`) — and would raise again on
   `solution/solve.sh` if a Dockerfile were supplied.
7. `process_one`'s blanket handler turns that into `status="failed",
   stage="error"` (`evolution/feedback_loop.py:1029-1030`); the task goes back
   into training unchanged.
8. Had it got past `load`, the next two walls are
   `agent_sandbox.oracle` — `if not (sol_dir / "solve.sh").exists(): return
   {"ok": False, "error": "package ships no solution/solve.sh"}`
   (`evolution/agent_sandbox.py:711-712`), reached from `pack.to_row(str(pkg))`
   at `:707` which itself needs the Dockerfile — and the caller's probe,
   `{"ok": False, "stage": "no_solution", "why": "package ships no
   solution/solve.sh"}` (`evolution/daytona_revalidate.py:268-273`).

### Verifier format the loop expects

Both are accepted, everywhere, and the choice is detected per package:

| component | line | accepts |
|---|---|---|
| `evolve.load` | `evolution/evolve.py:53, 85-90` | `tests/test_state.py` **or** `tests/test.sh`, recorded on the task as `_verifier_rel` so writeback lands where it came from (`:47-52`) |
| `agent_sandbox` names/step audit | `evolution/agent_sandbox.py:432-434` | `tests/test_state.py` if it exists, else `tests/test.sh` |
| the A/B summary | `evolution/ab_verifier_author_summary.py:29` | `VERIFIERS = ("tests/test_state.py", "tests/test.sh")` |
| `synth_loop`'s leak regex | `evolution/synth_loop.py:212-213` | `tests/test.sh \| tests/test_*.py \| /oracle/ \| test_state.py` |

The prompts also carry both, in the package table:
"`tests/test_state.py` or `tests/test.sh` | the verifier that grades an attempt"
(`agents/task_evolution.md:23`) and the same pair for the blind verifier
(`agents/verifier_author.md:29`). The probe author is told only that
"The file `tests/test.sh` is a placeholder"
(`agents/independent_verifier_probes.md:6`). The one place a *format* is assumed
is the leak rule the author must obey — "no `pytest` or `test.sh` command"
(`agents/task_evolution.md:308`) — which names both. So: **no prompt assumes
pytest, and none assumes `test.sh`**; the underlying grading contract is the
same either way (`bash tests/test.sh` → `/logs/verifier/reward.txt`,
`README_SEED_DATA.md:38-40`; `prepare_tmax_data.py:38-40`).

**Bottom line.** Four files, all fatal: `instruction.md`,
`environment/Dockerfile`, `solution/solve.sh` and one of
`tests/test_state.py` / `tests/test.sh`. A raw tmax-15k package has two of the
four and dies at `evolve.py:88` on the Dockerfile, after failing to pack into a
row at all under `build_mix_v2`. No converter exists in the training tree; the
conversion is done upstream, in the `Fzz1/Tmax-Tasks-Clean` reaudit split, which
ships a bare `FROM` Dockerfile and a solve.sh per task — which is precisely why
the reaudit half is evolvable and the raw half is not.

---

## 5. MEASURED OUTCOMES

Every number on the ref about how the loop behaves. Grouped, each with its line.

### Acceptance by direction — the reason simplify is off

> "Measured on this corpus: **693 accepted simplifies against 335 accepted
> evolves in one week, and 814 against 26 in an earlier window, with the on-mix
> solve rate climbing while the fixed eval stayed flat.**"
> — `runbook/RUNBOOK.md:916-918`, verbatim again at
> `evolution/evolve_ondella.py:83-87`

"That is the signature of a training set getting easier rather than a policy
getting better. With simplify off, the too-hard tail freezes instead of being
loosened" (`runbook/RUNBOOK.md:918-920`).

### An older whole-loop outcome breakdown (2026-08-24 run, step 39)

`handoff/2026-08-24-terminalworld-online-evolution.md:48`: "Evolution | 434
processed, **237 folded**, 25 pending, 21 rounds". The stage breakdown
(`:56-63`):

```
ok (folded)                        237
revalidate_daytona_oracle_failed    85   <- safety working: harder task broke its own oracle
evolve_blocked                      37
revalidate_daytona_error_failed     34
error                               28
revalidate_shortcut_failed          12   <- safety working: task became trivially shortcuttable
junk_infra                           1
```

"A rejected re-tune is **not** a loss: the original task stays in the pool. The
**55% fold rate** is the safety margin, not waste" (`:65-66`).

### The blind-versus-author verifier A/B

What `ab_verifier_author.sh` compares: the same signals through the loop twice,
`SWE_VERIFIER_AUTHOR=same` then `=blind`, on a dev root restored to identical
state between modes (`evolution/della/ab_verifier_author.sh:2-4, 73-98`); per
mode it records the ledger delta, the task directories, `timing.json` and an
accepted count (`:102-112`). `ab_verifier_author_summary.py` reads that record
and prints, per signal and mode, "what the round decided, how many sessions it
took, wall clock per session and in total, how far the rewrite sits above its
input revision by the size rule, and what the names audit had to say"
(`:2-6`) — and is careful that "Nothing here is a verdict on the flag; the
numbers are what the flag costs and what it changes" (`:6-8`).

The recorded result of the first paired round
(`wd-evolve-dev/ab/20260904-170942`, **six 16/16 signals each way**),
`evolution/evolve_codex.py:934-942`:

> "the per-task time went from a median of **430 s to 974 s** and the round's
> wall clock from **1323 s to 2320 s** on three workers, with the **same six of
> six folded**, the same sizes, and the hidden solution passing the blind
> verifier at first meeting on all six. The verifiers it wrote recompute the
> expected result from the container instead of asserting the solution's
> strings, which is what the split was for."

The runbook states the same as a ratio: "Blind costs about **2.3x** the per-task
loop time (paired round 2026-09-04: median 430 s to 974 s)"
(`runbook/RUNBOOK.md:905`).

### The "8 hardened tasks failed 16 of 16" study

> "Of eight hardened tasks reviewed that a policy failed 16 of 16 times, **five
> failed on exactly this**, three with all the work done."
> — `evolution/agents/verifier_author.md:13-14`

Same study, from the runbook's side: "Reviewed on wd-20260903b: **five of eight**
hardened tasks that went 0/16 failed on a report key or line label only the
reference solution knew, three of them with the work otherwise complete"
(`runbook/RUNBOOK.md:855-857`). And as the design rationale:
"the hidden contract that failed five of eight reviewed 0/16 tasks is then
impossible by construction rather than caught by a heuristic afterwards"
(`evolution/evolve_codex.py:932-934`).

### Hackability-gate removal

> "Of the **148 rewrites** that probe rejected in one week, **29** had 'passed'
> on `cd /app` or `mkdir -p` — a verifier green before anything is done, which
> this probe catches for free — and **100** on the expected artifact printf'd
> into place by a reader who had seen the solution and the verifier. A policy
> that has seen neither cannot write that answer, so those were tasks fit to
> train on, thrown away at a chat call and a sandbox each."
> — `evolution/feedback_loop.py:335-341`

### Names-audit and paths-audit precision

> "Measured on wd-20260904a over **464 rewrites**, the names audit flagged
> **130 literals** without its seed baseline and **none** with it, and **every
> one of the 130 was a false positive** (fstab column names, language keywords,
> environment variables, a jupyter output line); the paths audit rejected
> **nothing across 621 signals**. A gate with no measured precision has no
> business discarding a session."
> — `evolution/feedback_loop.py:448-455`

A separate, looser figure for the same family: "measured on wd-20260903b: the
static audit flags **a third of agentic rewrites**, most of them
README-documented and fine; the container check is what separates those from a
genuinely unsolvable task" (`runbook/RUNBOOK.md:848-850`).

### Step-size rule and the evidence behind it

"the reference solution grows by **3 to 8** non-comment lines over the seed's and
the verifier gains at most **5** assertions… The numbers come from the seed
corpus's own training outcomes (solutions of **14 to 20 lines** have the largest
mixed-signal share; the **0/16 share doubles past 20**; the agentic arm's
unbounded rewrites had a **median of 125 lines** and came back **0/16 five times
in six**)" (`runbook/RUNBOOK.md:859-866`). The prompt's version: student-guided
mode may grow "by at most 8 non-comment lines", operator mode "3 to 8"
(`agents/task_evolution.md:257-260`).

### Signal supply

"roughly **89% of rounds carry 8 signals or fewer**" (`runbook/RUNBOOK.md:967-968`);
"the loop is signal-starved (89% of rounds carry ≤8 signals)"
(`evolution/RUNBOOK.md:255-256`).

### Cost

* Derived from code: "an all-fail simplify is **1 model call and no sandbox**. An
  all-pass evolve is roughly **9-11 calls at high reasoning effort plus two
  sandbox probes**. The codex arm spends considerably more per signal than that."
  (`runbook/RUNBOOK.md:976-978`)
* The 2026-09-14 offline campaign: "**139 TMax tasks (136 accepted, $2,796 on
  Claude Opus 5)**" (`evolution/RUNBOOK.md:465-466`).
* Its cost shape, per `offline_cost.py`: "**60% output tokens**, of which **83%
  was thinking at effort `high`**; **25% cache reads** (Codex resends the whole
  conversation every turn, median **52k tokens**); **45% of the total was spent
  on sessions later lost to infrastructure faults and restarts**"
  (`evolution/RUNBOOK.md:500-503`).
* Throttling limit named: "one account is throttled around **32 concurrent
  sessions**, and its refresh token is revoked if two hosts refresh it"
  (`evolution/RUNBOOK.md:505-507`).

### Claude-proxy findings (all "paid for on 2026-09-14")

`evolution/RUNBOOK.md:487-498` and `evolution/claude_proxy/claude_env.sh:16-23`:
"the default five reconnects a few seconds apart ended **33%** of one batch's
sessions"; "Claude runs about **1.8 turns a minute**, and the verifier and repair
stages take **50-80 turns**"; "LiteLLM's **4,096** default was eaten by thinking
on **14% of turns**"; the proxy caps output at **32,000** tokens, runs one worker
(a multi-worker health check "SIGKILLs a worker whose pong thread loses the GIL
for 5 s, which **64 concurrent 50-80k-token requests** do"), and its cache
breakpoint "makes **97% of input tokens cache reads**".

### Infrastructure losses measured on the same campaign

"the login node's shared `/tmp` filled with other users' files and **every
sandbox boot failed at bind()** until `EVOLVE_SOCK_DIR` pointed at `/dev/shm`;
della-tridao killed the loop's sessions about **four minutes after every launch
of 32 or more** (cause never found; the login node did not)"
(`evolution/RUNBOOK.md:466-470`).

### Task-loss costs quoted to the author as evidence

`agents/task_evolution.md` — every item "was paid for" (`:159`):
**113** tasks marked fragile for end-of-life base images alone (`:176`);
**26** more for `:latest`/untagged (`:177`); **7** for rolling-distro upgrades
(`:179`); **22** with the spaced-heredoc form (`:185`); **40** packages with a
comment inside a `RUN` continuation (`:187`); "five tasks declaring 16 GiB
against an 8 GiB cap produced **704 refused creates** before anyone noticed"
(`:191-192`); "**25 tasks** in one run built correctly and then never reached
running state, costing **1,172 creates** between them" (`:204-205`).

### Difficulty actually moved

`evolution/measure_difficulty.py` is the only instrument, and it is run out of
band: "the loop folds an accepted rewrite back and moves on; **nothing measures
whether the task's difficulty actually moved toward target**. This closes that
loop directly" (`:10-13`). Its own caveats: the legacy
`moved_toward_target` "endpoint metric can be true at the opposite endpoint or on
a graded subset. It does not mean the task entered the target interval"
(`:19-21`); "**Neither metric establishes task validity or stable improvement
without independent evidence**" (`:25-26`). **No measured difficulty-shift result
is recorded on this ref — not found.**
The in-band substitute is `eval_host/difficulty_probe.sh`: "A rewrite's
difficulty is measured, not judged… runs the policy k times per task at the
training rollout's sampling and prints passes per task; 0/k is too hard for that
policy, k/k too easy, the band between is what a fold should land in"
(`runbook/RUNBOOK.md:887-894`), fed rows by `della/probe_rows.sh` (`:2-6`).

### Rejection stages that exist (the vocabulary the counts are keyed by)

`status.json` carries `rejected` "keyed by the stage that rejected"
(`LAYOUT.md:260-261`), with the example `{"oracle": 30, "dark_literals": 8}`
(`:255`). Rewrite statuses: `running | accepted | rejected | blocked | failed |
interrupted | kept` (`LAYOUT.md:194-198`); ledger outcomes `handled | deferred |
junk | superseded` (`LAYOUT.md:167-170`). `publish_evolve_analysis.py` derives
`harder_attempted` = accepted+rejected+blocked+failed on the `harder` job and
`harder_accept_rate = harder["accepted"] / decided`
(`evolution/della/publish_evolve_analysis.py:226-233`) — **the formula exists;
no value of it is recorded on the ref.**

### Training-side context numbers the loop runs inside

Restart cost: "a restart is closer to **two hours** before the first training
step lands… the full **445-rollout** eval… Measured on the 2026-08-28 restart:
14:01 start, 445/445 at ~16:05" and "At **14 self-heal restarts** so far that is
14 blocking evals" (`evolution/RUNBOOK.md:148-162`). Sandbox floor: "measured
create-failure rate is **~1.8% at concurrency 8 and ~1.9% at 32**, so it is
platform noise, not throttling" (`:200-202`).

**Bottom line.** The loop's strongest measured facts are negative: simplify is a
one-way ratchet (693 vs 335, 814 vs 26), the two "unseen name" audits have zero
measured precision (130/130 false positives, 0 rejections in 621 signals), and
the LLM hackability gate discarded 100 good rewrites out of 148. The blind split
is the one change with a paired measurement, and it bought identical acceptance
(6/6 both ways) for 2.3x wall clock. Nothing on the ref records whether a fold
actually moved a task's difficulty.

---

## 6. WHAT THE LOOP DOES NOT DO

The docs have no section literally titled "known gaps" / "TODO" / "open" except
in the 2026-08-24 handoff. Verbatim, with lines:

**From `handoff/2026-08-24-terminalworld-online-evolution.md`, "### D. Open --
infra and data, not RL code" (`:235`):**

* `:237` — "#### D1. Sandbox create failures (~1013 hard) -- data-side". "A
  subset of TerminalWorld task images never reach `STARTED`. This is the largest
  remaining source of unscored rollouts (16-22 per step)… *Recommendation.*
  Oracle build-filter the mix offline" (`:239-245`).
* `:247` — "#### D2. ~10-14 heavy tasks exceed the 10 GiB sandbox disk".
  "`session_disk_exhausted` = 14 events… Deliberately **not** fixed by raising
  the global disk to 30 GiB -- too expensive for 14 events… Low priority."
  (`:249-253`)
* `:255` — "#### D3. Eval cadence cannot keep up -- **fix this first**". "This
  matters more than it looks. `rollout_reward` **falls by design** as tasks are
  evolved harder, so without a fixed-difficulty eval there is no way to separate
  'the model got better' from 'the tasks got harder'." (`:268-271`)
* `:276` — "#### D4. Stale drops (7%) -- do not tune this". "This is a
  **symptom of slow rollout supply**, not a window that is too tight… The lever
  is D1, not this knob." (`:281-284`)
* `:299-300` — "**Compare against a frozen-data control** on the same recipe.
  Without it, evolution's contribution cannot be separated from ordinary
  training."

**Stated non-capabilities elsewhere, verbatim:**

* `evolution/measure_difficulty.py:10-11` — "the loop folds an accepted rewrite
  back and moves on; **nothing measures whether the task's difficulty actually
  moved toward target**."
* `evolution/measure_difficulty.py:25-26` — "Neither metric establishes task
  validity or stable improvement without independent evidence."
* `evolution/feedback_loop.py:452-455` — "the paths audit rejected nothing across
  621 signals. A gate with no measured precision has no business discarding a
  session, so both ride along in the record… and the size rule below stays the
  gate."
* `runbook/RUNBOOK.md:866-868` — "Size is a heuristic for how much the policy has
  to reproduce, not a difficulty measurement; the difficulty probe on the eval
  host (step 6) is the measurement, and the training signal is the final word."
* `evolution/RUNBOOK.md:250-252` — "**Enabling the arm does not establish that its
  rewrites improve student outcomes.**"
* `evolution/RUNBOOK.md:352-353` — "**The verifier is never touched** — difficulty
  comes off the instruction, or the task is worth less." (This sentence is the
  *old* description and is contradicted by the branch's own blind-verifier path;
  it has not been updated.)
* `evolution/README.md:141-142` — "**Difficulty measured once is not a property of
  the task.** The gate samples k=4; re-measuring accepted tasks at k=5 moves
  roughly a third of them out of the band."
* `evolution/results/tw_vs_tmax.md:22` — "**Not measured here: difficulty.** The
  one number that would settle 'better' is how a solver does on each, and only TW
  has been run."
* `evolution/DATA_RELEASES.md:76-78` — "Prepared Dockerfiles require tmux at image
  build time. **This requirement does not prove every image has been built**:
  upstream image tags and package repositories can still change."
* `README_SEED_DATA.md:129-132` — "Audit flags (instruction text quoting verifier
  literals, reward-hackability) **ship as measurements, not filters** -- they do
  not gate this list."
* `handoff/2026-08-29-b300-single-host-bringup.md:144-148` —
  "`daytona_unstartable.ids` from the private ops repo lists 15 tasks that build
  and then never reach running state. **They appear in no HuggingFace id list**,
  and two of them are in a mix built from `train_ready_ids.txt` +
  `main_pool_ids.txt`."
* `evolution/della/ab_verifier_author.sh:98` calls `evolve_dev_round.sh`, which
  is **absent from the ref** — the shipped A/B harness cannot run as written.

**Bottom line.** The loop's own documentation is explicit that it does not
measure whether a fold moved difficulty, does not gate on either unseen-name
audit, does not prove the simplify arm helps, and has never been run against a
frozen-data control. The one operational item the 08-24 handoff called
"fix this first" — eval cadence — is what later moved evaluation to a separate
host (`eval_watcher.sh`, `tb2_eval.sbatch`).

---

## 7. SEED SELECTION ADVICE IN THEIR OWN WORDS

These are written for the *author agent* rewriting a task, but they read
directly as seed-selection criteria: each one names a property that makes a task
survive or die on this platform.

**Runtime environment — tmux.**
> "**The sandbox has to be able to run a terminal agent.** The harness needs
> `tmux` inside the container. It tries the package manager first and falls back
> to building from source, so an image with neither `tmux` in its repositories
> nor a C compiler leaves the agent unable to take a single turn — every rollout
> in the group scores zero having done nothing."
> — `agents/task_evolution.md:164-169`

And from the data side: "**terminus needs tmux -- bake it in at build time.**…
without `--inject-agent-runtime` the whole SWE-Smith (or TerminalWorld) half
scores reward 0. … **The oracle smoke (`solve.sh` direct) does NOT catch a
missing-tmux bug** -- only a real terminus rollout does."
(`README_SEED_DATA.md:220-227`)

**Base image.**
> "**Base images.** Keep the seed's. A pinned, currently-supported base is worth
> more than a convenient one: End-of-life distributions (`vault.centos.org`,
> `archive.debian.org`, Ubuntu 14.04/16.04) serve from archive mirrors that are
> slow and intermittently gone. 113 tasks in this corpus are marked fragile for
> this alone. `:latest` or an untagged base moves under you and several are
> amd64-only. 26 more. A rolling distribution upgraded at build time
> (`pacman -Syu`) fails every build for as long as any upstream breakage lasts.
> 7 more."
> — `agents/task_evolution.md:171-179`

**Dockerfile forms.**
> "`RUN python3 << 'EOF'` with a space before the delimiter: Docker only
> recognises the form with no space… 22 tasks in this corpus had it. A comment
> line inside a `RUN` continuation. The backslash continues into the comment and
> the rest of the command disappears. 40 packages."
> — `agents/task_evolution.md:183-187`

**Resources / image size.**
> "**Resources are a request the platform can refuse, not a hint.** An oversized
> ask is rejected when the sandbox is created, so the task never starts and never
> earns a verdict — five tasks declaring 16 GiB against an 8 GiB cap produced 704
> refused creates before anyone noticed. Memory, disk and cores are not yours to
> set: they are measured. … `./sandbox check --max` measures the task at the
> platform ceiling (4 vCPU / 8 GiB / 10 GiB)… **a reading close to that ceiling
> means the task is unrunnable, not hard.** Do not raise the timeout above what
> the seed already needed."
> — `agents/task_evolution.md:189-202`

**Services / startup — the Daytona-unstartable class.**
> "**Building is not starting.** 25 tasks in one run built correctly and then
> never reached running state, costing 1,172 creates between them. If your
> environment does anything unusual at startup — a service that must bind, an
> entrypoint that waits — prefer the form the seed already proved."
> — `agents/task_evolution.md:204-207`

The concrete list is `evolution/della/daytona_unstartable.ids`: **15 lines**, ids
in the mix's own `label` format — 14 `tw_<digits>` and one SWE-Smith-shaped
`kennethreitz__records.5941ab27.pr_219` (`:1-15`). Eight of the fifteen carry an
inline comment: "`# BUILD_FAILED ~224x each in take8 2026-08-26, purged from
mix_live (1961->1953), pending Dockerfile repair`" (`:8-15`). Nothing in the tmax
tree reads this file — the only reference is prose in
`handoff/2026-08-29-b300-single-host-bringup.md:144`.

**Network.**
> "**The network is available and may be part of a task.** Pin anything fetched
> (URL plus checksum or version) so the reference solution is reproducible, and
> make a failed fetch fail loudly: non-zero exit, the error on stderr, no silent
> fallback. No credentials, nothing that only works through a proxy."
> — `agents/task_evolution.md:324-327`

**Verifier style — fixed literals versus computed expectations.** The single
most-repeated rule:
> "**Every name you depend on -- a key, a label, a file name, a column --
> appears, spelled the same, in `instruction.md` or in a file under
> `environment/` that the instruction points at.** Where the instruction leaves a
> name open, check the value instead: a report line that contains the commit's
> SHA, whichever label it is under; a field equal to the file's SHA-256,
> whichever key holds it."
> — `agents/verifier_author.md:52-56`

> "For supplied-data tasks, derive expectations from the original fixture or
> expected values prepared before solver execution and supplied with the grader.
> **Never use solver-writable replacement inputs as the authority for
> correctness**; copying or hashing them at grading time does not recover the
> original data."
> — `agents/verifier_author.md:86-90` (verbatim again at
> `agents/task_evolution.md:109-112`)

> "File existence, non-empty content, success words in a log, or agreement
> between two solver-written reports cannot alone establish correctness."
> — `agents/verifier_author.md:91-93`

> "Use byte equality only for content required to remain byte-for-byte
> unchanged. … Derive any tolerance from the public requirements and permitted
> encoding error, and record the bound and its basis in the replay contract."
> — `agents/verifier_author.md:107, 135-136`

> "Never invoke `solution/solve.sh` from the verifier: **it is absent during
> training.**"
> — `agents/task_evolution.md:124-125`

**Instruction style (how much to spell out).**
> "Dumping absolute paths, schema fields, exact formats or numbered operational
> steps is what teaches an agent to shortcut instead of work… **Roughly three
> absolute paths is the budget** — the entry point and the main deliverable — and
> never an inventory of intermediate artifacts."
> — `agents/task_evolution.md:150-155`

**Size of the package's own files.**
> "It travels as one line of JSON, so COPY sources together stay under 1 MiB, and
> files under `tests/` are text and together stay under 1 MiB; a binary under
> `tests/` is refused by name, and `./sandbox up` says so."
> — `agents/task_evolution.md:31-33`

**What a good seed looks like at corpus scale.** `README_SEED_DATA.md:128-132`:
"Clean-for-training means exactly three things: the reference solution passes its
verifier, the task builds and runs inside the sandbox platform's limits, and the
metadata reflects the task." And on difficulty:
"**The seed layer is saturated for a frontier model**: roughly a fifth of it
carries usable training signal… seed reference solutions have a median of 10
lines against TB2's 61" (`evolution/README.md:175-178`).

**Bottom line.** Their own selection criteria are almost entirely
platform-shaped: a pinned non-EOL base with tmux reachable, a Dockerfile that
actually parses, resources that fit 4 vCPU / 8 GiB / 10 GiB, nothing unusual at
container startup, network use pinned and loud on failure, and a verifier whose
every literal is readable by the agent or replaced by a computed value. The one
quality criterion is the three-part "clean for training" test, and the one
difficulty criterion is that k/k and 0/k tasks teach nothing.

---

## What a TMax task needs before the evolveloop can process it

One line per requirement, with the code line that enforces it.

1. **A package directory under `$TRL_BASE/data/sources/<corpus>/tasks/<task_id>/`,
   unique across corpora** — `evolution/evolve_ondella.py:421-433`
   (`NoSeed` on zero or on ambiguity).
2. **`instruction.md`, non-empty** — package discovery
   `evolution/evolve_ondella.py:425`; row build
   `prepare_rts_data.py:486-493` (`missing_instruction`) and `:429-430`
   (`empty_instruction_or_verifier` in the reaudit prep,
   `prepare_tmax_reaudit_data.py:429-430`).
3. **`environment/Dockerfile`** (a bare `FROM <prebuilt image>` is enough) —
   `evolution/evolve.py:43, 88-89` (fatal read) and
   `prepare_rts_data.py:489-493` (`missing_dockerfile`, so the row cannot be
   packed at all).
4. **`solution/solve.sh`, and it must score reward 1 in the row's own box** —
   read fatally at `evolution/evolve.py:44, 88-89`; refused as
   `stage="no_solution"` at `evolution/daytona_revalidate.py:268-273`; refused
   in-session at `evolution/agent_sandbox.py:711-712`; graded at
   `evolution/daytona_revalidate.py:349` (`ok = reward >= 1.0 and code == 0 and
   execution["submitted"]`).
5. **`tests/test.sh` (or `tests/test_state.py`), writing `0`/`1` to
   `/logs/verifier/reward.txt`** — `evolution/evolve.py:53, 85-90`;
   `prepare_tmax_reaudit_data.py:431-432`
   (`verifier_writes_no_reward`).
6. **The verifier must FAIL on the untouched workspace** —
   `evolution/agent_sandbox.py:491-513` (in-session `stage=null_probe`) and
   `evolution/feedback_loop.py:482-487` (`stage="null_pass"`).
7. **`tests/` fixtures under 1 MiB, text only; COPY sources under 1 MiB** —
   `evolution/pack_to_dataset.py:232-236` (`fixture_ceiling`, owned by
   `prepare_rts_data._MAX_CONTEXT_BYTES`); refusals at
   `prepare_tmax_reaudit_data.py:416-421, 468-473`.
8. **No `--privileged` / docker-socket / init-system requirement in the
   Dockerfile** — `prepare_rts_data.py:497-498`,
   `prepare_tmax_reaudit_data.py:412-413` (`needs_privileged`).
9. **`tmux` reachable in the image (package manager or a C compiler)** —
   `--inject-agent-runtime` at `prepare_tmax_reaudit_data.py:422-423`; stated as
   a task-killer at `agents/task_evolution.md:164-169`.
10. **The task must boot, not merely build** — no code gate; the observable is
    the zero-turn advisory `rollouter.py:1264-1289` and the manual exclusion
    list `evolution/della/daytona_unstartable.ids:1-15`.
11. **Resources within 4 vCPU / 8 GiB / 10 GiB, and declared `daytona_*` or
    nothing** — `agents/task_evolution.md:189-202`; fleet fallback
    `evolution/daytona_revalidate.py:277`; the loop refuses to start without the
    fleet defaults, `evolution/della/evolveloop_env.sh:15-17`.
12. **The row must still be in `live.jsonl` at fold time** —
    `evolution/evolve_ondella.py:858` (`stage="not_in_mix"`, "no longer in the
    mix"); fold is replace-only, `runbook/RUNBOOK.md:871-875`.
13. **If the row carries a pin hook, `pre_test_sh` and `pre_test_env_identity`
    must both be set or both empty** — `prepare_tmax_reaudit_data.py:42-44`;
    snapshotted for the rewrite at `evolution/evolve_ondella.py:540-551`.
14. **A signal must exist at all: the group must be 0/k or ≥`harder_ratio`** —
    `rollouter.py:1260-1262`, ratio default 1.0
    (`config_registry.py:183-185`), live 0.9 (`runbook/rltrain.env:163`); and
    0/k signals are only handled when `SWE_EVOLVE_SIMPLIFY=1`
    (`evolution/della/evolveloop_env.sh:52`,
    `evolution/evolve_ondella.py:89-93`).
15. **The rewrite must pass the author's own `./sandbox check`** —
    `evolution/evolve_codex.py:654-664` (`_require_checked`).
