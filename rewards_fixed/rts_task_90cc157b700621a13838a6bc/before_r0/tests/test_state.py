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


def _extract_report_sections(report_text):
    """
    Parse the rebuild report into a dict of section_name -> list of lines.
    Sections start with [something] (case-insensitive) and run until next section or end.
    """
    sections = {}
    current_section = None
    for line in report_text.splitlines():
        line = line.rstrip()
        header_match = re.match(r'^\s*\[([^\]]+)\]\s*$', line, re.IGNORECASE)
        if header_match:
            current_section = header_match.group(1).strip().lower()
            sections.setdefault(current_section, [])
            continue
        if current_section and line.strip():
            sections[current_section].append(line.strip())
    return sections


def _find_added_section_lines(report_text):
    """Return lines in a section whose header contains 'added' (case-insensitive)."""
    sections = _extract_report_sections(report_text)
    for sec_name, lines in sections.items():
        if "added" in sec_name:
            return lines
    return []


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
    assert len(current_lines) >= 2, (
        f"env_index.txt contains only {len(current_lines)} package(s); expected at least 2."
    )
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
    # Second package check: pip should be present in both freeze and env_index
    if "pip" in freeze:
        pip_entry = f"pip=={freeze['pip']}"
        assert pip_entry in current_lines, (
            f"env_index.txt missing pip entry {pip_entry}"
        )
    else:
        assert False, "pip not in venv freeze; cannot verify second package."


def test_reward_check_03_final_semantics():
    """Rebuild report identifies myutility_internal as added and accounts for differences."""
    assert os.path.isfile(REBUILD_REPORT), f"Rebuild report missing: {REBUILD_REPORT}"
    with open(REBUILD_REPORT, "r") as f:
        report = f.read()
    assert report.strip(), "Rebuild report is empty."

    added_lines = _find_added_section_lines(report)
    assert added_lines, "Rebuild report has no [Added] section lines."

    freeze = _get_venv_freeze()
    assert freeze and "myutility_internal" in freeze, "Cannot get venv freeze for myutility_internal."
    target_version = freeze["myutility_internal"]

    # Look for a line in added section that contains both package name and correct version
    found = False
    pkg_name = "myutility_internal"
    for line in added_lines:
        if pkg_name not in line:
            continue
        if target_version not in line:
            continue
        # line should not indicate a removal or change
        if any(kw in line.lower() for kw in ["removed", "changed", "moved"]):
            continue
        found = True
        break
    assert found, (
        f"Rebuild report's Added section must contain {pkg_name} with version {target_version}."
    )

    # If stale index has entries not in current freeze, they should appear as removed/changed.
    stale = _parse_stale_index(STALE_INDEX)
    if stale:
        for pkg, ver in stale.items():
            if pkg not in freeze:
                # Must be mentioned either in removed or version change (as stale) sections.
                sections = _extract_report_sections(report)
                mentioned = False
                for sec_name, lines in sections.items():
                    if "removed" in sec_name or "change" in sec_name:
                        for line in lines:
                            if pkg in line:
                                mentioned = True
                                break
                    if mentioned:
                        break
                if not mentioned:
                    # It's okay if removed package not explicitly listed? It should be accounted for.
                    # We'll allow if it's mentioned in any section, not necessarily with exact format.
                    # We'll just check that report mentions the package somewhere besides Added.
                    if pkg not in report:
                        assert False, (
                            f"Package {pkg}=={ver} from stale index disappeared, but report does not mention it."
                        )
            elif ver != freeze[pkg]:
                # version change must be mentioned in version changes section
                sections = _extract_report_sections(report)
                found_change = False
                for sec_name, lines in sections.items():
                    if "change" in sec_name:
                        for line in lines:
                            if pkg in line:
                                found_change = True
                                break
                if not found_change:
                    # alternative: might be described as stale=X, current=Y somewhere
                    if f"{pkg}=={ver}" not in report and f"{pkg}: stale=" not in report:
                        assert False, (
                            f"Package {pkg} has version change ({ver} -> {freeze[pkg]}), but report does not mention it."
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
    report_lower = report.lower()
    # Reject trivial placeholders
    forbidden = ["no changes", "(none)", "nothing changed", "empty", "n/a"]
    for phrase in forbidden:
        # Allow "(none)" only if it's not the only content and there are actual entries elsewhere
        # simple: if the report contains only (none) lines, fail.
        # We'll count the number of lines with package patterns versus placeholder lines.
        pass
    # Instead, verify that the report contains at least one explicit package==version line
    has_pkg_line = False
    for line in report.splitlines():
        if re.match(r"^[a-zA-Z0-9_.-]+==[a-zA-Z0-9_.+-]+$", line.strip()):
            has_pkg_line = True
            break
    assert has_pkg_line, (
        "Rebuild report does not contain any actual package==version entry; it appears to be a placeholder."
    )

    # Additionally, ensure report indicates an added package (myutility_internal is expected)
    assert "myutility_internal" in report, (
        "Rebuild report does not mention myutility_internal at all."
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
