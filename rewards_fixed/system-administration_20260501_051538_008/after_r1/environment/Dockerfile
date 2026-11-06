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

RUN test "$(ssh -V 2>&1 | sed -n 's/^\(OpenSSH_[^ ]*\).*/\1/p')" = 'OpenSSH_9.6p1' \
    && test "$(dpkg-query -W -f='${Version}' openssh-server)" = '1:9.6p1-3ubuntu13.20'

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

# Set up services before running the harness command
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["tail", "-f", "/dev/null"]
