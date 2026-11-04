# TB-2.1 overfit bench: results

Tasks from `tb21_tasks.txt` (the 48 the starting 9B never solved at step 0). Two bars, recorded
side by side:

- **Overfit (lenient, for tracking):** a training group on the original at half or more of its
  attempts, or a single readout of 3/16 or more (pass@5 about 0.71 on that readout; the user's bar
  from 2026-11-03, down from 4/16). Boxes in the Tracker are ticked on this bar.
- **Solved (two-stage):** a readout of 2/16 or more passes the screen; the checkpoint of that same
  step then gets the 64-attempt confirm (README), and 13/64 or more counts as solved, the
  pass@5 >= 0.5 line. A single readout at 4/16 is not enough on its own: read every 5 steps, a task
  at a 10% per-attempt rate (pass@5 0.41) crosses 4/16 within 50 steps about half the time, against
  ~5% under the two stages (#307).

**Leak audit (2026-11-03).** After 068e24a0 told the teacher to show the tests' convention and ship
checkers early, model-extraction-relu-logits got two versions carrying the original's grading
network, so its solve is void. An audit of every version behind the solved rows found no grading
material elsewhere; three rows are flagged for hints that state hidden-check specifics: extract-elf
(one version spells out the reference's section/word convention), mteb-retrieve (one version names
the reference's `SciFact`/`PromptType` arguments), sanitize-git-repo (versions name the edge cases
the hidden tests probe). From 9d033b16 the teacher may not put the original's tests, verifier,
hidden data, seeds, reference solution or hidden-check specifics where the student sees them,
validate() refuses versions carrying lines of the original's tests or solution, and a restarted
teacher retires such versions from the pool (a25621c8).

| task | overfit at | evidence | 64-attempt confirm | arm |
|---|---|---|---|---|
| sqlite-db-truncate | step 100 | readout 10/16 (pass@5 1.0); training groups on the original up to 11/12 | not run yet | recipe, original share 1/2 once it passed |
| db-wal-recovery | step ~95 | training group on the original 8/12 (others 7/12, 6/12, 5/12); readout 4/16 at step 90 | step 90: **17/64**, solved | recipe |
| raman-fitting | step 55 | readout 3/16 at steps 55, 105, 115 and 161 (base 1/16 at step 0, 0/74 across earlier step-0 runs); training groups on the original up to 3/10 and 3/12 | step 60: 3/64; step 160: 5/64 | recipe, then from step 135 the fast arm (16 groups, 2 rollout engines, default compile); original share 0.5 and a teacher-set share above it |
| code-from-image | step 0 | readout 4/16 at step 0, before any training: the base already meets the bar (base 7/75 across earlier step-0 runs, 0/5 in 1031b) | not run | fast arm, original share 0.5 |
| tune-mjcf | step 0 | training group on the original 6/12 in the first steps, before any update reached the rollouts (others 5/12, 3/12); the base already meets the bar (base 16/73 across earlier step-0 runs) | not run | fast arm, original share 0.5 |
| pytorch-model-recovery | step 0 | readout 4/16 at step 0 and a training group on the original 8/12 before any update reached the rollouts; the base already meets the bar | not run | fast arm, original share 0.5 |
| adaptive-rejection-sampler | step 10 | readout 4/16 at step 10 (step 0: 2/16, step 5: 1/16; base 7/73 across earlier step-0 runs); training groups on the original up to 4/12 | not run | fast arm, original share 0.5 |
| model-extraction-relu-logits (void: leaked) | step ~37 | training groups on the original 6/12 and 6/12 at steps ~36-37 (earlier groups 4/12 and 5/12 from step ~24); readout 1/16 at step 0, 2/16 at step 15 | not run | fast arm, original share 0.5 |
| extract-elf (hint flag) | step 5 | readout 4/16 at step 5 (step 0: 0/16; base 5/75 across earlier step-0 runs); training groups on the original up to 4/12 | not run | fast arm, original share 0.5 |
| financial-document-processor | step 15 | readout 5/16 at step 15 and 6/16 at step 20 (step 0: 0/16, step 10: 1/15; base 3/75 across earlier step-0 runs); training groups on the original up to 5/12 | not run | fast arm, original share 0.5 |
| sanitize-git-repo (hint flag) | step 10 | readout 3/16 at step 10, 4/16 at steps 25 and 30 (step 0: 1/15, step 5: 0/16; base 1/75 across earlier step-0 runs); training groups on the original up to 4/12 | not run | fast arm, original share 0.5 |
| mteb-retrieve (hint flag) | step 16 | readout 4/16 at step 16 (step 0: 0/16, step 5: 1/16; base 1/75 across earlier step-0 runs); training groups on the original up to 4/12 | not run | fast arm, original share 0.5 |
| bn-fit-modify | step 20 | readout 3/16 at step 20 (step 0: 0/16; base 1/75 across earlier step-0 runs); training groups on the original up to 2/12 | not run | fast arm, original share 0.5 |
| overfull-hbox | step ~54 | training group on the original 7/12 at step ~54 (earlier groups 5/11, 5/12, 4/12 from step ~40); readout 1/16 at steps 0-30, 2/16 at step 41; base 0/75 | not run | fast arm, original share 0.25, then 0.5 once it passed; teacher on the 068e24a0 guidance until 12:11, then 9d033b16 (no version of it carried lines of the original's tests or solution) |
| polyglot-rust-c | step 10 | readout 8/16 at step 10 (steps 0 and 5: 1/16; base 4/74); training groups on the original up to 4/12 | not run | fast arm, original share 0.5, then 0.25 from 07:17 (093dd27c); its one version that repeated a line of the original's solution (a bare C `main` signature) was retired at 12:12 |
| model-extraction-relu-logits (clean rerun) | step ~34 | training group on the original 6/12 at step ~34 (earlier groups 2/12-3/12 from step 2); readout 1/16 at step 0, 2/16 at step 5; fresh job dir `--teacherv4-gb300`, teacher from 9d033b16 with no earlier rounds; its 2 versions carry no line of the original's tests or solution and state no hidden-check specifics | not run | fast arm, original share 0.5 |

Learned what made db-wal-recovery pass: XOR the WAL with 0x42 (or copy it aside) before any
sqlite call, since opening the database while the WAL is still encrypted deletes it. Its
readouts at steps 15-95 before 2026-11-02 ran on step-10 weights (the eval-pull race fixed in
ad954878) and do not count.

## Tracker

The 48 tasks, by how often the starting 9B solves them: its step-0 rollouts across 15 runs from
10-15 to 10-31 (about 75 per task; every task here was 0/5 in 1031b), and whether a trained model
in run 1030c solved it (#309). A box is ticked once the task is overfit (rule above). Status as of
2026-11-02.

### Base sometimes solves it (13)

- [x] tune-mjcf (base 16/73) - at the bar at step 0, untrained (original group 6/12)
- [x] pytorch-model-recovery (9/74) - at the bar at step 0, untrained (readout 4/16)
- [x] adaptive-rejection-sampler (7/73) - overfit at step 10 (readout 4/16; step 0 was 2/16)
- [x] code-from-image (7/75) - at the bar at step 0, untrained (readout 4/16)
- [x] model-extraction-relu-logits (5/75) - overfit at step ~34 in a clean rerun (original group 6/12); the earlier solve at step ~37 stays void (its versions leaked the grading network)
- [x] extract-elf (5/75) - overfit at step 5 (readout 4/16; step 0 was 0/16)
- [ ] train-fasttext (4/73) - paused 2026-11-03 at step 6: readout 1/16 at step 0, original passed training groups up to 1/12; attempts run ~1 h each (model training in the sandbox, 18% flagged OOM), ~8 generations in flight, 6 steps in 3 h
- [x] polyglot-rust-c (4/74) - overfit at step 10 (readout 8/16; steps 0 and 5 were 1/16)
- [x] financial-document-processor (3/75) - overfit at step 15 (readout 5/16, then 6/16 at step 20; step 0 was 0/16)
- [x] bn-fit-modify (1/75) - overfit at step 20 (readout 3/16; step 0 was 0/16)
- [x] mteb-retrieve (1/75) - overfit at step 16 (readout 4/16; step 0 was 0/16)
- [x] sanitize-git-repo (1/75) - overfit at step 10 (readout 3/16, then 4/16 at steps 25 and 30; step 0 was 1/15)
- [ ] schemelike-metacircular-eval (1/75) - queued

### Base never, a trained model sometimes (8)

- [x] sqlite-db-truncate (base 0/74) - overfit at step 100
- [ ] llm-inference-batching-scheduler (0/75) - paused at step 42 (160K/300 turns; node budget 8 from 2026-11-02 09:00)
- [ ] mailman (0/70) - queued
- [ ] mteb-leaderboard (0/73) - queued
- [x] overfull-hbox (0/75) - overfit at step ~54 (training group on the original 7/12; base 0/75)
- [ ] regex-log (0/75) - queued
- [ ] sparql-university (0/75) - queued
- [ ] winning-avg-corewars (0/74) - queued

### Never solved, by the base or a trained model (27)

- [x] db-wal-recovery (base 0/75) - overfit at step ~95; 64-attempt confirm 17/64 at step 90
- [x] raman-fitting (0/74) - overfit at step 55 on the 3/16 bar (readout 3/16 at 55, 105, 115, 161); 64-attempt confirms: step 60 3/64, step 160 running
- [ ] install-windows-3.11 (0/71) - running; original passed 2 training groups, readout 1/16 at step 10
- [ ] filter-js-from-html (0/75) - ran the full 200 steps; original passed 2 training groups, every readout 0/16; retired 2026-11-03
- [ ] torch-tensor-parallelism (0/75) - ran the full 200 steps, every readout 0/16; retired 2026-11-03. Rerun from step 0 on 2026-11-03 (c6adef) under the 068e24a0 teacher_agents.md, which named this task with an earlier run's diagnosis: not comparable with other jobs (#307); its versions carrying the original's tests were retired at 12:12
- [ ] torch-pipeline-parallelism (0/75) - running (back on the recipe: at 300 turns most attempts hit the 2 h budget)
- [ ] path-tracing (0/75) - paused at step 32 (160K)
- [ ] path-tracing-reverse (0/75) - running (160K/300 turns)
- [ ] write-compressor (0/74) - paused at step 18 (160K)
- [ ] sam-cell-seg (0/72) - paused at step 25 (160K)
- [ ] gcode-to-text (0/75) - running
- [ ] extract-moves-from-video (0/73) - paused at step 24
- [ ] large-scale-text-editing (0/73) - running
- [ ] make-mips-interpreter (0/74) - running again from 2026-11-03 (resumed from step 5)
- [ ] circuit-fibsqrt (0/74) - paused (160K, 2 steps); queued
- [ ] video-processing (0/75) - paused (0 of 221 original attempts passed); queued
- [ ] dna-insert (0/75) - paused (135 steps at 80K, readout 0); queued
- [ ] make-doom-for-mips (0/74) - paused at step 15 (17 of 79 groups mixed)
- [ ] regex-chess (0/75) - paused at step 0 (160K; originals ran out of 80K context)
- [ ] chess-best-move (0/73) - queued
- [ ] polyglot-c-py (0/75) - queued
- [ ] gpt2-codegolf (0/75) - queued
- [ ] feal-differential-cryptanalysis (0/74) - queued
- [ ] feal-linear-cryptanalysis (0/74) - queued
- [ ] protein-assembly (0/75) - queued
- [ ] dna-assembly (0/73) - queued
- [ ] reshard-c4-data (0/74) - queued
