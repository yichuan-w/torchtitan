# TB 2.1 TF-IDF neighbors

For each Terminal-Bench 2.1 task, rank the pooled training corpora by
TF-IDF cosine on the **instruction / problem statement** (1–2 grams,
fit on the training side only).

## Training ids

The five corpora are pooled into **56,307** rows. The id in the CSVs is
whatever that corpus calls `task_id` (or the Harbor directory name):

| corpus | HF repo | n | id shape | example |
|---|---|---|---|---|
| TMax-15K | `allenai/TMax-15K` | 14,601 | `task_<n>_<hex>` | `task_000624_6f630314` |
| Recursive-Task-Synthesis | `Zhongzhi1228/Recursive-Task-Synthesis` | 37,484 | `rts_task_<hex>` | `rts_task_9bea742175cbc6b155920a37` |
| TerminalWorld-Seeds-Clean | `andylizf/TerminalWorld-Seeds-Clean` | 1,353 | `tw_<n>` | `tw_101115` |
| SWE-Smith-Seeds-Clean | `Fzz1/SWE-Smith-Seeds-Clean` | 1,552 | `<org>__<repo>.<sha>.<bug_family>__<suffix>` | `facebookresearch__fvcore.a491d5b9.combine_file__htnzvb1x` |
| SWE-Rebench-Tasks-Clean | `Fzz1/SWE-Rebench-Tasks-Clean` | 1,317 | `<org>__<repo>-<issue>` | `textualize__rich-3718` |

TB 2.1 queries use the Harbor task directory name (`mailman`,
`qemu-startup`, …).

Prefix is enough to tell the source: `task_` = TMax, `rts_task_` = RTS,
`tw_` = TerminalWorld, `__` + issue number = Rebench, `__` + sha +
bug family = Smith.

## Output CSVs

Same ranking, three cuts:

- **`tb21_task_ids.csv`** — one row per TB task. `top1_task_id` plus
  `top30_task_ids` joined with `|`. Use this to look up ids.
- **`tb21_top1.csv`** — one row per TB task. Top-1 id, score, short
  previews, and how many of the top 30 came from each corpus.
- **`tb21_top30.csv`** — 89 × 30 rows. One neighbor per row
  (`rank`, `score`, `source`, `task_id`, preview).

`summary.json` has corpus sizes and top-1 win counts.

These files are the **instruction** view (problem statement only). A
second “content” view (tests + assets + Dockerfile, minus Harbor
`test.sh` wrappers) can be regenerated with `run.sh`; do not treat
content-view near-neighbors as leakage — shared Harbor scaffolding
inflates TerminalWorld.

## Rerun

```bash
python compute_tb21_tfidf.py --out ./out --cache ./cache --top-k 30
```

Local Harbor trees and the two HF parquets are resolved by absolute
paths inside the script (Centralia layout). Set `HF_HOME` if you need
to re-download RTS / TMax-15K.
