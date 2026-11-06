FROM ubuntu:24.04

WORKDIR /app

# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    curl \
    ca-certificates \
    rsyslog \
    procps \
    findutils \
    tar \
    gzip \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Copy scripts to /app for reference
COPY files/backup.sh /app/
COPY files/cleanup.sh /app/
COPY files/healthcheck.sh /app/
COPY files/setup.sh /app/

# Run setup to create the (broken) initial state
RUN chmod +x /app/setup.sh && /app/setup.sh

# Create a marker that setup was run
RUN touch /var/lib/app-setup-complete

