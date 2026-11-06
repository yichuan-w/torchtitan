FROM python:3.13-slim-bookworm

WORKDIR /app

# Install system dependencies for memory profiling
RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy the API framework and server files
COPY files/api_framework.py /app/
COPY files/server.py /app/
COPY files/README.md /app/

