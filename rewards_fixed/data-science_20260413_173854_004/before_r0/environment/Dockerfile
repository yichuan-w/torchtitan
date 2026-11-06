FROM ubuntu:24.04

WORKDIR /app

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install pandas and pytz for data processing
RUN pip3 install --no-cache-dir --break-system-packages pandas pytz

# Create data and output directories
RUN mkdir -p /app/data /app/output

# Copy the sensor data
COPY files/data/sensor_readings.csv /app/data/

