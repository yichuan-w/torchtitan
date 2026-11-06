FROM ubuntu:24.04

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    wget \
    pkg-config \
    file \
    && rm -rf /var/lib/apt/lists/*

