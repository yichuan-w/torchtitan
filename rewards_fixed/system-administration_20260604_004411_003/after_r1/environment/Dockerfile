FROM aweaiteam/calibforge:tb2-verifier-base-v1@sha256:0f61b2776c40231d5d868278ed6042295ddbc9d24b036cd72f0b88ebe9bd621a

WORKDIR /app

# Install base packages - rsyslog (without gnutls TLS support - solver must install that),
# nginx for serving the report, openssl for certificate inspection
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    rsyslog \
    nginx \
    openssl \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Prepare certificate inputs outside the rsyslog TLS configuration exercise.
# The rsyslog process is confined to its configuration tree in this image.
COPY files/prepared-certs/ /etc/rsyslog.d/certs/
COPY files/prepared-certs/ /etc/rsyslog/certs/

# Create an empty remote.log file for rsyslog to write received messages
RUN touch /var/log/remote.log

# Copy nginx config that sets up port 8080
COPY files/nginx-default.conf /etc/nginx/sites-available/default

# Disable nginx default server (it listens on port 80 which we don't need)
# and keep only our config

# Ensure /app is writable
RUN chmod 777 /app
