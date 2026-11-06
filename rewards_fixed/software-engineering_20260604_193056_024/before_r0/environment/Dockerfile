FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Build tools for compiling C code
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy header, compressor stub, and pre-compiled decompressor object file
COPY files/compress.h /app/
COPY files/compress.c /app/
COPY files/decompress.o /app/

