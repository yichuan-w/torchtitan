FROM ubuntu:24.04

WORKDIR /app

# Install Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Copy the worker pool code
COPY files/worker_pool.py /app/worker_pool.py

