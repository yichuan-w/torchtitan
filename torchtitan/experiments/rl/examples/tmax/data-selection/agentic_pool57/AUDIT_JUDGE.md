# Blind judge: which 10 training tasks would most help solve this benchmark task?

You get one Terminal-Bench 2.1 task and about 50 candidate training tasks, labelled C01..C50 in random order.
Nothing tells you how they were found, and you must not try to guess. Pick the 10 that, if a model were
trained on them, would most help it solve the benchmark task.

Score every candidate you seriously consider on three axes, 1-5 each (the rubric used to make the original
selection, verbatim in spirit):

- **skill**: does solving it exercise the same capability that is the crux of the benchmark task? Not shared
  words; the same thing the model has to be able to do. Weighted highest.
- **domain**: same stack and subject area?
- **task_form**: same genre (implement-from-spec, fix-a-failing-test, recover-corrupted-data,
  configure-a-service, reverse-engineer, build/packaging, ...) and comparable depth?

`overall` is a judgement, not an average: skill first, then domain, then task_form. Lexical overlap on
boilerplate ("write to /app/out.txt", "run the tests") is worthless. A candidate whose `verifier` is `FREE`
must not be in your top 10. If a candidate is the same task as the benchmark task (same deliverable and checks,
reworded), do not put it in your top 10 and list it under `near_copies`.

Read every candidate's instruction before deciding. Write ONLY a JSON file at the path you are given:

{"query_id": "...", "top10": [{"rank": 1, "label": "C17", "skill": 5, "domain": 4, "task_form": 5,
  "overall": 5, "reason": "<=25 words, concrete"}, ...10 entries],
 "near_copies": ["C.."], "notes": "<optional>"}
