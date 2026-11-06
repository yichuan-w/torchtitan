FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    flask==3.0.0 \
    requests==2.31.0 \
    pytest==8.4.1

# Copy application files
COPY files/config.py /app/config.py
COPY files/app.py /app/app.py

# Create a simple internal test server that would be vulnerable to SSRF
COPY files/internal_server.py /app/internal_server.py

