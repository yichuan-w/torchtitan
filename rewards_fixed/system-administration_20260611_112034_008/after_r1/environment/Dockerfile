FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install build dependencies for sysinfod
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    make \
    libc6-dev \
    libcurl4-openssl-dev \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy the sysinfod source code
COPY files/sysinfod/ /app/sysinfod/

