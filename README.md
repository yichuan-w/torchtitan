# 114 evolve exports for the data side

- `repairs_114.jsonl`: every defect the 114 rewriter declared while rewriting (one line per fix; `kind` is `hidden_requirement`, `broken_setup`, `reward_hack` or `other`), plus a `source` column. Snapshot taken 2026-11-06.
- `rewards_fixed/`: the rewrites whose declared defects include `reward_hack`. Each task dir holds `before_r<k>/` (the revision the rewrite started from) and `after_r<k>/` (the accepted result): `instruction.md`, `task.toml`, `tests/`, `solution/`, and environment files up to 256 KiB (larger ones are listed in `environment/LARGE_FILES.txt`). `hardening.md` and `repair.json` are the rewriter's notes. `index.json` lists them; `task_003888_e6eb8949` was rejected (image USER changed) and has no package.
- `c_tier_evidence.md`: rollout files and turns behind the four "suspicious" tasks.

The security task's staged fake API key is replaced by `sk_live_<FIXTURE-KEY-REDACTED>` here so the push is not blocked; the original value is in the dataset rows.
