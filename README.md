# 114 evolve exports for the data side

- `repairs_114.jsonl`: every defect the 114 rewriter declared while rewriting (one line per fix; `kind` is `hidden_requirement`, `broken_setup`, `reward_hack` or `other`), plus a `source` column. Snapshot taken 2026-11-06.
- `rewards_fixed/`: the rewrites whose declared defects include `reward_hack`. Each task dir holds `before_r<k>/` (the revision the rewrite started from) and `after_r<k>/` (the accepted result): `instruction.md`, `task.toml`, `tests/`, `solution/`, and environment files up to 256 KiB (larger ones are listed in `environment/LARGE_FILES.txt`). `hardening.md` and `repair.json` are the rewriter's notes. `index.json` lists them; `task_003888_e6eb8949` was rejected (image USER changed) and has no package.
- `c_tier_evidence.md`: rollout files and turns behind the four "suspicious" tasks.

The security task's staged fake API key is replaced by `sk_live_<FIXTURE-KEY-REDACTED>` here so the push is not blocked; the original value is in the dataset rows.

## Second batch (2026-11-06): hidden_requirement / broken_setup / other

Run 114 was stopped at about step 90 (2026-11-06 ~08:50Z), so this is its final `repairs.jsonl` (167 rows).

- `rewards_fixed/` now also holds every accepted rewrite that declared a `hidden_requirement`, `broken_setup` or `other` defect: 151 defects, 125 rewrites, 124 tasks. Same layout as above. The rewriter's notes are in `notes_<rewrite id>/` (`repair.json`, plus `simplify.json` for easier rewrites or `hardening.md` for harder ones).
- `index.json` has one entry per declared defect with `kind`, `direction`, `rewrite`, `before_rev`, `after_rev`, `defect`, `evidence`, `change`, `changed`. `before_rev` is the revision the defect was found on (the rewrite's input revision).
- `after_r<k>/` is the whole accepted rewrite. An easier or harder rewrite can also change difficulty in the same revision, so `after` is not only the repair. `changed` and the notes say which files the rewriter touched.
- `task_006512_ac735565` (broken_setup) was rejected (image USER changed) and has no package.
- `task_003078_2ecd85ff` also has a broken_setup entry. It is the same rewrite as its reward_hack package.
