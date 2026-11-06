FROM ubuntu:24.04

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install polars with specific version
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir polars==1.28.1

# Create directory structure
RUN mkdir -p /app/data /app/output

# Copy data and pipeline
COPY files/data/transactions.csv /app/data/transactions.csv
COPY files/dedupe.py /app/dedupe.py

