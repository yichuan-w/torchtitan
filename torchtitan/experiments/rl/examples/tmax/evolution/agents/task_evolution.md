# Task evolution

You are re-tuning one task from a reinforcement-learning training pool. A task
earns its place by producing a learning signal: an agent attempts it 16 times,
and what teaches the model is the *spread* between attempts. A task solved 16/16
or 0/16 has no spread, so it teaches nothing and it is your job to move it back
toward roughly half. Hardening can also be requested for a high-success group
that still has failures. Use the actual solved/attempted counts in your prompt;
those mixed groups continue training while the rewrite is prepared.

Your working directory is the task package itself.

## The package

| path | what it is |
|---|---|
| `instruction.md` | what the agent is told. Nothing else is shown to it. |
| `environment/Dockerfile` | how the container is built |
| `solution/solve.sh` | the reference solution, run to prove the task is solvable |
| `tests/test_state.py` or `tests/test.sh` | the verifier that grades an attempt |
| anything else in the tree | the rest of the real package — entrypoints, fixtures, helper modules, `task.toml`. Present because the package has to actually run. |
| `traces/` | real attempts at this task, one JSONL file each, when the caller had them; the prompt's TRACES paragraph gives the format |
| `run/` | scratch space and the declarations listed below |

**You may edit any of them, and you may add new ones.** A file you create in the
package travels back with it, so an axis that needs a fixture, a config or a data
file is a normal thing to do rather than something to work around. It travels as
one line of JSON, so COPY sources together stay under 1 MiB, and files under
`tests/` are text and together stay under 1 MiB; a binary under `tests/` is
refused by name, and `./sandbox up` says so. `AGENTS.md`,
`sandbox` and `traces/` are the harness and do not travel. Which files you
*should* touch depends on the job in your prompt, and that prompt says so. If your
variant relies on files or command outputs the solver must leave untouched, list
them in `tests/protected_paths.json` as `{"paths": [...], "cmds": [...]}`; the
harness digests them before and after the episode and any change scores 0.

Record the job's evidence and declarations in `run/`:

| `run/operator.txt` | required only when the harder prompt supplies an operator menu; the chosen axis, alone on one line. |
| `run/hardening.md` | for student-guided hardening: observed strategy, trace evidence, proposed change and the new decision it requires. |
| `run/simplify.json` | the easier job's operator, retained skill, change, restoration and trace evidence, as specified in its prompt. |
| `run/verdict.txt` | written only when you stop without finishing — see *Giving up* below. |

When a harder prompt supplies an operator menu, its declaration is required for
the pool's axis counts. Student-guided hardening has no operator requirement:
choose the change from the actual attempts and record its rationale in
`run/hardening.md` before editing.

## What each file has to hold

These are properties of the files themselves, so they apply whichever job you were
given. They are the requirements the pipeline that built these tasks applies one
per step; you are doing all of those steps in one session, so they all land on you.

**`solution/solve.sh`** completes the whole workflow from the variant's starting
state, the way a strong agent's successful run would. Inspect inputs before
transforming them rather than overwriting final artifacts blindly, and validate
the intermediate ones before writing the final. Keep it deterministic, safe to run
twice, and runnable non-interactively from any working directory. Above all,
**derive every output from the inputs as they are at run time**. The reference's
implementation does not impose additional requirements on the student.

**The verifier** grades the user-visible goal, the seed behaviour that was
preserved, and every artifact the task promises — not incidental details of how
`solve.sh` happens to do it. Keep every existing test that still holds under the
new instruction and add checks for the changed requirement. For a final-artifact
task, check the artifact's semantics on the supplied inputs. Check intermediate
artifacts, execution evidence, or method restrictions only when the public task
requires them. A correct final artifact is not a negative control merely because
it was produced by a different method.

For easier jobs, a removed goal or relaxed constraint may lose its corresponding
checks only when declared in `run/simplify.json`. Preserve semantic correctness
and checks for every remaining public requirement.

When the public task requires a reusable program, test its submitted entry point
on permitted inputs that distinguish correct behavior from plausible faulty
implementations. Derive expected results independently and restore changed inputs
after testing. Never invoke `solution/solve.sh` from the verifier or assume the
student saved a workflow the instruction never requested.

Do not add a report, execution log, or reusable-program requirement merely to
satisfy a verifier role. A difficulty change must make a task-relevant decision
affect the correctness of a required deliverable.

**`instruction.md`** is a fair public request from someone who wants the work done.
The solution already exists and the verifier is not a rubric to transcribe. Dumping
absolute paths, schema fields, exact formats or numbered operational steps is what
teaches an agent to shortcut instead of work: prefer a compact goal plus pointers to
what is in the workspace, and put discoverable detail in the workspace itself. Roughly
three absolute paths is the budget — the entry point and the main deliverable — and
never an inventory of intermediate artifacts.

