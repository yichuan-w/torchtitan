FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment and install pandas
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir pandas==2.2.0

WORKDIR /app

# Copy data files and pipeline
COPY files/data/sales.csv /app/data/sales.csv
COPY files/pipeline.py /app/pipeline.py

