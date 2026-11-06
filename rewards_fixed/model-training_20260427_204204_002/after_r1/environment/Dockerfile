FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install numpy first (compatible version)
RUN pip install --no-cache-dir numpy==1.26.4

# Install PyTorch (CPU version)
RUN pip install --no-cache-dir \
    torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu

# Copy the model and training script
COPY files/model.py /app/model.py
COPY files/train.py /app/train.py

