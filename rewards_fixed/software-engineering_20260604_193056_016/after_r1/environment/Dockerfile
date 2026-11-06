FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install gcc and libc headers for building the parser
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy source files
COPY files/parser.c /app/
COPY files/Makefile /app/
COPY files/format-spec.txt /app/

# Copy test data files
COPY files/testdata/ /app/testdata/
