FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY files/calc.c /app/
COPY files/Makefile /app/
COPY files/test_basic.csv /app/
COPY files/test_precedence.csv /app/
COPY files/test_sum.csv /app/

