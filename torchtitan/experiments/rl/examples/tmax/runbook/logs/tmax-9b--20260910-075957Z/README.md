# TW + SWE-Smith epoch solve log

- Run: `tmax-9b--20260910-075957Z`
- Host path: `/data/yichuan_wang/tmax-9b-centralia-mix-v2/runs/tmax-9b--20260910-075957Z`
- Mix: TerminalWorld 668 + SWE-Smith 1243 (1847 train / 64 holdout)
- Group size 12, 150 trainer steps
- Source: `trainer/training_lineage/events.jsonl` `finalized` groups (`dataset_epoch` is 0-indexed)
- W&B: [79gymyc3](https://wandb.ai/yichuan_wang-uc-berkeley-electrical-engineering-computer/terminal-agent-rl/runs/79gymyc3)
- Related: [`error-reports/tmax-9b--20260910-075957Z.error-report.md`](../../error-reports/tmax-9b--20260910-075957Z.error-report.md)

`RL_OBSERVE_REWARDS` was off, so these numbers are rebuilt from lineage, not the W&B observer.

**Fully solved** = `num_solved == num_scored_rollouts` and `scored > 0`. That is usually 12/12; a few groups scored fewer rollouts because of infra (59 / 147 / 10 in epochs 1–3) but still passed every scored trial.

Epoch 3 stopped at 1751/1847 groups when the run was halted.

## Pass rate

| Epoch | Groups | Pass (solved/scored) | Any-solve | Fully solved | Exactly 12/12 | Zero |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1847 | 60.84% | 81.6% | 623 | 564 | 340 |
| 2 | 1847 | 67.93% | 84.5% | 793 | 646 | 286 |
| 3 | 1751 | 69.49% | 86.1% | 766 | 756 | 243 |

Same 1751 tasks in every epoch: 61.43% → 68.62% → 69.49%. Fully solved in all three epochs: 463. Newly fully solved in epoch 2 (not epoch 1): 245.

## Pass rate by family

| Family | E1 n | E1 pass | E2 pass | E3 pass | E1→E2 pp |
|---|---:|---:|---:|---:|---:|
| terminalworld | 647 | 68.2% | 74.9% | 76.4% | +6.7 |
| swe.pr | 269 | 57.1% | 65.8% | 67.8% | +8.7 |
| swe.func_pm | 340 | 68.0% | 75.8% | 76.1% | +7.8 |
| swe.lm_rewrite | 324 | 45.7% | 50.9% | 54.0% | +5.2 |
| swe.combine | 203 | 46.8% | 55.8% | 57.8% | +9.0 |
| swe.func_basic | 62 | 84.8% | 87.8% | 87.6% | +3.0 |

Two `lm_modify` tasks were 100% in every epoch and are omitted from the family table.

## Held-out TB 2.1 (independent of train epochs)

89 tasks × 5. Source: `trainer/validation_traces/step-*/summary.json`. Step 140 never wrote a summary.

| Step / policy | avg@5 | pass@5 |
|---:|---:|---:|
| 0 | 19.3% | 31.5% |
| 20 | 22.0% | 38.2% |
| 40 | 21.4% | 34.8% |
| 60 | 19.6% | 34.8% |
| 80 | 19.3% | 34.8% |
| 100 | 19.8% | 30.3% |
| 120 | 23.6% | 38.2% |

Train-mix pass rose every epoch; TB pass@5 did not.

## Files

- [`epoch1_fully_solved.txt`](epoch1_fully_solved.txt) — 623 ids
- [`epoch2_fully_solved.txt`](epoch2_fully_solved.txt) — 793 ids
- [`epoch3_fully_solved.txt`](epoch3_fully_solved.txt) — 766 ids
- [`epoch_fully_solved.json`](epoch_fully_solved.json) — same lists plus the summary fields above
