#!/bin/bash
set -u

mkdir -p /logs/verifier

# Check if we're in a valid working directory
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi

# Use the system Python which has duckdb pre-installed
if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c "import duckdb" >/dev/null 2>&1; then
  PYTHON=/usr/bin/python3
elif [ -x /opt/verifier/bin/python ]; then
  PYTHON=/opt/verifier/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Error: no Python interpreter found."
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi

# Install pytest if not available
if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
  "$PYTHON" -m pip install --break-system-packages pytest==8.4.1 pytest-json-ctrf==0.3.5 2>/dev/null || {
    echo "Error: could not install pytest for $PYTHON"
    echo 0 > /logs/verifier/reward.txt
    exit 1
  }
fi

"$PYTHON" -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

