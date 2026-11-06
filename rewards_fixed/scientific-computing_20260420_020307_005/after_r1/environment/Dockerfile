FROM ubuntu:24.04

WORKDIR /app

# Install Python and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-numpy \
    python3-pandas \
    python3-scipy \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir scipy==1.11.4

# Copy calibration pipeline
COPY files/calibration/ /app/calibration/
