# Run 1026b review: what to fix before the next run (2026-10-26)

Review of the first ~107 training steps of run 1026b: Qwen3.5-9B from the base
checkpoint, Terminus-2 harness, agentpick clean-only mix (release `965a2f05`, 724
tasks) with the evolve curriculum on, 96K training context / 180 turns, binary
verifier reward with unsubmitted rollouts graded, TB-2.1 eval at 220K every 25
steps. Task-level data problems are listed separately in
[`DATA_AUDIT_20261026.md`](DATA_AUDIT_20261026.md); this document covers the harness,
the training loop, the evolve rewriter and the masking policy, and ends with a
prioritized list for the next run.

## Where the run stands

| eval step | avg@5 | pass@5 |
|---|---|---|
| 0 (base) | .2225 | .382 |
| 25 | .2584 | .404 |
| 50 | .2337 | .416 |
| 75 | .3101 | .517 |

Training is healthy: no restart, KL ~6e-4, infra errors 0.9%, no KV thrashing on
the rollout engine (KV p50 .55, 11 preemptions in 9 h over 64 generators, prefix
cache hit .98). No reward hacking: nobody touches `/logs/verifier`, immediate
submits stay at 0.1%, and the mid-run rise in `exit`/C-d (2.5% -> 7%) has reversed.

## 1. Context: heredoc echo and the urge notice

25-40% of training rollouts end at the 96K context limit. The context of those
trajectories is terminal output 43%, model reply 37%, thinking 18%, prompt 2%. It
is many medium turns (median 82), not a few huge outputs; the 10,000-byte
observation cap works.

- **The terminal echo doubles every heredoc.** `cat > file << EOF` bodies are 17% of
  the context, and tmux echoes each line back as `> ...`: those echo lines are 28%
  of all terminal characters, **13.8% of total context** on the trajectories that hit
  the wall. 129 of 487 heredoc-written files were rewritten in full more than once.
  **Fix:** collapse runs of echoed `> ` continuation lines in the observation to a
  one-line `[N heredoc lines echoed]` placeholder (in `terminus.py`, not the frozen
  `vanillux_*` files).
- **The urge notice fires as designed but rarely changes the outcome.** Only 22-38%
  of rollouts that saw the last-20% line went on to submit; 45 of 66 sampled ones
  acknowledged it in their thinking ("We're at 97% context") and kept working.
  **Fix:** start urging earlier (`TMAX_CONTEXT_URGE_LEFT_FRAC` 0.3-0.35).

## 2. Masked rollouts: the policy learns to kill its own sandbox

455 training rollouts (0.9%) were masked as infra failures (unscored, dropped).
Most are the policy's own doing:

| class | rollouts | example |
|---|---|---|
| agent killed its own infrastructure | ~175 (73 left the sandbox gone) | `pkill -f ...`, `pkill daytona`, `kill -9 -1`, `kill 1`, `tmux kill-session`, `apt-get remove tmux` (64 tasks) |
| likely agent resource exhaustion | ~70 | `make -j$(nproc)` in a 1 GB box, unbounded DuckDB recursive queries |
| disk full at the verifier upload | 21 | `mkdir /tests: no space left on device` |
| platform / task | ~124 | `tw_14495` (72, see data audit), verifier download HTTP errors, setup timeouts |
| unclear | ~55 | |

- **The disk-exhaustion penalty never fired** (0 times in this run): it only checks
  `failure_stage == "agent"`, while disk-full surfaces at the verifier upload.
- Masking means killing the daemon is never punished, so the behavior spreads.

**Fix (proposed rule; reward 0, trained on, `finish_reason="sandbox_destroyed"`):**
1. Extend the disk rule to `failure_stage in {"agent", "verifier"}` (21 rollouts).
2. Self-destruct: `failure_stage == "agent"`, at least one turn, and either the
   harness sees a PID it recorded (tmux server/pane, Daytona daemon) signalled, or the
   last turn's keystrokes match a short kill/remove pattern list (~175 rollouts).
3. Keep masked: turn-0 failures, platform status timeouts, verifier download/setup
   errors, wall-clock timeouts. Leave memory-loss cases masked until tasks are
   resized (see data audit), then revisit.

Rules 1-2 convert ~1.8 rollouts per step (0.4%). Complementary: hide the Daytona
daemon and tmux PIDs from the agent so broad `pkill -f` cannot reach them.

## 3. Grading

- **A 600 s verifier timeout scores working solutions as 0.** 308 verifier runs hit
  exit 124 and were scored 0. `pymc-devs__pymc-7637`: 46 of 72 rollouts end with
  `100 passed ... in 312s` and still time out (environment setup plus tests).
  **Fix:** raise the verifier timeout for the slow tasks, kill the agent's leftover
  processes before grading, and re-measure the 0-solve timeout tasks.
