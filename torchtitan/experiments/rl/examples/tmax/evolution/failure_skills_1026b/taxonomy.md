# A: Failure taxonomy of 1026b pv75 (TB-2.1, avg@5 .310, 138/445 passed, pass@5 .517)

Source: `validation_traces/step-75/index.json` (copy: idx75.json); evidence = pytest FAILED/E lines in verifier.output_tail
(ctrf is null for failed trials) plus trace spot-checks. 307 reward-0 trials; 4 infra (3 `error` + 1 infra hit_max_turns) excluded -> 303 scored.
Labels per trial: labels75.json. Rule: a trial that ended on time/context/turn cap is labelled by the cap (M4/M5), since no
finished artifact was verified; `submit` trials are labelled by task-level evidence from the failing tests.

## Modes
- M1 core algorithm / domain knowledge: submitted a confident but wrong answer (wrong key, wrong primers, wrong moves, wrong fit, spawn crash in PP/TP).
- M2 spec-following miss: the output exists but breaks a stated constraint (reshard 330 files/dir vs 30 max; overfull-hbox edits words not in synonyms.txt; CWE id; zero-padding; "uname -r" written literally).
- M3 happy-path self-check: the agent's own test passed but the verifier probed an edge/adversarial/completeness case (XSS vectors, second HF token, SIGINT cleanup, regex date edges, tie-break preference, missing `np` import in a path the agent never ran).
- M4 time budget (5400 s): long builds/training (protein-assembly, train-fasttext, caffe, compcert, pytorch-model-recovery, rstan).
- M5 context (220K) / turn (300) cap: rewrite-debug loops on big systems builds (doom/mips, path-tracing, write-compressor, feal-linear, polyglot).
- M6 env/service: shell exited, Windows VM / mailman stack broke.
- M7 performance threshold missed: query-optimize, portfolio 0.64x vs 1.2x, eigenval speedup, tune-mjcf, batching scheduler.
- "gave up / premature submit" was not separable: low-turn submits (gcode-to-text, torch PP/TP) were confident wrong answers -> M1. 0 trials.

## Counts (303 scored failed trials)
| mode | trials | % of 304 scored fails | tasks | trials in near-miss tasks (1-4/5) | trials in 0/5 tasks | representative tasks |
|---|---|---|---|---|---|---|
| M1 Wrong core algorithm / missing domain knowledge (submitted wrong answer) | 98 | 32% | 28 | 20 | 78 | db-wal-recovery (5), extract-elf (5), gcode-to-text (5), gpt2-codegolf (5), raman-fitting (5), sparql-university (5) |
| M2 Spec-following miss (output path/format/schema, persistence, stated constraint) | 17 | 6% | 9 | 7 | 10 | reshard-c4-data (5), overfull-hbox (4), fix-code-vulnerability (2), cobol-modernization (1), count-dataset-tokens (1), crack-7z-hash (1) |
| M3 Happy-path only: edge/adversarial case or completeness the self-check never exercised | 33 | 11% | 12 | 18 | 15 | filter-js-from-html (5), regex-log (5), sanitize-git-repo (5), cancel-async-tasks (4), headless-terminal (4), financial-document-processor (2) |
| M4 Ran out of 5400 s time budget (long build/train/compute, slow iteration) | 50 | 17% | 24 | 18 | 32 | protein-assembly (5), train-fasttext (5), caffe-cifar-10 (4), pytorch-model-recovery (4), rstan-to-pystan (4), sam-cell-seg (4) |
| M5 Ran out of context or turn cap without converging (rewrite/debug loops) | 86 | 28% | 32 | 20 | 66 | make-doom-for-mips (5), make-mips-interpreter (5), path-tracing (5), dna-assembly (4), feal-linear-cryptanalysis (4), path-tracing-reverse (4) |
| M6 Environment/service setup broke (agent exited shell, VM/GUI/mail stack) | 6 | 2% | 2 | 0 | 6 | install-windows-3.11 (4), mailman (2) |
| M7 Performance target missed (speed/cost/quality threshold) | 12 | 4% | 5 | 7 | 5 | query-optimize (4), llm-inference-batching-scheduler (3), largest-eigenval (2), tune-mjcf (2), portfolio-optimization (1) |
| M9 Other / unclear (no failing test captured) | 1 | 0% | 1 | 1 | 0 | mcmc-sampling-stan (1) |

INFRA (excluded): 4 trials

| finish_reason | trials | modes |
|---|---|---|
| submit | 165 | M1:98, M2:17, M3:33, M6:4, M7:12, M9:1 |
| hit_context_limit | 63 | M5:63 |
| hit_time_budget | 50 | M4:50 |
| hit_max_turns | 23 | M5:23 |
| terminal_exited | 2 | M6:2 |

## Takeaways for the task rewriter
- Near-miss tasks (1-4/5, most trainable) are dominated by M3 (18), M1 (20, mostly precision: count-dataset-tokens, crack-7z, merge-diff), M4 (18), M5 (20) and M7 (7).
  M2+M3+M7 = 32 near-miss trials: 'right idea, didn't verify against the real spec' is the trainable band -> drill: re-read spec constraints, adversarial/edge self-tests, measure perf against the stated target before submitting.
