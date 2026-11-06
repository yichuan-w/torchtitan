# Hardening rationale

## Attempt evidence

All 12 measured attempts passed. Every attempt inspected the package metadata and `commands.py`, then either appended a static `[project.scripts]` table or replaced `pyproject.toml` with one. For example, `traces/attempt-02.jsonl` turn 6 appends five literal mappings and turn 8 installs; `traces/attempt-12.jsonl` first tries installation unchanged (turn 6), then appends the same literal mappings (turn 7) and reinstalls (turn 9). This restores today's five launchers but bypasses `setup.py`'s existing command discovery, so adding another `main_*` function requires another metadata edit. No observed attempt reasoned about PEP 621 dynamic scripts.

The successful attempts earned their reward under the prior verifier: they installed the named commands and exercised them; none edited grading files or skipped the stated installation goal. However, accepting literal mappings does not preserve the instruction's claim that the commands are generated dynamically from `commands.py`. I will repair that underchecked requirement by making continued discovery explicit and checking installation again after adding a valid `main_*` command to a fresh copy of the source. This repair is recorded separately in `run/repair.json`.

## Primary sources and reproduced behavior

- https://packaging.python.org/en/latest/specifications/pyproject-toml/#dynamic — the specification says: “If the metadata does not list a key in `dynamic`, then a build back-end CANNOT fill in the requisite metadata on behalf of the user.”
- https://setuptools.pypa.io/en/stable/userguide/pyproject_config.html#dynamic-metadata — setuptools documents that `scripts` can be listed as dynamic; it also documents that dynamic `scripts` are resolved from the `console_scripts` group supplied by the distribution configuration.
- https://raw.githubusercontent.com/pypa/setuptools/v80.9.0/setuptools/config/_apply_pyprojecttoml.py — the pinned implementation maps `scripts` to the prior `console_scripts` value and resets it when the field is not dynamic.
- https://pypi.org/pypi/setuptools/80.9.0/json — the selected upstream artifact is setuptools 80.9.0; its sdist SHA-256 is `f36b47402ecde768dbfafc46e8e4207b4360c654f1f3bb84475f0a28628fb19c`.

Pinned artifact: `setuptools==80.9.0` in the image's build virtual environment and isolated build requirements.

Before editing, I reproduced the failure in the sandbox with:

```
cd /app/datatools
python -m build --wheel --outdir /tmp/base-wheel
pip uninstall -y datatools || true
pip install /app/datatools
```

Inspecting the wheel showed `entry_points files: []`; shell lookup reported `csv2json`, `json2csv`, `validate`, `stats`, and `datatools` all `MISSING`. The build succeeded and included `datatools/commands.py`, making this a silent metadata loss rather than an announced fix.

## Changed decision

The student must preserve setuptools' runtime command discovery while completing the PEP 621 migration, rather than copying the currently visible functions into static metadata. This is one modest step beyond the measured strategy: the same files, installation, and launcher validation remain, but the solver must connect `setup.py`'s generated `console_scripts` to PEP 621's dynamic metadata contract. The build's success does not dictate that choice, and a static table looks correct until discovery is tested with another command.

No earlier simplification help is being withdrawn; the history contains only the seed revision.

## Plausible first fixes and grades

- Static declarations, the measured student's first fix: on a fresh reset I appended the five `[project.scripts]` mappings from `traces/attempt-02.jsonl`, installed, and graded. Grade: `0.0`. All original command checks passed, while the fresh-copy dynamic-discovery check failed because `probe` was not installed.
- Upgrade/reinstall, the fallback used in `traces/attempt-12.jsonl`: on a fresh reset I installed the unchanged package, force-reinstalled the newest available setuptools and wheel into the environment, then reinstalled the package. Grade: `0.0`. The original launchers remained absent and the dynamic-discovery check failed as well. This fix does not alter isolated build requirements, which are pinned in the package.

The intended change, declaring scripts dynamic while retaining `setup.py` discovery, graded `1.0` and also installed a newly added `main_probe` function as the `probe` launcher from a fresh package copy.

## Replay and prediction

I replayed the substantive commands of `traces/attempt-02.jsonl` from a fresh reset: inspect the metadata/source, append its literal scripts table, install, and invoke the launchers. The revised grade was `0.0`; it now stops at the added dynamic-discovery check, with the original nine checks passing and `probe: not found` in the failing check.

I separately replayed `traces/attempt-12.jsonl` from a fresh reset: inspect, try the unchanged installation, append the literal scripts table, uninstall/reinstall, and invoke the launchers. Its revised grade was also `0.0`; it fails only the dynamic-discovery check for the same missing `probe` launcher.

Expected next result: **6 of 12**. The exact static-table strategy used by all measured attempts now fails, but several attempts explicitly inspected `setup.py` and the existing `dynamic = ["dependencies"]` declaration, so a capable subset should infer the analogous PEP 621 opt-in within the available debug loop.
