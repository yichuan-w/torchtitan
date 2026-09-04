# Task evolution

You are re-tuning one task from a reinforcement-learning training pool. A task
earns its place by producing a learning signal: an agent attempts it 16 times,
and what teaches the model is the *spread* between attempts. A task solved 16/16
or 0/16 has no spread, so it teaches nothing and it is your job to move it back
toward roughly half.

Your working directory is the task package itself.

## The package

| path | what it is |
|---|---|
| `instruction.md` | what the agent is told. Nothing else is shown to it. |
| `environment/Dockerfile` | how the container is built |
| `solution/solve.sh` | the reference solution, run to prove the task is solvable |
| `tests/test_state.py` or `tests/test.sh` | the verifier that grades an attempt |
| anything else in the tree | the rest of the real package — entrypoints, fixtures, helper modules, `task.toml`. Present because the package has to actually run. |
| `traces/` | transcripts of real attempts, when the caller had them |
| `run/` | scratch space for you, and where you write the two files below |

**You may edit any of them, and you may add new ones.** A file you create in the
package travels back with it, so an axis that needs a fixture, a config or a data
file is a normal thing to do rather than something to work around. `AGENTS.md`,
`sandbox` and `traces/` are the harness and do not travel. Which files you
*should* touch depends on the job in your prompt, and that prompt says so.

Two files in `run/` are read by the caller rather than by you:

| `run/operator.txt` | the id of the axis you chose, alone on one line. Only the harder job offers a choice; write it before you start editing, so the record survives a session that later times out. |
| `run/verdict.txt` | written only when you stop without finishing — see *Giving up* below. |

The caller rebuilds the pool's axis balance by reading `run/operator.txt` off
every task that comes back. A task folded in without it is invisible to that
count, so a session that finishes the work but never declares the axis is
discarded rather than kept.

## What each file has to hold

These are properties of the files themselves, so they apply whichever job you were
given. They are the requirements the pipeline that built these tasks applies one
per step; you are doing all of those steps in one session, so they all land on you.

**`solution/solve.sh`** completes the whole workflow from the original starting
state, the way a strong agent's successful run would. Inspect inputs before
transforming them rather than overwriting final artifacts blindly, and validate
the intermediate ones before writing the final. Keep it deterministic, safe to run
twice, and runnable non-interactively from any working directory. Above all,
**derive every output from the inputs as they are at run time** — this single
property decides whether the reference passes its own verifier, because a
`no_shortcut` check that perturbs an input and re-runs will catch an answer that
was written once and never recomputed.

**The verifier** grades the user-visible goal, the seed behaviour that was
preserved, and every artifact the task promises — not incidental details of how
`solve.sh` happens to do it. Four roles have to be covered. They are roles, not
a count: keep every existing test function that still holds under the new axis
and add what the axis needs, so the verifier never checks less than the seed's
did. The roles:

- `required_evidence` — the agent had to find something, not guess it;
- `intermediate_artifact` — it produced the middle of the workflow, not only the end;
- `final_semantics` — the end state means what it should, checked by content;
- `no_shortcut` — an answer that was copied, hardcoded, or written for the verifier
  is caught.

`no_shortcut` is the one usually missing and it has to be earned in behaviour: change
an input the answer depends on and re-run the workflow, asserting the output followed
and restoring what you changed; or recompute the expected answer inside the verifier
from the current inputs; or require an intermediate artifact whose content must agree
with the final one. Asserting a file is non-empty, or lacks the word "placeholder", is
not this check. Never invoke `solution/solve.sh` from the verifier — it is not there
when the agent runs. Invoke the workflow the way the instruction tells a user to.

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
also checks that every path the verifier requires is either named where an agent
can read it (the instruction, the Dockerfile, a file the image ships) or already
exists in the untouched container: a verifier that demands a file only the
reference solution knows the name of makes the task unsolvable, and the caller
sends it back. Editing `sandbox`, or shaping the task around it, costs you the
whole session and gains nothing.

**A harder task is one rung above the seed, and the rung is measured.** Keep
everything the seed asks for and add one requirement. The reference solution
may grow by 3 to 8 non-comment lines over the seed's and may not pass 20; the
verifier may gain at most 5 assertions. `./sandbox check` fails outside that and
the caller rejects the rewrite. The numbers come from this corpus: seeds of 14
to 20 solution lines are where the training signal is mixed most often, past 20
the 0/16 share doubles, and the rewrites that grew to 125 lines came back 0/16
five times in six. Size is not difficulty, but a rewrite outside that band has
left the region where the policy can be taught.

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
under). `./sandbox check` runs this audit after the oracle and fails the check on
what it finds; the caller runs the same audit and sends the rewrite back.

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
still there, teaching the model that less is enough. If your only route to
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
