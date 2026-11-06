import os
import re
import glob
import subprocess
import zipfile

HOME = "/root"
VENV_PYTHON = os.path.join(HOME, "python_venv/myproject/bin/python")
RESULT_FILE = "/app/result.txt"
STALE_INDEX = os.path.join(HOME, "env_index_previous.txt")
CURRENT_INDEX = os.path.join(HOME, "env_index.txt")
REBUILD_REPORT = os.path.join(HOME, "rebuild_report.txt")
SOURCE_DIR = os.path.join(HOME, "pkg_src")
WHEEL_DIR = os.path.join(HOME, "test/myutil")
WHEEL_METADATA = os.path.join(WHEEL_DIR, "wheel_metadata.txt")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _get_wheel_info():
    """Return (wheel_path, pkg_name, pkg_version) for the single wheel in ~/test/myutil."""
    wheels = sorted(glob.glob(os.path.join(WHEEL_DIR, "*.whl")))
    if len(wheels) != 1:
        return None
    wheel_path = wheels[0]
    if os.path.getsize(wheel_path) == 0:
        return None
    try:
        with zipfile.ZipFile(wheel_path, 'r') as zf:
            metadata_names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
            if not metadata_names:
                return None
            with zf.open(metadata_names[0]) as f:
                metadata = f.read().decode("utf-8")
    except Exception:
        return None
    name_match = re.search(r"^Name:\s*(.+)$", metadata, re.MULTILINE)
    version_match = re.search(r"^Version:\s*(.+)$", metadata, re.MULTILINE)
    if not name_match or not version_match:
        return None
    return (wheel_path, name_match.group(1).strip(), version_match.group(1).strip())


def _get_venv_freeze():
    """Return a dict {package: version} from the venv pip freeze."""
    try:
        out = subprocess.check_output(
            [VENV_PYTHON, "-m", "pip", "freeze", "--all"],
            stderr=subprocess.STDOUT,
            timeout=15
        )
    except subprocess.CalledProcessError:
        return None
    lines = out.decode("utf-8").strip().splitlines()
    packages = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("-"):
            continue
        # skip comments and editables
        if line.startswith("#") or line.startswith("-e "):
            continue
        if "==" not in line:
            continue
        name, _, version = line.partition("==")
        packages[name.strip()] = version.strip()
    return packages


def _parse_stale_index(path):
    """Parse a package==version index file into a dict."""
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        lines = [line.strip() for line in f if line.strip()]
    packages = {}
    for line in lines:
        if "==" in line:
            name, _, version = line.partition("==")
            packages[name.strip()] = version.strip()
    return packages


def _normalized_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _report_mentions(report_text, package, version):
    """Whether free-form text contains a package name and version."""
    package_pattern = re.escape(package).replace(r"\_", "[-_.]").replace(r"\-", "[-_.]")
    version_pattern = re.escape(version)
    has_package = re.search(rf"(?i)\b{package_pattern}\b", report_text) is not None
    has_version = re.search(rf"(?<![A-Za-z0-9]){version_pattern}(?![A-Za-z0-9])", report_text) is not None
    return has_package and has_version


# --------------------------------------------------------------------------
# Preserved checks
# --------------------------------------------------------------------------

def test_venv_exists():
    assert os.path.isfile(VENV_PYTHON), (
        f"Virtual environment Python binary not found at {VENV_PYTHON}. "
        "Ensure the virtual environment was created at ~/python_venv/myproject."
    )


