# TB-2.1 overfit bench: results

Tasks from `tb21_tasks.txt` (the 48 the starting 9B never solved at step 0). A task counts as
overfit once the original task passes reliably: a training group on the original at half or more
of its attempts, or a readout of 4/16 or more. The 64-attempt confirm (README) is the stricter
check where it has run.

| task | overfit at | evidence | arm |
|---|---|---|---|
| sqlite-db-truncate | step 100 | readout 10/16 (pass@5 1.0); training groups on the original up to 11/12 | recipe, original share 1/2 once it passed |
| db-wal-recovery | step ~95 | training group on the original 8/12 (others 7/12, 6/12, 5/12); readout 4/16 at step 90 | recipe |

Learned what made db-wal-recovery pass: XOR the WAL with 0x42 (or copy it aside) before any
sqlite call, since opening the database while the WAL is still encrypted deletes it. Its
readouts at steps 15-95 before 2026-11-02 ran on step-10 weights (the eval-pull race fixed in
ad954878) and do not count.
