FROM ubuntu:24.04

WORKDIR /app

# Install system dependencies and Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-numpy \
    python3-scipy \
    && rm -rf /var/lib/apt/lists/*

# Copy the data and analysis script
COPY files/data/ /app/data/
COPY files/spectral_analyzer.py /app/spectral_analyzer.py

