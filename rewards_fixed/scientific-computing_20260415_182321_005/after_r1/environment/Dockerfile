FROM ubuntu:24.04

WORKDIR /app

# Install Python and essential scientific computing dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install scientific computing packages
# SciPy's solve_ivp behavior is part of the debugging scenario; keep it pinned.
RUN pip3 install --no-cache-dir --break-system-packages \
    numpy==2.3.2 \
    scipy==1.16.1

# Copy the kinetics simulation files
COPY files/kinetics/ /app/kinetics/
