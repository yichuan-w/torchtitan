# TerminalWorld seeds vs TMax-15K

TW: all 1530 seed tasks. TMax: first 3000 of 14,601.

| | TerminalWorld | TMax-15K |
|---|---|---|
| ships an executable reference solution | yes, all 1530 | no such field |
| verifier checks, median | 3 | 3 |
| tasks with >= 4 checks | 46% | 27% |
| a check that rejects an unproduced answer | 3% | 9% |
| instruction length, median chars | 686 | 2090 |
| reference solution lines, median | 10 | 53 over the 30% of `truth` that parses as a script |

## What the numbers do and do not say

**TW ships a proof and TMax does not.** Every TW task carries a `solution/solve.sh` that can be run against its own verifier, which is what made it possible to establish that 861 of them are internally consistent by execution rather than by inspection. TMax has no solution column; its `truth` is sometimes solving shell and sometimes a spec for building the task, so the same check cannot be run over it.

**TW verifies more densely, TMax specifies more.** TW puts four or more checks on 46% of tasks against TMax's 27%, while TMax's instructions are several times longer. Denser grading and thinner prompts on one side, the reverse on the other.

**Neither corpus rejects shortcuts often.** Both sit in single digits on checks that would catch an answer the agent never actually produced. RST's contract puts one on every task by construction, and that is the gap either corpus has against it.

**Not measured here: difficulty.** The one number that would settle "better" is how a solver does on each, and only TW has been run (GPT-5.6-sol, pass@5: 72% of tasks solved every time). The same run against TMax needs its containers built, which its `container_def` column should allow.
