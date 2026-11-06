In the repo at /root/git-katas/rebase-branch, bring the `uppercase` branch onto `master` with a clean, linear history (no merge commits).
Initialize the kata environment, then rebase `uppercase` onto `master` and fast-forward `master` to match the rebased tip.
After the rebase, create two files in the same directory: `changelog.md` (following the specification in `CHANGELOG_SPEC.md`) and `diff_report.md`.
The diff report must compare the final `master` tip with its pre-rebase ancestor, identified solely by inspecting the reflog (no guessing or simple relative references).
The diff report should document what changed during the rebase.
