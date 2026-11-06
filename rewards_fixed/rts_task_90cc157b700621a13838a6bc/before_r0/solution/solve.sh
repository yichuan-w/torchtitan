#!/bin/bash
set -euo pipefail

# --- Stage: inspect (original source) ---
echo "Inspecting source directory..."
if [ ! -d ~/pkg_src ]; then
    echo "ERROR: ~/pkg_src does not exist."
    exit 1
fi
if [ ! -f ~/pkg_src/setup.py ] && [ ! -f ~/pkg_src/pyproject.toml ]; then
    echo "ERROR: No setup.py or pyproject.toml found in ~/pkg_src."
    exit 1
fi

# --- Stage: inspect (stale index) ---
echo "Inspecting stale environment index..."
if [ ! -f ~/env_index_previous.txt ]; then
    echo "ERROR: ~/env_index_previous.txt does not exist. A stale environment index is required."
    exit 1
fi
echo "Stale index present, will use for comparison."

# --- Stage: derive ---
echo "Deriving build command..."
BUILD_CMD=("python" "-m" "build" "--wheel" "--outdir" "/tmp/wheels")
if ! command -v python > /dev/null; then
    echo "ERROR: python not found."
    exit 1
fi
python -c "import build" 2>/dev/null || {
    echo "build module not installed; falling back to setup.py bdist_wheel"
    BUILD_CMD=("python" "setup.py" "bdist_wheel" "--dist-dir" "/tmp/wheels")
}

# --- Stage: execute (build and copy) ---
echo "Building wheel from source..."
cd ~/pkg_src
mkdir -p /tmp/wheels
"${BUILD_CMD[@]}"
cd - >/dev/null

echo "Copying wheel to ~/test/myutil/ ..."
mkdir -p ~/test/myutil
cp /tmp/wheels/*.whl ~/test/myutil/

# --- Stage: validate intermediate artifact ---
echo "Validating intermediate wheel..."
WHEEL_COUNT=$(ls -1 ~/test/myutil/*.whl 2>/dev/null | wc -l)
if [ "$WHEEL_COUNT" -ne 1 ]; then
    echo "ERROR: Expected exactly one .whl file in ~/test/myutil/, found $WHEEL_COUNT."
    exit 1
fi
WHEEL_FILE=$(ls ~/test/myutil/*.whl)
if [ ! -s "$WHEEL_FILE" ]; then
    echo "ERROR: Wheel file is empty."
    exit 1
fi
echo "Wheel file: $WHEEL_FILE"

if command -v unzip >/dev/null; then
    PACKAGE_NAME_META=$(unzip -p "$WHEEL_FILE" '*.dist-info/METADATA' 2>/dev/null | grep '^Name:' | head -1 | cut -d' ' -f2 | xargs)
    VERSION_META=$(unzip -p "$WHEEL_FILE" '*.dist-info/METADATA' 2>/dev/null | grep '^Version:' | head -1 | cut -d' ' -f2 | xargs)
    echo "Metadata: Name=$PACKAGE_NAME_META Version=$VERSION_META"
    if [ -z "$PACKAGE_NAME_META" ]; then
        echo "WARNING: Could not extract package name from wheel metadata."
    fi
else
    echo "unzip not available; skipping metadata verification."
fi

# --- Stage: start local PyPI server ---
echo "Installing pypiserver..."
pip install 'pypiserver>=2.0'

echo "Starting local PyPI server..."
pkill -f "pypiserver run" || true
sleep 1
nohup python -m pypiserver run -p 8080 ~/test/myutil/ &>/dev/null &
disown
sleep 2

# --- Stage: execute (venv, install, capture version, current index) ---
echo "Creating virtual environment..."
rm -rf ~/python_venv/myproject
python3 -m venv ~/python_venv/myproject
source ~/python_venv/myproject/bin/activate

echo "Installing myutility_internal from local PyPI..."
pip install --index-url http://localhost:8080/simple/ myutility_internal

echo "Capturing package version..."
python3 -c "import myutility_internal; print(myutility_internal.__version__)" > /app/result.txt
VERSION=$(cat /app/result.txt)
echo "Version detected: ${VERSION}"

echo "Generating current environment index file..."
pip freeze --all > ~/env_index.txt
LINE_COUNT=$(wc -l < ~/env_index.txt)
echo "Current index written with ${LINE_COUNT} packages."

# --- Stage: regenerate rebuild report from stale and current ---
echo "Generating rebuild report by comparing stale and current indexes..."

# Sort both files for comparison
sort ~/env_index_previous.txt -o /tmp/stale_sorted.txt
sort ~/env_index.txt -o /tmp/current_sorted.txt

# Added: lines only in current
comm -13 /tmp/stale_sorted.txt /tmp/current_sorted.txt > /tmp/added.txt

# Removed: lines only in stale
comm -23 /tmp/stale_sorted.txt /tmp/current_sorted.txt > /tmp/removed.txt

# Version changes: same package, different version
awk -F'==' '
    NR==FNR { stale[$1] = $2; next }
    {
        if ($1 in stale) {
            if (stale[$1] != $2) {
                printf("%s: stale=%s, current=%s\n", $1, stale[$1], $2)
            }
        }
    }
' /tmp/stale_sorted.txt /tmp/current_sorted.txt > /tmp/version_changes.txt

# Compose human-readable rebuild report
{
    echo "Rebuild Report"
    echo "=============="
    echo ""
    echo "Comparing stale index (~/env_index_previous.txt) with current index (~/env_index.txt)."
    echo ""
    echo "[Added]"
    if [ -s /tmp/added.txt ]; then
        cat /tmp/added.txt
    else
        echo "(none)"
    fi
    echo ""
    echo "[Removed]"
    if [ -s /tmp/removed.txt ]; then
        cat /tmp/removed.txt
    else
        echo "(none)"
    fi
    echo ""
    echo "[Version Changes]"
    if [ -s /tmp/version_changes.txt ]; then
        cat /tmp/version_changes.txt
    else
        echo "(none)"
    fi
    echo ""
    echo "Report generated on $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
} > ~/rebuild_report.txt

echo "Rebuild report created."

# --- Stage: validate rebuild report ---
if [ ! -s ~/rebuild_report.txt ]; then
    echo "ERROR: rebuild_report.txt was not created or is empty."
    exit 1
fi

# Validate that the rebuild report references entries from the stale index
if ! grep -qF "$(head -1 /tmp/stale_sorted.txt)" ~/rebuild_report.txt ; then
    echo "WARNING: rebuild_report.txt does not appear to reference the stale index contents."
fi

# --- Stage: validate final (original checks) ---
PACKAGE_NAME="myutility_internal"
REQUIRED_LINE="${PACKAGE_NAME}==${VERSION}"

if ! grep -q "^${REQUIRED_LINE}$" ~/env_index.txt; then
    echo "ERROR: env_index.txt does not contain the expected line '${REQUIRED_LINE}'."
    exit 1
fi

if [ "${LINE_COUNT}" -lt 2 ]; then
    echo "ERROR: env_index.txt must list multiple packages, but found only ${LINE_COUNT}."
    exit 1
fi

# --- Stage: finalize ---
if [ ! -s /app/result.txt ]; then
    echo "ERROR: /app/result.txt is missing or empty."
    exit 1
fi

if [ ! -s ~/env_index.txt ]; then
    echo "ERROR: ~/env_index.txt is missing or empty."
    exit 1
fi

if [ ! -s ~/rebuild_report.txt ]; then
    echo "ERROR: ~/rebuild_report.txt is missing or empty."
    exit 1
fi

echo "All artifacts created and validated successfully."