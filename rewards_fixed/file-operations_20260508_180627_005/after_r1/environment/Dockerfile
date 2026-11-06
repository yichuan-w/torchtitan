FROM ubuntu:24.04

WORKDIR /app

# Install Python 3 (minimal - solver should install additional packages like ftfy, chardet)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Copy log generator and run it to create the test files
COPY files/generate_logs.py /tmp/generate_logs.py
RUN python3 /tmp/generate_logs.py && rm /tmp/generate_logs.py

