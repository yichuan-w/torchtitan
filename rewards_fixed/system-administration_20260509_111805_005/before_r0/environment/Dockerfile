FROM ubuntu:24.04

WORKDIR /app

# Install required packages for git HTTP server
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    nginx \
    fcgiwrap \
    apache2-utils \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy setup script and run it to create the broken environment
COPY files/setup-broken-env.sh /tmp/setup-broken-env.sh
RUN chmod +x /tmp/setup-broken-env.sh && /tmp/setup-broken-env.sh && rm /tmp/setup-broken-env.sh

# Create necessary directories
RUN mkdir -p /var/log/nginx /run

