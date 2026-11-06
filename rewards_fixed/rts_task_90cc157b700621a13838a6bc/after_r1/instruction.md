Set up a Python virtual environment at ~/python_venv/myproject and install the package from the source in ~/pkg_src.
Build a wheel from the source and place it into ~/test/myutil/.
Before starting a local PyPI server, inspect the built wheel's metadata (for example, by unpacking the `.dist-info/METADATA` inside the wheel) and write the package name and version to ~/test/myutil/wheel_metadata.txt.
Start a local PyPI server on port 8080 serving ~/test/myutil/, then use it to install the package into the virtual environment.
After installation, capture the package's version (import it and read `__version__`) and write it to /app/result.txt.
The workspace contains an outdated environment index at ~/env_index_previous.txt.
Refresh it by producing a current index at ~/env_index.txt for the virtual environment.
Also generate a rebuild report at ~/rebuild_report.txt comparing the old and current indexes.
