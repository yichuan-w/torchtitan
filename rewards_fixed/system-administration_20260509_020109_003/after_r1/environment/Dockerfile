FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install logrotate, systemd, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    logrotate \
    systemd \
    cron \
    gzip \
    procps \
    ca-certificates \
    curl \
    build-essential \
    libpopt-dev \
    libacl1-dev \
    pkg-config \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fL --retry 3 -o /tmp/logrotate.tar.xz \
        https://github.com/logrotate/logrotate/releases/download/3.22.0/logrotate-3.22.0.tar.xz \
    && echo "42b4080ee99c9fb6a7d12d8e787637d057a635194e25971997eebbe8d5e57618  /tmp/logrotate.tar.xz" | sha256sum -c - \
    && tar -C /tmp -xf /tmp/logrotate.tar.xz \
    && cd /tmp/logrotate-3.22.0 \
    && ./configure --prefix=/usr \
    && make -j1 \
    && make install \
    && rm -rf /tmp/logrotate-3.22.0 /tmp/logrotate.tar.xz

# Create a dedicated log directory for the app
RUN mkdir -p /var/log/myapp && chmod 755 /var/log/myapp

WORKDIR /app

# Copy logrotate configuration (needs su directive fix)
COPY files/app.conf /etc/logrotate.d/myapp

# Copy and run setup script to create broken logrotate state
COPY files/setup_broken_state.sh /tmp/setup_broken_state.sh
RUN chmod +x /tmp/setup_broken_state.sh && /tmp/setup_broken_state.sh && rm /tmp/setup_broken_state.sh