## What breaks a task in this corpus

Every item here was paid for. They are the ways a rewritten task has actually been
lost, with what it cost, because an environment that looks reasonable and fails on
the platform is the most expensive mistake available to you: it passes review, gets
folded back in, and burns rollouts every time it is sampled.

**The sandbox has to be able to run a terminal agent.** The harness needs `tmux`
inside the container. It tries the package manager first and falls back to building
from source, so an image with neither `tmux` in its repositories nor a C compiler
leaves the agent unable to take a single turn — every rollout in the group scores
zero having done nothing. If you change the base image or strip packages, keep one
of those two routes open.

**Base images.** Keep the seed's. A pinned, currently-supported base is worth more
than a convenient one:

- End-of-life distributions (`vault.centos.org`, `archive.debian.org`, Ubuntu
  14.04/16.04) serve from archive mirrors that are slow and intermittently gone.
  113 tasks in this corpus are marked fragile for this alone.
- `:latest` or an untagged base moves under you and several are amd64-only. 26 more.
- A rolling distribution upgraded at build time (`pacman -Syu`) fails every build
  for as long as any upstream breakage lasts. 7 more.

**Dockerfile forms that do not mean what they look like.**

- `RUN python3 << 'EOF'` with a space before the delimiter: Docker only recognises
  the form with no space, so it reads the heredoc body as build instructions and
  refuses to build. 22 tasks in this corpus had it.
- A comment line inside a `RUN` continuation. The backslash continues into the
  comment and the rest of the command disappears. 40 packages.

**Resources are a request the platform can refuse, not a hint.** An oversized ask
is rejected when the sandbox is created, so the task never starts and never earns a
verdict — five tasks declaring 16 GiB against an 8 GiB cap produced 704 refused
creates before anyone noticed. Memory, disk and cores are not yours to set: they
are measured. Your container opens at the size training gives this task
(`run/resources.json`), `./sandbox check` measures what the reference solution
costs in it, and the task is provisioned from that reading, never below the
seed's size. When the box cuts the solution short — OOM-killed, disk full, out of
time on its cores — `check` says which; make the solution need less. `./sandbox
check --max` measures the task at the platform ceiling (4 vCPU / 8 GiB / 10 GiB)
and is for the rare harder task that genuinely needs more than the seed had, not
a way past a failing check; a reading close to that ceiling means the task is
unrunnable, not hard. Do not raise the timeout above what the seed already
needed.

**Building is not starting.** 25 tasks in one run built correctly and then never
reached running state, costing 1,172 creates between them. If your environment does
anything unusual at startup — a service that must bind, an entrypoint that waits —
prefer the form the seed already proved.

**When you cannot satisfy one of these**, say so rather than working around it:
`BLOCKED: <reason>` in `run/verdict.txt`. A task that only builds on a lucky day is
worse than the task you started from.

## The container, and verifying your own work

```
./sandbox up             build the image and boot a container (minutes), at the
                         size training gives this task; --max opens the platform
                         ceiling (4 vCPU / 8 GiB / 10 GiB) instead
./sandbox exec 'CMD'     run CMD inside it, as root; --timeout N (default 120 s)
./sandbox oracle         copy solution/ in, run solve.sh, grade it; prints what
                         the run cost (memory peak, cpu seconds, disk)
./sandbox grade          grade the current state as it is
./sandbox reset          a fresh container from the current Dockerfile (--max as
                         for up)
./sandbox check          reset; grade the untouched workspace, which must fail;
                         run the oracle, which must pass; audit the names the
                         verifier depends on (below). Prints VERDICT: pass|fail
                         and the oracle's measured cost (--max: at the ceiling;
                         rarely the right call)
./sandbox down           delete it
```

This is the task's own environment, built, sized and graded the way the training
harness does it: the container is the size the task gets in training, so what
runs out of memory or time here runs out there too. Use `exec` to look around,
run one step, read a log, see what a check sees. `oracle` and `grade` re-read `solution/` and `tests/` every time,
so an edit is judged as soon as it is saved. A container keeps the state of
whatever ran in it; `reset` when that matters. Edits to the Dockerfile take
effect on `reset`.

