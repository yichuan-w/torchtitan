# 1026b pv75 (avg@5 .310): the 43 tasks with 0/5, why, and what is trainable

Source: validation_traces/step-{0..268}/index.json (per-trial reward, finish_reason, verifier tail) and
step-75 traces (last turns read for ~16 tasks). "Ever" = eval steps where >=1 of 5 trials passed.

Caveat for every time-budget failure: eval generators thrash their prefix cache in the second half of a
pass (KV .95-.99, prefix hit .01-.05, ~35 s/turn, 97% of the 5400 s spent waiting on generation). Time-budget
failures below are partly infra; re-check them under the 32-gen / 0.9 eval before drilling.

Categories: a = missing domain knowledge/algorithm, b = long-horizon engineering, c = time/compute bound,
d = process failure (no real verification, spec misread, fabrication), e = environment/infra, f = likely beyond 9B now.

| task | ever solved | cat | why it fails at pv75 | trainable skill |
|---|---|---|---|---|
| adaptive-rejection-sampler | 0,25,125,200,268 | c,d | 2 time / 3 context; long R iteration, statistical test vs known truth fails | S8, S2 |
| break-filter-js-from-html | 25,50,150,175 | a,d | output does not actually bypass the filter; no real-browser check | S2 |
| chess-best-move | - | a,f | board reconstruction from image fails (hand-built FEN), never trusts a tool | S5 |
| circuit-fibsqrt | - | b,f | gate-level design; context/time exhausted | (skip) |
| db-wal-recovery | - | d,a | WAL never decoded; model INVENTED the 6 missing rows from a "pattern" and submitted | S1 |
| dna-assembly | - | a | primer design rules (overhangs/annealing) wrong; checklist "verified" | S4, S2 |
| dna-insert | - | a,d | primers pass its own Tm checklist but test_primers fails | S4, S2 |
| extract-elf | 150 | a,d | ELF vaddr mapping wrong; "tested" only for self-consistency | S1, S2 |
| extract-moves-from-video | - | c,e | video/transcription pipeline; content similarity fails | (re-check infra) |
| feal-differential-cryptanalysis | - | a,f | attack wrong/slow; 1 verifier 900 s timeout | S8 (low priority) |
| feal-linear-cryptanalysis | - | a,f | 4/5 context limit; attack never converges | (skip) |
| filter-js-from-html | - | d | ✓-checklist confidence; no adversarial XSS corpus tried | S2 |
| gcode-to-text | - | d | answered a metadata string ("MK4S") instead of rendering the toolpath text | S5 |
| gpt2-codegolf | - | f | full GPT-2 in tiny C | (skip) |
| install-windows-3.11 | - | b,e | QEMU GUI + VNC key automation | (skip) |
| large-scale-text-editing | - | d,c | vim macro output not equivalent; time | S9 |
| llm-inference-batching-scheduler | - | a,d | perf thresholds missed; no measurement against threshold | S8 |
| mailman | - | d,b | stuck in an interactive shell/REPL, C-d loops, terminal exited | S6 |
| make-doom-for-mips | - | b,f | 5/5 context limit | (skip) |
| make-mips-interpreter | - | b | 4/5 context limit; rewrites, no incremental progress | S7 |
| mteb-leaderboard | 100 | c,e | data fetch/compute heavy; data mismatch | (re-check infra) |
| overfull-hbox | 50,100,125,150 | d | edits text that must stay identical (test_input_file_matches) | S9 |
| path-tracing | - | b,a | image similarity; 5/5 context limit | S7 |
| path-tracing-reverse | - | b,a | reverse-engineering a renderer; context | S7 |
| polyglot-c-py | 125,150 | a,d | blind trial-and-error until max turns | S10 |
| polyglot-rust-c | 100..250 (7 evals) | a,d | blind syntax trials until max turns; solvable | S10 |
| protein-assembly | - | c,a | 5/5 time budget | (re-check infra) |
| qemu-alpine-ssh | 50 | b,e | VM boot; verifier timeouts | S6 |
| raman-fitting | - | a,d | wrong x-scaling; accepted a 2D peak at 3372 cm-1 (expected ~2700) | S4 |
| regex-chess | - | f | regex chess engine | (skip) |
| regex-log | 125,175,200,268 | d | regex edge cases (dates) missed | S2 |
| reshard-c4-data | 125,200,250,268 | d | own round-trip passes; hidden workflow constraint missed | S9, S2 |
| sam-cell-seg | 200,268 | c | 4/5 time budget; verifier timeout | (re-check infra), S8 |
| sanitize-git-repo | 25,50,175,200,250,268 | d | misses secrets / wrong replacement placeholders | S9, S2 |
| sparql-university | 125,200,225 | a,d | query semantics; results not checked by hand | S2 |
| sqlite-db-truncate | - | a,d | did not parse SQLite record format (varints/serial types); guessed byte values | S1 |
| torch-pipeline-parallelism | - | a,d | never ran >1 process; semantics wrong | S3 |
| torch-tensor-parallelism | - | a,d | ✓-checklist "verified" at world_size=1 only; shard/gather wrong | S3 |
| train-fasttext | 0 | c,e | compile+train in budget; verifier compile timeout | (re-check infra) |
| tune-mjcf | 100,150,175,200,250,268 | c,d | speed threshold not met/measured | S8 |
| video-processing | - | a,d | heuristic frame picking by guess ("second-guessing… proceed") | S5, S4 |
| winning-avg-corewars | 150,250,268 | a | domain (warrior design) | (low priority) |
| write-compressor | - | b,a | 4/5 context; reverse-engineering decoder | S7 |

