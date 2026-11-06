FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install build tools and debugging utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdb \
    valgrind \
    && rm -rf /var/lib/apt/lists/*

# Copy project source files and Makefile
COPY files/Makefile /app/Makefile
COPY files/gen_config.sh /app/gen_config.sh
COPY files/kvdb.h /app/kvdb.h
COPY files/kvdb.c /app/kvdb.c
COPY files/main.c /app/main.c

