FROM ubuntu:24.04

WORKDIR /app

# Install Python and pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Install pinned data-processing dependencies
RUN pip3 install --no-cache-dir --break-system-packages pandas==2.2.3 pytz==2022.7.1

# Configure the application with its deployed timezone database
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSLo /tmp/tzdata.tar.gz https://data.iana.org/time-zones/releases/tzdata2022g.tar.gz \
    && echo "4491db8281ae94a84d939e427bdd83dc389f26764d27d9a5c52d782c16764478  /tmp/tzdata.tar.gz" | sha256sum -c - \
    && mkdir -p /app/tzdata \
    && tar -xzf /tmp/tzdata.tar.gz -C /tmp \
    && zic -d /app/tzdata /tmp/africa /tmp/etcetera \
    && rm -f /tmp/tzdata.tar.gz /tmp/africa /tmp/etcetera \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONTZPATH=/app/tzdata
ENV TZDIR=/app/tzdata

# Create data and output directories
RUN mkdir -p /app/data /app/output

# Copy the sensor data
COPY files/data/sensor_readings.csv /app/data/
