FROM ubuntu:24.04

WORKDIR /app

# Install Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy data files and documentation
COPY files/data/ /app/data/
COPY files/README.txt /app/README.txt

# Create output directory
RUN mkdir -p /app/output

