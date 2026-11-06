In the repo at /root/git-katas/rebase-branch, bring the `uppercase` branch onto `master` with a clean, linear history (no merge commits).
Initialize the kata environment, then rebase `uppercase` onto `master` and fast-forward `master` to match the rebased tip.
After the rebase, create `/root/git-katas/rebase-branch/changelog.md` (following the specification in `CHANGELOG_SPEC.md`) and `/root/git-katas/rebase-branch/diff_report.md`.
The diff report must compare the final `master` tip with the pre-rebase tip of `uppercase`, identifying that former tip solely by inspecting the `uppercase` reflog (no guessing or simple relative references).
The diff report should document what changed during the rebase and include the exact unified patch for that comparison in a fenced `diff` block.
