#!/bin/bash

# Check if we're in a valid working directory
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    exit 1
fi

ensure_uv() {
  if command -v uvx >/dev/null 2>&1; then
    return 0
  fi

  if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env"
  fi
  if command -v uvx >/dev/null 2>&1; then
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update
      apt-get install -y --no-install-recommends ca-certificates curl
    else
      echo "Error: uvx is not installed and curl is unavailable."
      return 1
    fi
  fi

  curl -LsSf https://astral.sh/uv/0.9.5/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
  export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
  command -v uvx >/dev/null 2>&1
}

if ! ensure_uv; then
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi

uvx \
  -p 3.13 \
  -w pytest==8.4.1 \
  -w pytest-json-ctrf==0.3.5 \
  pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA


if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi

