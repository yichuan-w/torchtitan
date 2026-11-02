# TB-2.1 overfit bench: results

Tasks from `tb21_tasks.txt` (the 48 the starting 9B never solved at step 0). A task counts as
overfit once the original task passes reliably: a training group on the original at half or more
of its attempts, or a readout of 4/16 or more. The 64-attempt confirm (README) is the stricter
check where it has run.

| task | overfit at | evidence | arm |
|---|---|---|---|
| sqlite-db-truncate | step 100 | readout 10/16 (pass@5 1.0); training groups on the original up to 11/12 | recipe, original share 1/2 once it passed |
| db-wal-recovery | step ~95 | training group on the original 8/12 (others 7/12, 6/12, 5/12); readout 4/16 at step 90 | recipe |

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

- [ ] tune-mjcf (base 16/73) - queued
- [ ] pytorch-model-recovery (9/74) - queued
- [ ] adaptive-rejection-sampler (7/73) - queued
- [ ] code-from-image (7/75) - queued
- [ ] model-extraction-relu-logits (5/75) - set aside: readout 1/16 at step 0
- [ ] extract-elf (5/75) - queued
- [ ] train-fasttext (4/73) - set aside: readout 1/16 at step 0; original passed 2 training groups
- [ ] polyglot-rust-c (4/74) - queued
- [ ] financial-document-processor (3/75) - queued
- [ ] bn-fit-modify (1/75) - queued
- [ ] mteb-retrieve (1/75) - queued
- [ ] sanitize-git-repo (1/75) - queued
- [ ] schemelike-metacircular-eval (1/75) - queued

### Base never, a trained model sometimes (8)

- [x] sqlite-db-truncate (base 0/74) - overfit at step 100
- [ ] llm-inference-batching-scheduler (0/75) - running (160K/300 turns)
- [ ] mailman (0/70) - queued
- [ ] mteb-leaderboard (0/73) - queued
- [ ] overfull-hbox (0/75) - queued
- [ ] regex-log (0/75) - queued
- [ ] sparql-university (0/75) - queued
- [ ] winning-avg-corewars (0/74) - queued

### Never solved, by the base or a trained model (27)

- [x] db-wal-recovery (base 0/75) - overfit at step ~95
- [ ] raman-fitting (0/74) - running (160K/300 turns); readouts 3/16, 2/16; confirm at step 60 3/64
- [ ] install-windows-3.11 (0/71) - running; original passed 2 training groups
- [ ] filter-js-from-html (0/75) - running; original passed 2 training groups
- [ ] torch-tensor-parallelism (0/75) - running (160K/300 turns)
- [ ] torch-pipeline-parallelism (0/75) - running (160K/300 turns)
- [ ] path-tracing (0/75) - running (160K/300 turns)
- [ ] path-tracing-reverse (0/75) - running (160K/300 turns)
- [ ] write-compressor (0/74) - running (160K/300 turns)
- [ ] sam-cell-seg (0/72) - running (160K/300 turns)
- [ ] gcode-to-text (0/75) - running
- [ ] extract-moves-from-video (0/73) - running
- [ ] large-scale-text-editing (0/73) - running
- [ ] make-mips-interpreter (0/74) - running
- [ ] circuit-fibsqrt (0/74) - paused (160K, 2 steps); queued
- [ ] video-processing (0/75) - paused (0 of 221 original attempts passed); queued
- [ ] dna-insert (0/75) - paused (135 steps at 80K, readout 0); queued
- [ ] make-doom-for-mips (0/74) - running
- [ ] regex-chess (0/75) - queued
- [ ] chess-best-move (0/73) - queued
- [ ] polyglot-c-py (0/75) - queued
- [ ] gpt2-codegolf (0/75) - queued
- [ ] feal-differential-cryptanalysis (0/74) - queued
- [ ] feal-linear-cryptanalysis (0/74) - queued
- [ ] protein-assembly (0/75) - queued
- [ ] dna-assembly (0/73) - queued
- [ ] reshard-c4-data (0/74) - queued
