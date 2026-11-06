FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install logrotate, systemd, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    logrotate \
    systemd \
    cron \
    gzip \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create a dedicated log directory for the app
RUN mkdir -p /var/log/myapp && chmod 755 /var/log/myapp

WORKDIR /app

# Copy logrotate configuration (needs su directive fix)
COPY files/app.conf /etc/logrotate.d/myapp

# Copy and run setup script to create broken logrotate state
COPY files/setup_broken_state.sh /tmp/setup_broken_state.sh
RUN chmod +x /tmp/setup_broken_state.sh && /tmp/setup_broken_state.sh && rm /tmp/setup_broken_state.sh


