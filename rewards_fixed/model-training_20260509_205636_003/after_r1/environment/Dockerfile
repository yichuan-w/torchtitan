FROM ubuntu:24.04

WORKDIR /app

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install PyTorch (CPU version) + pytest
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir pytest

# Copy model definition files
COPY files/old_model.py /app/
COPY files/new_model.py /app/
COPY files/generate_data.py /app/

# Generate the checkpoint and dataset during build
RUN python /app/generate_data.py