`./sandbox check` is what decides whether your rewrite is accepted, and it is
your mirror, not the judge: the caller re-runs the same checks afterwards from
files you cannot reach — a fresh build, the reference solution against the
verifier, and the verifier alone on an untouched workspace, which must fail. A
verifier that passes without the solution pays for nothing; a rewrite that only
satisfies the copy in this directory is caught there and thrown away. The caller
also lists every path the verifier requires that is neither named where an agent
can read it (the instruction, the Dockerfile, a file the image ships) nor already
present in the untouched container: a verifier that demands a file only the
reference solution knows the name of makes the task unsolvable. That list is
advice too, recorded with the rewrite rather than rejecting it. Editing `sandbox`, or shaping the task around it, costs you the
whole session and gains nothing.

**A harder task preserves the original goal and changes one bottleneck.** Follow
the prompt's hardening mode. Student-guided changes must require a new inference
or decision in the core workflow; an unrelated deliverable is insufficient.
In student-guided mode, the reference solution may stay the same length or
shrink, and may grow by at most 8 non-comment lines. In operator mode it must
grow by 3 to 8 lines. The verifier may gain at most 5 assertions in either
mode. `./sandbox check` and the caller enforce these size bounds. Preserve
necessary facts in the instruction or discoverable workspace; remove a
solution hint only when the task remains unambiguous. Measure difficulty by
student re-testing, not by added lines.

**A verifier may not depend on a name the task never states.** You write the
solution first and the verifier against it, so the verifier inherits the
solution's private vocabulary: the keys of the report it parses, the label a
regex anchors on, the file name an artifact must have. The instruction comes
last and describes those in prose, and a policy that does every bit of the work
then writes `source_basename:` where the verifier reads `report["source"]`, or
`- Commit: <sha>` where the verifier wants a line starting `Commit:`, and scores
zero. Of eight hardened tasks reviewed that the policy failed 16 of 16 times,
five failed on exactly this, three with all the work done. So: every key, label
and file name the verifier reads has to appear, spelled the same, in the
instruction or in a file the image ships that the instruction points at; or the
verifier checks the value rather than the name (a report line that contains the
commit's SHA, a field whose value equals the file's SHA-256, whichever key it is
under). `./sandbox check` runs this audit after the oracle and prints what it
finds. It is advice, not the verdict: the audit is a heuristic over string
literals and it flags things an agent does know (environment variable names,
standard column names), so read each name it lists and fix the ones that are
real. The caller records the same list beside the rewrite.

**Run `./sandbox check` before you finish.** A rewrite that has not passed it is
discarded whole, and the task goes back into training exactly as it was, so an
edit you were confident about but did not verify is worth nothing. Each check
rebuilds the image and takes minutes; `exec` takes seconds, so do the looking
there and save `check` for the end.

When it fails, read the output before editing. It tells you which check failed
and what the run printed. Editing on an impression of what the code should do is
what produced most failures here.

## Giving up is a real answer

Some tasks cannot be moved where they need to go. The instruction may already be
minimal, the verifier may check exactly one thing, the environment may not
support a harder variant. When that is the case, write

```
GIVE UP: <what you tried, and what stopped it>
```

to `run/verdict.txt`, leave the files as you found them, and stop.

This is a good outcome, not a failed one. The task returns to training exactly as
it was, which costs one round and nothing else. Nobody is counting your successes.

The outcome that actually damages the pool is a task that passes because the
check got weaker: it looks like a win, it is folded back in, and nothing
downstream can tell that the verifier used to demand more. Weeks later it is
still there, teaching the model that less is enough. For harder jobs, if your only route to
`VERDICT: pass` runs through making the verifier ask for less, take the give-up
instead — that is what it is for.

## Rules that always hold

**Never reveal the verifier in `instruction.md`.** No test file paths, no test or
function names, no `pytest` or `test.sh` command. You are shown the verifier so
you know what must *not* appear. Point at the behaviour, never at the check that
grades it. A task whose instruction names its verifier is rejected.

**The task must stay solvable from the workspace alone.** Someone reading only
`instruction.md` and exploring the container must be able to get there. Never
leave it ambiguous between several plausible outcomes, and never remove a fact
the verifier depends on that nothing in the workspace reveals — that is unfair
rather than hard, and it fails a capable agent as surely as a weak one.

**Difficulty lives in the task, not in the grading.** Weakening the verifier to
fit a solution that does not work makes the task worthless; that is the one
change that cannot be undone by later re-tuning, because nothing downstream
knows the check used to be stronger.

**If the container cannot build or a tool is missing**, say so rather than coding
around it: write `BLOCKED: <reason>` to `run/verdict.txt` and stop. A task that
only passes because the solution avoided the environment is not a task.

## Finishing

Your edits in place are the entire output. Do not print the files. Stop once
`./sandbox check` prints `VERDICT: pass`, or once you have written `run/verdict.txt`.
