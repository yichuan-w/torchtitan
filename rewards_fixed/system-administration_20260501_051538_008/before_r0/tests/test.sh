#!/bin/bash

# Install curl
apt-get update
apt-get install -y curl

# Install uv with retry
for i in 1 2 3; do
    if curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh; then
        break
    fi
    echo "Retry $i failed, waiting..."
    sleep 5
done

source $HOME/.local/bin/env 2>/dev/null || true

# Check if we're in a valid working directory
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    exit 1
fi

# Try uvx first, fall back to pip
if command -v uvx &> /dev/null; then
    uvx \
      -p 3.13 \
      -w pytest==8.4.1 \
      -w pytest-json-ctrf==0.3.5 \
      pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
else
    pip3 install pytest pytest-json-ctrf --break-system-packages -q
    python3 -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA
fi


if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

