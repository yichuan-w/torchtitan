FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssl \
    && rm -rf /var/lib/apt/lists/*

COPY files/repo.tar.gz /tmp/repo.tar.gz

RUN mkdir -p /app/repo && cd /app/repo && tar xzf /tmp/repo.tar.gz --strip-components=1 && \
    rm /tmp/repo.tar.gz

