FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# The solver needs hex-analysis tools to examine the binary blob.
# Python is already in the base image for scripting.
RUN apt-get update && apt-get install -y --no-install-recommends \
    xxd \
    && rm -rf /var/lib/apt/lists/*

# Copy the corrupted binary blob
COPY files/data.blob /app/data.blob