- Rare verifier-side infra (Python download 5xx, ~10): retry the download.

## 4. Evolve rewriter

85% of rewrites are accepted and the curriculum is working overall (mixed 1..11
groups rose from 52% to 75% of trained groups; 0/12 fell from 33% to 17%), but
rewrite quality has clear gaps that the oracle check cannot see.

- **Easier rewrites mostly miss.** 491 of 773 trained easier revisions stayed 0/12
  (274 landed in 1..11, 8 overshot). The rewriter predicts 4.6/12 on average; the
  actual mean is 1.4. Most 0/12 groups are context-limit failures, and the easier
  route is pinned to a vague hint (`SWE_SIMPLIFY_HINT=vague`), which cannot fix that.
  146 tasks got 3+ easier rewrites in a row, 10 got 6.
- **Harder rewrites often don't make the task harder, or overshoot.** 46 of 291
  trained harder revisions stayed 12/12, 64 collapsed to 0/12. Example:
  `software-engineering_20260417_112147_020` was rewritten harder 6 times and every
  revision trained 12/12: each round appended requirements (pinned build
  requirements, a self-contained wheel, a complete sdist) that the standard fix
  already satisfies. 73 tasks went harder -> 0/12 -> easier, undoing each other. 16%
  of accepted harder rewrites (53 of 335) changed tests without changing the instruction.
- **Easier rewrites weaken verifiers.** 72% of accepted easier rewrites (620 of
  863) touched tests; e.g. `virtuslab__git-machete-330` replaced a 58-line negative-control test
  with a 15-line `isinstance` check and deleted the matching instruction line, then
  trained 12/12.
- **Long chains drift off the original skill** (`amueller__word_cloud…` r0 -> r5).
- **Gate:** the Daytona oracle rejected 5 rewrites; semantic probes are the only real
  gate. `security_*` tasks fail 31% (provider cyber-policy refusals, 1,553 logged).
- **Latency:** signal -> accepted p50 22 min, but accepted -> first trained group p50
  136 min (~20 steps); 128 accepted revisions were never trained.

**Fix:**
- Harder rewrites predict an expected solve count too, and a quick check on the
  current policy (or a proxy) rejects revisions that still solve 4/4.
- Escalate easier rewrites (vague -> structural -> specific) and park a task after
  2 rewrites in one direction that did not move it; on an easier signal right after a
  harder rewrite, revert to the parent revision.
- Guard tests on the easier route: verifier edits only as declared repairs, and new
  tests must still reject the previous revision's negative controls.
- Cap chain depth (~3) and judge skill retention against r0.
- Route or exclude `security_*` tasks; put accepted revisions at the front of the
  queue; fix the zeroed `usage` field in `rewrite.json`.

## 5. Off-policy drops

894 of 4,355 finalized groups (20.5%) were dropped as stale (`SWE_OFFPOLICY_STEPS=5`),
57% of them gradient-bearing and skewed toward long, hard tasks (42% all-fail vs 26%
among trained groups). Their age is 6 (549), 7 (146), 8 (24), 9 (3):
**`SWE_OFFPOLICY_STEPS=7` recovers 96% of them (97% of the gradient-bearing ones).**
In-flight groups are set separately (`SWE_INITIAL_ACTIVE_GROUPS=200`), so raising the
age limit does not add lag. Planned for the next run together with score centering
(`SWE_LOSS=sc`).

## Next run: prioritized

| # | change | where | expected effect |
|---|---|---|---|
| 1 | apply the data audit (exclude 3, resize ~12, drop or re-check the 60 never-solved, 10 near-always-solved) | data release | ~12% rollout compute back |
| 2 | `SWE_OFFPOLICY_STEPS=7` + score centering | launcher | ~96% of stale drops recovered, more hard-task signal |
| 3 | collapse heredoc echo in observations | `terminus.py` | ~14% context back on wall-hitting trajectories |
| 4 | penalize self-destruct and verifier-stage disk-full | `rollouter.py` | stops the kill-the-daemon habit (~1.8 rollouts/step) |
| 5 | earlier urge (0.3-0.35) | launcher env | more submits before the wall |
| 6 | verifier timeout for slow tasks; kill leftovers before grading | data + `grading.py` | working solutions stop scoring 0 |
| 7 | rewriter: harder prediction + quick check, easier escalation/park/revert, test guard, chain cap, queue priority | evolve | fewer wasted or harmful rewrites |
