FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    gcc-14 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fL https://github.com/rui314/mold/releases/download/v2.34.1/mold-2.34.1-x86_64-linux.tar.gz -o /tmp/mold.tar.gz \
    && echo "dafd02fe19c51818aa22c9d569393616b1a85a2220d6fb32ba3728d351074e59  /tmp/mold.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/mold.tar.gz -C /tmp \
    && cp /tmp/mold-2.34.1-x86_64-linux/bin/mold /usr/local/bin/mold \
    && ln -s mold /usr/local/bin/ld.mold \
    && rm -rf /tmp/mold.tar.gz /tmp/mold-2.34.1-x86_64-linux

WORKDIR /app

# Copy the legacy library source
COPY files/numutils-src/ /app/numutils-src/

RUN sed -i 's/^CC = gcc$/CC = gcc-14 -fuse-ld=mold/' /app/numutils-src/Makefile

# Copy the test program
COPY files/test_numutils.c /app/test_numutils.c
