FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the legacy library source
COPY files/numutils-src/ /app/numutils-src/

# Copy the test program
COPY files/test_numutils.c /app/test_numutils.c

