# Hardening rationale

## Observed successful strategy

All 12 attempts earned reward and performed the intended remote rewrite rather than editing the grader. The dominant path was to identify the `secret.key`-adding commit at `HEAD`, run `git reset --hard` to its parent, repair the stale Makefile reference, and force-push. Examples are `traces/attempt-01.jsonl` turns 2-8 and `traces/attempt-03.jsonl` turns 6-19. Attempts 02, 08, and 09 instead used interactive rebase, but the three-commit linear history spared every attempt the real decision of preserving descendants of the bad commit. No rewarded attempt edited tests or grading files. The instruction's final sentence refers to four contract JSON files that are absent in the untouched image; that setup defect will be repaired by deleting the false pointer and recording it in `run/repair.json`.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/git/git/v2.47.3/Documentation/git-rebase.txt — the `--onto` example states that rebasing the range after flawed middle commits removes those commits while replaying later commits, producing `E---H'---I'---J'` after removing `F` and `G`.
- https://raw.githubusercontent.com/git/git/v2.47.3/Documentation/git-reset.txt — the `--hard` mode resets the branch, index, and working tree to the selected commit; therefore using the bad commit's parent as the target also moves the branch behind every later commit.
- https://api.github.com/repos/git/git/git/ref/tags/v2.47.3 and https://api.github.com/repos/git/git/git/git/tags/ccff137f339698d014904044fb2cfa0c697b03dc — these resolve signed tag `v2.47.3` to Git commit `a52a24e03c8c711f1d5e252fba78f9276908129b`.
- https://www.kernel.org/pub/software/scm/git/sha256sums.asc — the upstream checksum list gives `9c2eb1250781b3e5bfef098572d07fdf132d67e6c065e4307332ade9819a1501` for `git-2.47.3.tar.xz`.

The Dockerfile will fetch exactly `https://www.kernel.org/pub/software/scm/git/git-2.47.3.tar.xz`, verify that SHA-256, build it, and install it as the active Git. The repository will gain one ordinary release-documentation commit after the `secret.key` commit.

## Reproduction before editing

In the running seed container I built the pinned Git tarball and confirmed `git version 2.47.3`. I copied the bare fixture, added a post-incident `document version 1.0 release` commit, cloned it, and ran the reset used by the dominant successful strategy. The observed output was:

```
BEFORE_RESET
Initial commit
hello, world!
add secret.key!
document version 1.0 release
AFTER_RESET
Initial commit
hello, world!
RELEASE_FILE=missing
```

This is Git's documented behavior in this environment: it succeeds silently but produces the wrong result by dropping valid later work.

## Proposed change and new decision

Move the bad commit into the middle of the branch by adding a later release-documentation commit, and require unrelated commits to remain in the corrected remote history. The student must choose a range/history rewrite that removes the selected middle commit while replaying its descendants, rather than moving the branch to the bad commit's parent. This is one modest step beyond the demonstrated skill: the attempts already inspect logs, identify commits, amend content, use reset/rebase, and force-push; the new work is selecting the correct Git history transformation and validating that later work survived.

No prior simplification help exists to withdraw. The original goal and wording remain, apart from repairing the pointer to nonexistent contract files and adding the preservation requirement.

## Plausible first fixes and replay results

- Reset the branch to the bad commit's parent, repair the stale reference, and force-push: reward 0. The Makefile check passed, but the result-listing and remote-history checks failed because `RELEASE.md` and its later commit were dropped.
- Revert the bad commit, repair the stale reference, and push: reward 0. The result-listing and Makefile checks passed, but the remote-history check failed because both the introducing commit and its revert remained in history.

## Attempt replays and prediction

I replayed the successful reset-based paths of attempts 01 and 03 from separate fresh resets, including their fresh-clone listing. Each now stops after grading with reward 0. In both replays, the Makefile check passes; the result-listing check fails because `RELEASE.md` is absent, and the remote-history check fails because the later release file and commit are absent. The secret-removal portions of the history check pass, so the new preservation decision is specifically what separates the old path from a correct path.

I expect **6 of 12** in the next same-sized group. Nine of twelve measured attempts used a reset-based route that now silently loses the descendant and should fail unless they revisit it. Three already reached for interactive rebase; those have the right mechanism, while some reset users are likely to notice the explicit preservation sentence or validate the resulting log and switch approaches.
