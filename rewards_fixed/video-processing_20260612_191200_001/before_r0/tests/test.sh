#!/bin/bash
set -u

mkdir -p /logs/verifier

# Check if we're in a valid working directory
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi

# Use /usr/bin/python3 which has OpenCV/numpy installed
PYTHON=/usr/bin/python3
if [ ! -x "$PYTHON" ]; then
  echo "Error: /usr/bin/python3 not found."
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi

# Ensure pytest and ctrf plugin are available
if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
  pip3 install --break-system-packages pytest pytest-json-ctrf >/dev/null 2>&1 || true
fi

"$PYTHON" -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