def test_server_accessible():
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request("http://localhost:8080/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200, f"Local PyPI server returned HTTP {resp.status}"
    except urllib.error.URLError as e:
        assert False, f"Local PyPI server is not accessible: {e}"


def test_package_installed_in_venv():
    site_packages_glob = os.path.join(HOME, "python_venv/myproject/lib/python*/site-packages")
    sp_dirs = glob.glob(site_packages_glob)
    assert sp_dirs, "No site-packages directory found in venv."
    installed = False
    for sp in sp_dirs:
        if not os.path.isdir(sp):
            continue
        for entry in os.listdir(sp):
            if "myutility_internal" in entry.lower() or "myutility-internal" in entry.lower():
                installed = True
                break
        if installed:
            break
    assert installed, "Package myutility_internal is not installed in the virtual environment."


def test_result_version_matches_wheel():
    info = _get_wheel_info()
    assert info is not None, "Could not find a valid wheel to check result version."
    _, _, wheel_version = info
    assert os.path.isfile(RESULT_FILE), f"Result file not found: {RESULT_FILE}"
    with open(RESULT_FILE, "r") as f:
        result_version = f.read().strip()
    assert result_version, "Result file is empty."
    assert result_version == wheel_version, (
        f"Version mismatch: wheel={wheel_version}, result.txt={result_version}"
    )


# --------------------------------------------------------------------------
# Reward checks per contract
# --------------------------------------------------------------------------

def test_reward_check_01_required_evidence():
    """Stale index exists and rebuild report references it."""
    assert os.path.isfile(STALE_INDEX), (
        f"Stale environment index not found at {STALE_INDEX}. "
        "Expected a pre-existing file containing a previous package list."
    )
    with open(STALE_INDEX, "r") as f:
        stale_lines = [line.strip() for line in f if line.strip()]
    assert stale_lines, "Stale index is empty."

    assert os.path.isfile(REBUILD_REPORT), (
        f"Rebuild report not found: {REBUILD_REPORT}"
    )
    with open(REBUILD_REPORT, "r") as f:
        report = f.read()
    assert report.strip(), "Rebuild report is empty."

    # The report must mention at least one package from the stale index.
    stale_names = set()
    for line in stale_lines:
        if "==" in line:
            stale_names.add(line.partition("==")[0])
    assert stale_names, "Stale index contains no package names."
    report_lower = report.lower()
    found = any(name.lower() in report_lower for name in stale_names)
    assert found, (
        "Rebuild report does not reference any package from the stale index."
    )


def test_reward_check_02_intermediate_artifact():
    """Current env_index.txt exists, non-empty, formatted, matches venv."""
    assert os.path.isfile(CURRENT_INDEX), (
        f"Current environment index not found: {CURRENT_INDEX}"
    )
    with open(CURRENT_INDEX, "r") as f:
        current_lines = [line.strip() for line in f if line.strip()]
    assert current_lines, "env_index.txt is empty."
    pattern = re.compile(r"^[a-zA-Z0-9_.-]+==[a-zA-Z0-9_.+-]+$")
    for i, line in enumerate(current_lines, start=1):
        assert pattern.match(line), (
            f"Line {i} in env_index.txt does not match 'package==version': {line}"
        )

    freeze = _get_venv_freeze()
    assert freeze is not None, "Could not obtain pip freeze from the virtual environment."
    assert "myutility_internal" in freeze, "myutility_internal not found in venv pip freeze."
    expected_entry = f"myutility_internal=={freeze['myutility_internal']}"
    assert expected_entry in current_lines, (
        f"Expected {expected_entry} not in env_index.txt. Found: {current_lines}"
    )


def test_reward_check_03_final_semantics():
    """Rebuild report truthfully accounts for old/current index differences."""
    assert os.path.isfile(REBUILD_REPORT), f"Rebuild report missing: {REBUILD_REPORT}"
    with open(REBUILD_REPORT, "r") as f:
        report = f.read()
    assert report.strip(), "Rebuild report is empty."

    current = _parse_stale_index(CURRENT_INDEX)
    stale = _parse_stale_index(STALE_INDEX)
    assert current is not None and stale is not None, "Could not parse old and current indexes."
    current_by_name = {_normalized_name(pkg): (pkg, ver) for pkg, ver in current.items()}
    stale_by_name = {_normalized_name(pkg): (pkg, ver) for pkg, ver in stale.items()}
    differences = []
    for name in sorted(set(current_by_name) | set(stale_by_name)):
        old = stale_by_name.get(name)
        new = current_by_name.get(name)
        if old != new:
            differences.append((old, new))
    assert differences, "Old and current indexes have no differences to compare."
    for old, new in differences:
        if old:
            assert _report_mentions(report, old[0], old[1]), (
                f"Report omits old index value {old[0]} {old[1]}."
            )
        if new:
            assert _report_mentions(report, new[0], new[1]), (
                f"Report omits current index value {new[0]} {new[1]}."
            )


def test_reward_check_04_no_shortcut():
    """Reject solutions that bypass regeneration, leave placeholder reports, or don't reflect real diff."""
    assert os.path.isfile(CURRENT_INDEX) and os.path.isfile(STALE_INDEX), (
        "Both index files must exist for no-shortcut check."
    )
    with open(CURRENT_INDEX, "r") as f:
        cur_lines = sorted([line.strip() for line in f if line.strip()])
    with open(STALE_INDEX, "r") as f:
        stale_lines = sorted([line.strip() for line in f if line.strip()])
    assert cur_lines, "Current index is empty."
    assert stale_lines, "Stale index is empty."
    # Indices must differ
    assert cur_lines != stale_lines, (
        "Current env_index.txt is identical to stale index; regeneration must have produced changes."
    )

    assert os.path.isfile(REBUILD_REPORT), f"Rebuild report not found: {REBUILD_REPORT}"
    with open(REBUILD_REPORT, "r") as f:
        report = f.read()
    assert report.strip(), "Rebuild report is empty."
    assert _report_mentions(report, "myutility_internal", "0.0.1"), (
        "Rebuild report does not contain the added package and version."
    )


def test_wheel_and_metadata():
    """Preserved check: exactly one valid wheel with correct metadata exists."""
    info = _get_wheel_info()
    assert info is not None, "No valid wheel found in ~/test/myutil."
    _, pkg_name, pkg_version = info
    assert pkg_name == "myutility_internal", (
        f"Wheel package name is '{pkg_name}', expected 'myutility_internal'"
    )
    assert re.match(r"^\d+\.\d+(\.\d+)?", pkg_version), (
        f"Wheel version '{pkg_version}' is not a semantic version"
    )
    assert os.path.isfile(WHEEL_METADATA), f"Wheel metadata summary missing: {WHEEL_METADATA}"
    with open(WHEEL_METADATA, "r") as f:
        summary = f.read()
    assert _report_mentions(summary, pkg_name, pkg_version), (
        "wheel_metadata.txt does not record the built wheel's package name and version."
    )
