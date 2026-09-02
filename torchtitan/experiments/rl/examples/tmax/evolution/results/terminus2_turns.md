# Terminus-2 turn counts on RST tasks (from the released trajectories)

Sample: 14735 trajectories from 3 of 71 shards; agent field: {'terminus-2': 14735}.
A turn = one agent-source step in trajectory.json (one model call).

- median 11 turns; mean 14.6
- p25 8 / p75 16 / p90 25 / p95 37; max 100
- completion tokens per trajectory: median 3129

Turn histogram (bucketed):
- 0-9: 5171 (35.1%)
- 10-19: 7012 (47.6%)
- 20-29: 1422 (9.7%)
- 30-39: 451 (3.1%)
- 40-49: 209 (1.4%)
- 50-59: 118 (0.8%)
- 60-69: 339 (2.3%)
- 70-79: 8 (0.1%)
- 80-89: 1 (0.0%)
- 90-99: 2 (0.0%)
- 100+: 2 (0.0%)
