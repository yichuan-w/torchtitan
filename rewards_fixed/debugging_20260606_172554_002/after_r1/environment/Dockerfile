FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install build tools and debugging utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gdb \
    valgrind \
    binutils \
    && rm -rf /var/lib/apt/lists/*

# Copy all source files
COPY files/parser.c /app/
COPY files/parser.h /app/
COPY files/reader.c /app/
COPY files/Makefile /app/

# Pre-build the shared library with the bug
RUN gcc -Wall -Wextra -g -O2 -fPIC -shared -o /app/libparser.so /app/parser.c

# Remove the library source - solver must work with the compiled library
# The header (parser.h) is kept for the API contract
RUN rm -f /app/parser.c

# Copy test data files
COPY files/normal.dat /app/
COPY files/malformed.dat /app/

