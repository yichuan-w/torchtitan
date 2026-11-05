# evolution/: who decides what in the rewrite loop

## Division of work

- Zhifei sets the principles below and judges whether a design meets them.
- The sessions working here, on either collaborator's side, own how the principles are met: what
  the rewriting agents are told, which prompt changes to try, settings and their values, checks and
  their thresholds. A session decides these from EvolveBench measurements without asking him, and
  says in its report what it decided.
- A choice goes to him when it would change one of the principles below, even if it looks like an
  implementation detail.
- A principle here is only what he stated explicitly. Each gives the date and the question he was
  answering, so it is read neither wider nor narrower than he ruled.

## Principles

**The loop spends no extra student rollouts to measure a rewrite** (2026-10-19). The question was
whether evolve should, like a published method, try every candidate task with the student before
admitting it: pre-publication calibration, a policy canary, a hint probe. He ruled it out. A
rewrite's difficulty is read only from the rollouts training already does on it. EvolveBench's own
evaluations measure prompts from outside the loop and are not covered by this.

**EvolveBench decides changes to the rewriting agents' prompts** (2026-11-05). The question arose
when prompt-changing settings were about to go into a live run on the strength of an offline
analysis of an earlier run. He ruled that every change to what the author, verifier or probe agents
are told, including any setting that adds or replaces a paragraph of their prompts, reaches a
training run only after it has beaten the current mainline prompt on EvolveBench, and that
EvolveBench is the only measure that decides. A check that only records, and changes no prompt, is
outside this.

**A rewriting agent's wrong behaviour is fixed at its cause in the prompt; a check is the
fallback** (2026-11-05). The question arose when a harder rewrite wrote a grader's concrete input and
expected output into the task's instruction, and an acceptance check for that pattern had been added
first. He ruled that the first step is to find why the agent did it (its notes, its session, the
prompt sentence that led there) and change what it is told; an acceptance check backs that up for
what the prompt cannot control.
