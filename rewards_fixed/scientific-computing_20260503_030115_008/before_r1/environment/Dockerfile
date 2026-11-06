FROM ubuntu:24.04

WORKDIR /app

# Install Python and required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-numpy \
    python3-matplotlib \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir --ignore-installed numpy==1.26.4 scipy==1.13.1

# Copy the simulation code
COPY files/orbit_sim.py /app/orbit_sim.py
