FROM ubuntu:24.04

WORKDIR /app

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install mpmath for high-precision arithmetic
RUN pip3 install --no-cache-dir --break-system-packages mpmath==1.3.0

# Copy the constants module
COPY files/constants.py /app/

