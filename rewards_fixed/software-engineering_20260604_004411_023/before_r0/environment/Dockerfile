FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install build tools for C compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    make \
    libc6-dev \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy task files
COPY files/lz4_decompress.h /app/
COPY files/lz4_decompress.c /app/
COPY files/Makefile /app/
COPY files/test_runner.c /app/

