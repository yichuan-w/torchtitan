# torchtitan, as used for this research

This line is not a contribution path: nothing here is submitted to
pytorch/torchtitan, so upstream's lint gates and PR conventions do not bind.
What does bind is below.

## One canonical branch

`yichuan-w/torchtitan`, branch `yichuan/qwen35-port-cotrain`, is the single
canonical line for this collaboration. Feature branches are cut from it and
merge back once they have run or carry a smoke test. Keeping a long-lived
personal mainline anywhere else puts one change under two SHAs, and then
neither copy is identifiably the one that ran.

Pushing to `origin` and merging there is ours to do, without asking.

## One checkout per person, selected by profile

Two people launch from one account on della. `TRL_PROFILE` picks whose checkout
and data root a run uses; the profiles are one file per person in
`torchtitan/experiments/rl/examples/tmax/runbook/profiles/`, and ours is
`andy`. A directory named after Andy is not evidence it is ours: the account is
`al9080`, so every path on that box carries that name, including the trees
other people run from.

Write only to the tree our profile names, update it by pulling rather than
copying files into it, and do not create or adopt a checkout outside the
profiles. A checkout is not a repository, which is why pushing freely to
`origin` says nothing about writing into someone's working tree: a push leaves
a visible, revertible commit, while a write into a tree changes what is running
out of it right now and leaves no record, so its owner is the one who does it.
The workspace `CLAUDE.md` carries the rest of the reasons and the della-side
detail; the profile a script is given, not the directory it sits in, decides
which checkout runs.

## Keep the work inside experiments/

Changes belong under `torchtitan/experiments/rl/`. Core torchtitan is shared
with every other model and example in the tree, so an `if tmax:` branch in a
core file breaks llama3, qwen3, deepseek_v3 and the rest without saying so. When
a core change is genuinely needed, raise it rather than working around it
locally.

Shared harness code reaches the other examples too: `harness/sandbox/`,
`harness/agents/` and `rollout/` are used by swe_r2e as well. Check those
callsites before changing them.

## tmax: what must not be "improved"

`vanillux_loop.py` and `vanillux_prompts.py` are a byte-faithful port of the
scaffold the 9B was SFT'd under. Rewriting a prompt for clarity puts the policy
off-distribution and starves the solve rate. Leave them alone.

`rollout_reward/mean`, the number on stdout, is not the learning curve. With
`drop_zero_std=True` the trained batch is filtered to mixed-outcome groups, so
that number sits near 0.5 by construction. The learning signal is
`rollout_reward/avg_train_reward`.

Per-sandbox Daytona resources come from the data row. `TT_DAYTONA_CPU` and its
two siblings apply only to rows that declare nothing themselves, so measure the
mix before quoting any fleet number.

## Where the rest is

`experiments/rl/examples/tmax/README.md` covers what the system is and how a
rollout works. `tmax/runbook/RUNBOOK.md` is the from-zero reproduction guide,
and carries the environment variable reference.
