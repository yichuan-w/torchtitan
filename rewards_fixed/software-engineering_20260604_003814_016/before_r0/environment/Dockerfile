FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libc6-dev binutils \
    && rm -rf /var/lib/apt/lists/*

# The VM binary
COPY files/vm /app/

# Example bytecode programs and expected outputs
COPY files/examples/ /app/examples/
COPY files/expected/ /app/expected/

