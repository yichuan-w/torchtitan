FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install strace and build tools for compiling the processor binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    strace \
    && rm -rf /var/lib/apt/lists/*

# Copy and compile the processor program
COPY files/processor.c /app/src/processor.c
RUN mkdir -p /app/bin \
    && gcc -Wall -O2 -o /app/bin/processor /app/src/processor.c \
    && rm -rf /app/src \
    && chmod 755 /app/bin/processor

# Remove gcc so solver must use strace, not recompile
RUN apt-get remove -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

