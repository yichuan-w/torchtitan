Initialize the kata environment, then rebase the `uppercase` branch onto `master` and fast-forward `master` so both branches point to the same tip commit.
The repository is located at `/root/git-katas/rebase-branch`.
After the rebase and fast-forward, produce an audit file at `/root/audit.txt` that contains:
- A forensics header documenting the baseline commit (the state of `master` just before the rebase), the final commit, and the reflog reference used to determine the baseline.
- Followed by a unified diff showing all changes introduced by the rebased `uppercase` branch.
The repository must have a clean linear history with no merge commits, and the audit file must accurately reflect the changes brought in by the rebase.
