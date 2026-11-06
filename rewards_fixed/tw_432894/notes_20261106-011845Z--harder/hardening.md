# Hardening rationale

## Attempt evidence

All 12 attempts use the same successful strategy: inspect `Makefile` and `pyproject.toml`, then run `make install`, `make lint`, `make build`, `make publish`, and `make force-install` without changing project configuration. For example, `traces/attempt-01.jsonl` turns 6-10 and `traces/attempt-05.jsonl` turns 4-8 execute exactly that lifecycle. Several attempts then check only distribution metadata or import the top-level package; none inspects wheel contents or launches the installed `brain-even` entry point from outside the source tree. The rewarded attempts satisfy the original lifecycle goal and do not exploit the verifier.

## Primary sources and pinned artifact

- https://raw.githubusercontent.com/python-poetry/poetry/1.8.5/docs/pyproject.md — Poetry 1.8.5 documents that a `packages` entry can be restricted with `format`, and its example says a package marked `format = "sdist"` is included only in the sdist archive. It also documents that declaring `packages` disables auto-detection, so packages must be specified explicitly.
- https://pypi.org/pypi/poetry/1.8.5/json — PyPI identifies the pinned Poetry release as 1.8.5 and reports SHA-256 `5505fba69bf2a792b5d7402d21839c853644337392b745109b86a23010cce5f3` for `poetry-1.8.5-py3-none-any.whl`.
- https://github.com/python-poetry/poetry/releases/tag/1.8.5 — the upstream release page identifies the same 1.8.5 release, published 2024-12-06.

The Dockerfile will pin the actual upstream artifact as `poetry==1.8.5`; the task project already pins its runtime dependency as `prompt = "^0.4.1"` and resolves it through Poetry's lock file.

## Reproduced behavior

With Poetry 1.8.5 in the sandbox, I changed the package declarations so the complete `brain_games` package is sdist-only while the wheel includes only `brain_games/__init__.py`, then ran the unchanged lifecycle. Every make target succeeded. `python3 -m zipfile -l dist/*.whl` showed only `brain_games/__init__.py` plus distribution metadata, while `tar tzf dist/*.tar.gz` showed `brain_games/games/brain_even.py`. After force-installing the wheel, running `brain-even` from `/tmp` failed with:

```
ModuleNotFoundError: No module named 'brain_games.games'
```

This is real Poetry package-format behavior, not an imitation. The exception names the missing import and investigation of the two archives exposes the cause, but it does not prescribe which package declaration to change.

## Proposed change and new decision

The environment will ship that format-specific package configuration. The original lifecycle requirement remains unchanged, and the installed `brain-even` command must additionally launch from outside the source checkout. A solver must now validate the installed wheel rather than infer success from five zero exit codes, compare wheel and sdist contents, and decide that the wheel package selection—not installation itself or application code—is wrong. The reference solution removes the format restriction before running the same lifecycle.

Testing the new executable requirement also exposed a seed-code incompatibility: the project imported `prompt` from the pinned `prompt==0.4.1`, whose module instead exports typed prompting functions such as `string`. This did not invalidate the old lifecycle-only rewards, but it would create a second obstacle unrelated to the chosen packaging decision. The environment repairs that existing application call separately, as recorded in `run/repair.json`.

This is one modest packaging-debugging step beyond the attempts: they already inspect `pyproject.toml`, use Poetry, build both formats, and force-install the wheel, but they never validate the installed entry point or reason about format-specific package inclusion.

## Student-like fixes and replay

I tested the two immediate recovery strategies on fresh containers:

1. Reinstall/regenerate: after the normal lifecycle, I force-reinstalled the same wheel, removed `dist`, rebuilt, and force-installed again. Grading returned reward 0. The lock, artifacts, and installed metadata checks passed; launching `brain-even` outside the checkout still failed with `ModuleNotFoundError: No module named 'brain_games.games'`.
2. Upgrade/reinstall the tool: before the normal lifecycle, I force-reinstalled the latest Poetry version compatible with the pinned environment (`poetry==1.8.5`) and rebuilt. Grading returned reward 0 with the same three passing checks and the same installed-command failure. Reinstalling the build tool cannot change an explicit format selection in project metadata.

I replayed two representative successful attempts against the revised task, each from a fresh reset:

- `attempt-01` strategy: inspect files, run all five make targets, then stop. It now reaches the same apparent zero-exit lifecycle but grading fails only the installed-command check; the lock, wheel/sdist existence, and installed distribution checks pass.
- `attempt-10` strategy: run the five make targets, list `dist`, try importing `hexlet_code`, inspect pip/user site, and import `brain_games`. The exact `hexlet_code` import remains a harmless mistaken probe as in the original trace, while its final `brain_games` import succeeds because the wheel contains the top-level package. Grading still fails only the installed-command check with the missing `brain_games.games` module.

Both paths stop because they treat distribution metadata or a top-level import as proof of a usable install and never execute the promised entry point away from the checkout. Expected next-group result: **6 of 12**. Roughly half of similarly capable agents should inspect or test the console script once it is an explicit deliverable; those repeating the measured lifecycle-only path will miss the format-specific wheel omission.
