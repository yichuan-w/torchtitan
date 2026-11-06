FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    openssh-client \
    python3 \
    python3-pip \
    curl \
    procps \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

# Create required directories
RUN mkdir -p /run/sshd /var/log /etc/ssh/sshd_config.d

# Copy configuration files
COPY files/sshd_config /etc/ssh/sshd_config
COPY files/security-hardening.conf /etc/ssh/sshd_config.d/security-hardening.conf
COPY files/dashboard.py /app/dashboard.py
COPY files/api_server.py /app/api_server.py
COPY files/metrics_server.py /app/metrics_server.py
COPY files/start-tunnel.sh /app/start-tunnel.sh
COPY files/tunnel-monitor.sh /app/tunnel-monitor.sh
COPY files/README.md /app/README.md
COPY files/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh /app/start-tunnel.sh /app/tunnel-monitor.sh

# Use entrypoint to set up services
CMD ["/app/entrypoint.sh"]


