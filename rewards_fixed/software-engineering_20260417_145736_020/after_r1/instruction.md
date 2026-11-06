I recently migrated our `datatools` package from a pure `setup.py` configuration to use `pyproject.toml` with PEP 621 metadata. The package builds and installs without errors, but the CLI commands are not available after installation.

The package is in `/app/datatools`. It should provide several CLI commands (`csv2json`, `json2csv`, `validate`, `stats`, and the main `datatools` command) that are generated dynamically from the `commands.py` module.

After running `pip install /app/datatools`, the package imports work fine (`import datatools` succeeds), but running any of the commands gives a "command not found" error.

The package worked perfectly before the migration to `pyproject.toml`. I need the CLI commands to be properly installed so users can run them directly from the shell. Keep command generation dynamic: when another `main_*` function is added to `commands.py`, reinstalling the package must create its corresponding launcher without another metadata edit.
