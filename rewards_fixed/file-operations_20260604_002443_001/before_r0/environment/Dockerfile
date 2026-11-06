FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Basic tools for binary inspection and scripting
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    xxd \
    && rm -rf /var/lib/apt/lists/*

# The corrupted custom archive and its format specification
COPY files/data.bin /app/data.bin
COPY files/README.txt /app/README.txt

