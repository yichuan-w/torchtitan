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

Pushing to `origin` writes into someone else's repository and needs Zhifei's
approval, given in the conversation before staging.

## One checkout per person, selected by profile

Two people launch from one account on della. `runbook/profiles/<name>.env` holds
the checkout (`TRL_TT`) and data root (`TRL_BASE`) each runs from, and
`launch_9b.sh` refuses to start without `TRL_PROFILE`.

The path never says whose checkout it is: the account is `al9080`, Zhifei's
netID, so every directory on the box has an andy-shaped name, including the
tree Yichuan runs from. Only the profile is authority.

| profile | `TRL_TT` | whose |
|---|---|---|
| `andy` | `/home/al9080/torchtitan` | ours |
| `yichuan` | `/scratch/gpfs/TRIDAO/al9080/andy-rl-tb/torchtitan` | Yichuan's |

Work in our profile's checkout. Do not update his, and do not create a third:
a running loop reads `agents/task_evolution.md`, `agent_sandbox.sh`,
`agent_sandbox.py` and `daytona_revalidate.py` from its checkout at the moment
it uses them, so updating that tree changes a run in flight, with nothing in
its log to say so.

Update a checkout with `git pull`, never with `rsync`. A tree whose HEAD names
one commit and whose files are another cannot be traced to anything, and the
next pull silently reverts the copy. To try an unmerged branch on della, push
it and check it out there; if a checkout cannot reach GitHub, give it
credentials.

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