- 0/5 tasks are dominated by M1 (78) and M5 (66): domain gaps (biology primers, cryptanalysis, chess, MIPS/Doom, path tracing) and context exhaustion on large builds. Context/turn exhaustion = 28% of failures overall -> drill: context-economical work (avoid cat-ing huge files, incremental builds, stop rewriting from scratch).
- Time-outs (M4, 17%) cluster on compile/train tasks -> drill: background long jobs, smaller configs first, budget awareness.

## Per-task mode table (tasks with <5/5)
| task | pass/5 | failed trials by mode |
|---|---|---|
| adaptive-rejection-sampler | 0 | M4:2, M5:3 |
| break-filter-js-from-html | 0 | M1:3, M5:2 |
| chess-best-move | 0 | M1:3, M5:2 |
| circuit-fibsqrt | 0 | M4:2, M5:3 |
| db-wal-recovery | 0 | M1:5 |
| dna-assembly | 0 | M1:1, M5:4 |
| dna-insert | 0 | M1:3, M5:2 |
| extract-elf | 0 | M1:5 |
| extract-moves-from-video | 0 | M1:4, M4:1 |
| feal-differential-cryptanalysis | 0 | M1:4, M5:1 |
| feal-linear-cryptanalysis | 0 | M4:1, M5:4 |
| filter-js-from-html | 0 | M3:5 |
| gcode-to-text | 0 | M1:5 |
| gpt2-codegolf | 0 | M1:5 |
| install-windows-3.11 | 0 | INFRA:1, M6:4 |
| large-scale-text-editing | 0 | M1:2, M4:2, M5:1 |
| llm-inference-batching-scheduler | 0 | M4:1, M5:1, M7:3 |
| mailman | 0 | M5:3, M6:2 |
| make-doom-for-mips | 0 | M5:5 |
| make-mips-interpreter | 0 | M5:5 |
| mteb-leaderboard | 0 | M1:3, M4:1, M5:1 |
| overfull-hbox | 0 | M2:4, M4:1 |
| path-tracing | 0 | M5:5 |
| path-tracing-reverse | 0 | M1:1, M5:4 |
| polyglot-c-py | 0 | M4:1, M5:4 |
| polyglot-rust-c | 0 | INFRA:1, M5:4 |
| protein-assembly | 0 | M4:5 |
| qemu-alpine-ssh | 0 | INFRA:1, M4:1, M5:3 |
| raman-fitting | 0 | M1:5 |
| regex-chess | 0 | M4:1, M5:4 |
| regex-log | 0 | M3:5 |
| reshard-c4-data | 0 | M2:5 |
| sam-cell-seg | 0 | M2:1, M4:4 |
| sanitize-git-repo | 0 | M3:5 |
| sparql-university | 0 | M1:5 |
| sqlite-db-truncate | 0 | M1:5 |
| torch-pipeline-parallelism | 0 | M1:5 |
| torch-tensor-parallelism | 0 | M1:4, M5:1 |
| train-fasttext | 0 | M4:5 |
| tune-mjcf | 0 | M4:3, M7:2 |
| video-processing | 0 | M1:5 |
| winning-avg-corewars | 0 | M1:5 |
| write-compressor | 0 | M4:1, M5:4 |
| bn-fit-modify | 1 | M1:4 |
| build-pov-ray | 1 | M4:1, M5:3 |
| caffe-cifar-10 | 1 | M4:4 |
| cancel-async-tasks | 1 | M3:4 |
| fix-ocaml-gc | 1 | M4:1, M5:3 |
| headless-terminal | 1 | M3:4 |
| mteb-retrieve | 1 | M1:4 |
| pytorch-model-cli | 1 | M1:4 |
| pytorch-model-recovery | 1 | M4:4 |
| qemu-startup | 1 | M2:1, M5:3 |
| query-optimize | 1 | M7:4 |
| rstan-to-pystan | 1 | M4:4 |
| schemelike-metacircular-eval | 1 | M5:4 |
| distribution-search | 2 | M1:1, M5:2 |
| model-extraction-relu-logits | 2 | M1:1, M4:1, M5:1 |
| password-recovery | 2 | M1:3 |
| code-from-image | 3 | M1:2 |
| compile-compcert | 3 | M4:2 |
| crack-7z-hash | 3 | M2:1, M5:1 |
| financial-document-processor | 3 | M3:2 |
| fix-code-vulnerability | 3 | M2:2 |
| fix-git | 3 | M3:2 |
| git-multibranch | 3 | M3:1, M5:1 |
| largest-eigenval | 3 | M7:2 |
| log-summary-date-ranges | 3 | M3:2 |
| mcmc-sampling-stan | 3 | M4:1, M9:1 |
| build-cython-ext | 4 | M3:1 |
| cobol-modernization | 4 | M2:1 |
| configure-git-webserver | 4 | INFRA:1 |
| constraints-scheduling | 4 | M3:1 |
| count-dataset-tokens | 4 | M2:1 |
| custom-memory-heap-crash | 4 | M5:1 |
| hf-model-inference | 4 | M2:1 |
| merge-diff-arc-agi-task | 4 | M1:1 |
| openssl-selfsigned-cert | 4 | M3:1 |
| portfolio-optimization | 4 | M7:1 |
| vulnerable-secret | 4 | M5:1 |
