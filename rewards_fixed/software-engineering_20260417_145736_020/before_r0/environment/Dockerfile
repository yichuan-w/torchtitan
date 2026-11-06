FROM ubuntu:24.04

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment for build tools with setuptools>=69
RUN python3 -m venv /opt/build-venv && \
    /opt/build-venv/bin/pip install --no-cache-dir --upgrade pip setuptools>=69 wheel build

# Make build tools available
ENV PATH="/opt/build-venv/bin:$PATH"

# Copy the datatools package
COPY files/datatools /app/datatools



