#!/bin/bash
set -u

mkdir -p /logs/verifier

# Check if we're in a valid working directory
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    echo 0 > /logs/verifier/reward.txt
    exit 1
fi

if [ -x /opt/verifier/bin/python ]; then
  PYTHON=/opt/verifier/bin/python
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Error: no Python interpreter found. Use the TB2 verifier base image."
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi

if ! "$PYTHON" -c "import pytest" >/dev/null 2>&1; then
  echo "Error: pytest is not installed for $PYTHON. Use the TB2 verifier base image."
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi

"$PYTHON" -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

