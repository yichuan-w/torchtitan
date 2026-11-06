#!/bin/bash
set -u

mkdir -p /logs/verifier

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

# Install test dependencies (ensure pip is available)
"$PYTHON" -m ensurepip 2>/dev/null || true
"$PYTHON" -m pip install --quiet lightgbm pandas scikit-learn 2>/dev/null || \
  pip3 install --quiet lightgbm pandas scikit-learn 2>/dev/null || \
  pip install --quiet lightgbm pandas scikit-learn 2>/dev/null || true

"$PYTHON" -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