Counts: d (process) is a primary cause in ~22 tasks; c/e time-or-infra in ~9 (re-check after the eval fix);
f ~7 (do not drill now). 14 of the 43 were solved at some other eval step, so they are reachable.

## Trainable skills (describe the skill, not the benchmark task — do not copy TB-2.1 tasks)

**S1. Decode the evidence, never invent it.** Failure: missing data reconstructed from a "plausible pattern"
(WAL rows), guessed values from raw bytes without parsing the format (SQLite pages, ELF headers).
Drill: environments with a corrupted/encoded artifact in a documented binary format (journal/WAL-style logs,
page-structured DBs, object files, custom container formats) whose correct content is randomly generated per
task, so any pattern guess fails. Put a tempting false pattern in the visible data. Tests compare exact
recovered values. Instruction states the format exists, not how to parse it.

**S2. Adversarial self-testing before submit.** Failure: ✓-checklists of requirements with no test that
tries to break the solution (sanitizers, bypasses, regexes, primers, queries). Drill: tasks with 1-2 given
examples and hidden tests full of edge/adversarial inputs (encodings, nesting, boundary dates, empty cases);
environment ships the real checker tool (e.g. a headless browser, the reference library) so the student can
verify behaviour, not just reason about it.

**S3. Test in the real execution mode.** Failure: distributed layers "verified" at world_size=1 only.
Drill: implement a component that must be correct under N spawned processes (CPU/gloo, torchrun -nproc 2-4)
or after a service restart / new shell; tests run it that way; single-process tests pass trivially.

**S4. Domain sanity and units.** Failure: accepted physically implausible fits (peak far from its known
band after a wrong axis scaling), primer designs that satisfy local numbers but not the biology.
Drill: analysis/fitting tasks with a unit/scale trap in the input; tests require values in the physically
correct range; instruction names the quantity but not its expected value.

**S5. Change modality to see the answer.** Failure: grepped strings from G-code / picked frames by guess
instead of rendering the toolpath, plotting, or reading the image with a tool. Drill: data whose answer only
appears after rendering (toolpaths, vector paths, frame sequences, board images) plus a decoy text string in
metadata; rendering tools available in the image.

**S6. Drive interactive programs from tmux.** Failure: stuck inside a REPL/admin shell, repeated C-d,
terminal exited. Drill: configure a service whose setup uses an interactive CLI; tests check the service
state; reward depends on using non-interactive flags/here-strings/timeouts and leaving the shell usable.

**S7. Long-horizon work under a context budget.** Failure: context limit on multi-stage builds/ports;
whole-file heredoc rewrites, no incremental progress. Drill: multi-stage build/port tasks that need several
dependent steps; tight context; tests per stage (partial-credit-free, but stages make progress observable).
Pairs with the echo-collapse fix (harness) — the habit to train is editing in place and keeping notes.

**S8. Meet performance thresholds with margin.** Failure: submits when its own run is just above the bar
or never measures. Drill: optimization tasks with a hidden threshold checked under load/noise (tests run
several times); the student must benchmark like the test does and leave margin.

**S9. Spec-constraint fidelity.** Failure: changed text that must remain identical, missed secrets in
history, wrong placeholder format, workflow constraints beyond the round-trip. Drill: tasks with explicit
invariants (protected regions, exact replacement tokens, all occurrences incl. history) checked by tests.

**S10. Reason from the grammar, not blind retries.** Failure: polyglot/language-trick tasks burn all turns
on random syntax variants. Drill: tasks needing one key insight about two parsers/toolchains, with man pages
/specs in the image; reward a short path (turn cap tighter than usual).

## Do not drill now
- Likely beyond 9B: gpt2-codegolf, regex-chess, make-doom-for-mips, circuit-fibsqrt, feal-linear,
  install-windows-3.11 (GUI).
- Re-check after the eval infra fix (32 gens, mem 0.9) before blaming the model: protein-assembly,
  train-fasttext, sam-cell-seg, mteb-leaderboard, extract-moves-from-video, adaptive-rejection-sampler
  (time budget / verifier 900 s timeouts).
