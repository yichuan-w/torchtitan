#!/bin/bash

# Set torch to use minimal threads to avoid slowdowns
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

# Run pytest using the already installed packages from the venv
/opt/venv/bin/python -m pytest /tests/test_outputs.py -rA -v --tb=short

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

