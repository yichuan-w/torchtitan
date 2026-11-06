FROM ubuntu:24.04

WORKDIR /app

# Install DuckDB CLI and Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install DuckDB CLI
RUN wget -q https://github.com/duckdb/duckdb/releases/download/v1.1.3/duckdb_cli-linux-amd64.zip \
    && unzip duckdb_cli-linux-amd64.zip \
    && mv duckdb /usr/local/bin/ \
    && rm duckdb_cli-linux-amd64.zip

# Install DuckDB Python library
RUN pip3 install --no-cache-dir --break-system-packages duckdb==1.1.3

# Create data and output directories
RUN mkdir -p /app/data /app/output

# Copy data files
COPY files/pageviews.csv /app/data/
COPY files/conversions.csv /app/data/

